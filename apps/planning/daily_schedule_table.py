"""Atomic edits and additions from the daily SMG matrix."""
import hashlib
import json
from decimal import Decimal,InvalidOperation
from types import SimpleNamespace
from django.core.exceptions import ValidationError,PermissionDenied
from django.db import transaction
from core.permissions import require_roles,PLAN_ROLES
from .models import DailySchedule,DailyScheduleEntry,ResourceMonthAllocation
from . import daily_schedule as service


def locked(user,schedule):
    require_roles(user,PLAN_ROLES | {'MANAGER'})
    frozen=getattr(schedule,'_smg_frozen_specs',None)
    schedule=DailySchedule.objects.select_for_update().select_related('source').defer('source__snapshot').get(pk=schedule.pk,company=user.company)
    if frozen and frozen[0]==(schedule.source_id,schedule.month):schedule._smg_frozen_specs=frozen
    from core.permissions import require_planning_object
    require_planning_object(user,schedule.construction_object_id)
    if schedule.status!='DRAFT':raise PermissionDenied('СМГ зафиксирован. Создайте новую редакцию.')
    return schedule


@transaction.atomic
def save_table(user,schedule,changes):
    schedule=locked(user,schedule)
    if not isinstance(changes,list) or not changes:raise ValidationError('Нет изменений для сохранения.')
    seen=set();crew_edits={}
    try:
        for change in changes:
            pk=int(change['id'])
            if pk in seen:raise ValidationError('Строка указана повторно.')
            seen.add(pk)
            entry=DailyScheduleEntry.objects.select_for_update().filter(pk=pk,schedule=schedule).first()
            if entry is None:raise ValidationError('Строка не относится к этому СМГ.')
            entry.schedule=schedule
            stored={(r['date'],r.get('item_id')):r for r in entry.rows}
            fields={name for name,_,_ in service.FIELDS[entry.kind]}
            values=[{field:Decimal(str(service.row_value(entry.kind,stored.get((day.isoformat(),item['id']),{}),field))) for field in fields} for day,item in service.identities(entry)]
            index={(day.isoformat(),item['id']):i for i,(day,item) in enumerate(service.identities(entry))}
            edited=set()
            for cell in change['changes']:
                key=(cell['date'],cell.get('item_id'));field=cell['field']
                if key not in index or field not in fields or (key,field) in edited:raise ValidationError('Недопустимая ячейка СМГ.')
                edited.add((key,field));values[index[key]][field]=Decimal(str(cell['value']).replace(',','.'))
            if entry.kind=='labor':
                crew_edits[entry.pk]={c['date']:c['value'] for c in change['changes'] if c['field']=='planned_count'}
            service.save_entry(user,entry,values,int(change['revision']),sync_crew=False)
    except (KeyError,TypeError,ValueError,InvalidOperation):raise ValidationError('Проверьте введенные числа и обновите страницу.')
    from .table_resource_crew import sync_daily
    sync_daily(user,schedule,edited_counts=crew_edits)
    return len(seen)


@transaction.atomic
def add_entry(user,schedule,data):
    schedule=locked(user,schedule);kind=data['kind']
    if kind=='work' and data.get('selection'):
        return add_work_branch(user,schedule,data['selection'])
    from .workspace_services import specification
    from .estimate_catalog import alternatives,local_work
    metadata={};original=[]
    if kind=='work':
        require_roles(user,PLAN_ROLES)
        work=data['work']
        from apps.works.planning_access import planning_works
        if not planning_works(alternatives(schedule),user,whole=True).filter(pk=work.pk).exists():raise ValidationError('Работа не принадлежит объекту или подключенной истории.')
        work=local_work(user,schedule,work) if schedule.source_id else work
        metadata=specification(work);metadata['history']=[];metadata['original_daily']=[]
        label=work.name;key='work:'+str(work.pk)
    else:
        from .preparation import require_resource_input
        require_resource_input(user,schedule)
        row={'origin':'PLAN','comment':''}
        if kind=='labor':
            brigade=data['brigade']
            if brigade.company_id!=user.company_id:raise PermissionDenied
            label=str(brigade);row.update(brigade_id=brigade.pk,planned_count='0',planned_hours='0',hourly_rate=str(data['rate']))
        elif kind=='equipment':
            equipment=data['equipment']
            if equipment.company_id!=user.company_id:raise PermissionDenied
            if equipment.parent_id:raise ValidationError('В СМГ выберите вид техники, как в ГПР, без модели и номера машины.')
            label=str(equipment);row.update(equipment_type_id=equipment.pk,equipment_number='',planned_count='0',planned_machine_hours='0',hourly_rate=str(data['rate']))
        elif kind=='fuel':
            choices=dict(ResourceMonthAllocation._meta.get_field('fuel_type').choices)
            if data['fuel'] not in choices:raise ValidationError('Выберите вид ГСМ.')
            label=choices[data['fuel']];row.update(fuel_type=data['fuel'],equipment_ref='',planned_liters='0',planned_balance='0',price_per_liter=str(data['rate']))
        else:raise ValidationError('Неизвестный вид строки.')
        row['label']=label
        identity=service.resource_identity(row,kind)
        key=kind+':'+hashlib.sha256(json.dumps(identity).encode()).hexdigest()
        original=[dict(row,date=day.isoformat()) for day,_ in service.identities(SimpleNamespace(schedule=schedule,kind=kind,label=label,metadata={}))]
    existing=schedule.entries.filter(key=key).first()
    if existing:
        if kind=='equipment':
            from .table_resource_crew import add_daily_crew
            if add_daily_crew(user,schedule,data['equipment']):return existing
        raise ValidationError('Эта позиция уже есть в СМГ. Измените ее дневные значения.')
    if kind=='equipment' and not data.get('add_crew',True):metadata={**metadata,'table_crew':[]}
    entry=DailyScheduleEntry.objects.create(schedule=schedule,key=key,kind=kind,label=label,metadata=metadata,original=original,rows=original)
    if kind=='work':
        from .subobject_smg import annotate_entry
        annotate_entry(entry,work);entry.save(update_fields=['metadata','gpr_work'])
    values=[{field:Decimal(0) for field,_,_ in service.FIELDS[kind]} for _ in service.identities(entry)]
    entry=service.save_entry(user,entry,values,0,sync_crew=False)
    if kind=='equipment':
        from .table_resource_crew import add_daily_crew
        add_daily_crew(user,schedule,data['equipment'])
    return entry


@transaction.atomic
def add_work_branch(user,schedule,selection,*,selected_items=None):
    from .estimate_catalog import alternatives,local_work
    from .workspace_services import specification
    from .work_selection import resolve
    from .models import DailyScheduleEntry
    from copy import deepcopy
    require_roles(user,PLAN_ROLES)
    schedule=locked(user,schedule)
    result=[]
    for work,selected in resolve(alternatives(schedule),selection,user=user):
        if selected_items is not None:
            if not set(selected_items)<=set(work.items.values_list('pk',flat=True)):raise ValidationError('Состав работы общего ГПР изменён. Проверьте справочник.')
            selected=list(selected_items)
            from apps.works.planning_access import planning_selection
            permitted=planning_selection(work,selected,user)
            if permitted!=selected:raise ValidationError('Выберите доступные работы комплекса по отдельности.')
        original_work=work
        work=local_work(user,schedule,work) if schedule.source_id else work
        if selected and work.pk!=original_work.pk:
            explicit=getattr(work,'_smg_gpr_origin',None)
            mapped=[]
            for item in original_work.items.filter(pk__in=selected):
                if explicit and str(item.pk) in explicit['items']:
                    mapped.append(explicit['items'][str(item.pk)]);continue
                targets=list(work.items.filter(name=item.name,unit=item.unit,quantity_per_unit=item.quantity_per_unit))
                if len(targets)!=1:raise ValidationError('Не удалось однозначно сопоставить выбранные работы истории.')
                mapped.append(targets[0].pk)
            selected=mapped
        key='work:'+str(work.pk)
        entry=schedule.entries.select_for_update().filter(key=key).first()
        if entry:
            old_ids={item['id'] for item in entry.metadata.get('items',[])}
            wanted=set(selected) if selected else set(work.items.values_list('pk',flat=True))
            if work.kind!='COMPOSITE' or wanted<=old_ids:result.append(entry);continue
            selected=sorted(old_ids|wanted)
        metadata=specification(work,selected)
        if entry:
            for field in ['history','original_daily','revenue_prices','gpr_item_mapping']:
                if field in entry.metadata:metadata[field]=deepcopy(entry.metadata[field])
            entry.metadata=metadata;entry.save(update_fields=['metadata'])
        else:
            metadata.update(history=[],original_daily=[])
            entry=DailyScheduleEntry.objects.create(schedule=schedule,key=key,kind='work',label=work.name,metadata=metadata,original=[],rows=[])
        from .subobject_smg import annotate_entry
        annotate_entry(entry,work);entry.save(update_fields=['metadata','gpr_work'])
        stored={(row['date'],row.get('item_id')):row for row in entry.rows}
        values=[{'quantity':Decimal(str(stored.get((day.isoformat(),item['id']),{}).get('quantity',0)))} for day,item in service.identities(entry)]
        result.append(service.save_entry(user,entry,values,entry.revision))
    return result
