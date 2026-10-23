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
    schedule=DailySchedule.objects.select_for_update().select_related('source').defer('source__snapshot').get(pk=schedule.pk,company=user.company)
    from core.permissions import require_planning_object
    require_planning_object(user,schedule.construction_object_id)
    if schedule.status!='DRAFT':raise PermissionDenied('СМГ зафиксирован. Создайте новую редакцию.')
    return schedule


@transaction.atomic
def save_table(user,schedule,changes):
    schedule=locked(user,schedule)
    if not isinstance(changes,list) or not changes:raise ValidationError('Нет изменений для сохранения.')
    seen=set()
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
            service.save_entry(user,entry,values,int(change['revision']))
    except (KeyError,TypeError,ValueError,InvalidOperation):raise ValidationError('Проверьте введенные числа и обновите страницу.')
    return len(seen)


@transaction.atomic
def add_entry(user,schedule,data):
    schedule=locked(user,schedule);kind=data['kind']
    from .workspace_services import specification
    from .estimate_catalog import alternatives,local_work
    metadata={};original=[]
    if kind=='work':
        require_roles(user,PLAN_ROLES)
        work=data['work']
        if not alternatives(schedule).filter(pk=work.pk).exists():raise ValidationError('Работа не принадлежит объекту или подключенной истории.')
        work=local_work(user,schedule.source,work) if schedule.source_id else work
        metadata=specification(work);metadata['history']=[];metadata['original_daily']=[]
        label=work.name;key='work:'+str(work.pk)
    else:
        from .preparation import require_resource_editor
        require_resource_editor(user,schedule)
        row={'origin':'PLAN','comment':''}
        if kind=='labor':
            brigade=data['brigade']
            if brigade.company_id!=user.company_id:raise PermissionDenied
            label=str(brigade);row.update(brigade_id=brigade.pk,planned_count='0',planned_hours='0',hourly_rate=str(data['rate']))
        elif kind=='equipment':
            equipment=data['equipment']
            if equipment.company_id!=user.company_id:raise PermissionDenied
            label=str(equipment);row.update(equipment_type_id=equipment.pk,equipment_number=data['reference'],planned_count='0',planned_machine_hours='0',hourly_rate=str(data['rate']))
        elif kind=='fuel':
            choices=dict(ResourceMonthAllocation._meta.get_field('fuel_type').choices)
            if data['fuel'] not in choices:raise ValidationError('Выберите вид ГСМ.')
            label=choices[data['fuel']];row.update(fuel_type=data['fuel'],equipment_ref=data['reference'],planned_liters='0',planned_balance='0',price_per_liter=str(data['rate']))
        else:raise ValidationError('Неизвестный вид строки.')
        row['label']=label
        identity=service.resource_identity(row,kind)
        key=kind+':'+hashlib.sha256(json.dumps(identity).encode()).hexdigest()
        original=[dict(row,date=day.isoformat()) for day,_ in service.identities(SimpleNamespace(schedule=schedule,kind=kind,label=label,metadata={}))]
    if schedule.entries.filter(key=key).exists():raise ValidationError('Эта позиция уже есть в СМГ. Измените ее дневные значения.')
    entry=DailyScheduleEntry.objects.create(schedule=schedule,key=key,kind=kind,label=label,metadata=metadata,original=original,rows=original)
    values=[{field:Decimal(0) for field,_,_ in service.FIELDS[kind]} for _ in service.identities(entry)]
    return service.save_entry(user,entry,values,0)
