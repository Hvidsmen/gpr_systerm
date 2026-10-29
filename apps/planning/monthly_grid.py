"""Month columns over the existing GPR allocations; no second planning store."""
import json
from copy import deepcopy
from collections import defaultdict
from decimal import Decimal
from hashlib import sha256
from django.core.exceptions import ValidationError,PermissionDenied
from django.db import transaction
from core.sqlite_busy import retry_sqlite_busy
from core.permissions import role_code,require_planning_object
from .models import GlobalPlanVersion,WorkMonthAllocation,ResourceMonthAllocation
from .workspace_forms import WorkAllocationForm,ResourceAllocationForm
from .bulk_add import editable_months

ZERO=Decimal(0)


def identity(row):
    values=['work',row.work_id] if isinstance(row,WorkMonthAllocation) else [row.kind,row.brigade_id,row.equipment_type_id,row.equipment_number,row.fuel_type,row.equipment_ref]
    return sha256(json.dumps(values).encode()).hexdigest()


def permissions(user,version):
    mutable=version.status in {'DRAFT','REJECTED'} and not version.workspace.is_archived
    works=mutable and role_code(user) in {'ADMIN','PLANNER'}
    resources=False
    if mutable:
        from .preparation import require_resource_input
        try:require_resource_input(user,version);resources=True
        except (PermissionDenied,ValidationError):pass
    return works,resources


def allocations(version):
    return (list(version.work_allocations.select_related('work__section','work__complex_object','load_profile').prefetch_related('work__items')),
        list(version.resource_allocations.select_related('brigade__group','equipment_type__category','equipment_type__parent__category')))


def item_values(row):
    return {str(item.pk):Decimal(str(row.item_quantities.get(str(item.pk),(row.quantity*item.quantity_per_unit).quantize(Decimal('.000001'))))) for item in row.work.items.all() if not row.selected_item_ids or item.pk in row.selected_item_ids}


def matrix(user,version):
    from apps.resources.group_order import order_key
    labor_group_order=order_key(version.company)
    equipment_group_order=order_key(version.company,'equipment')
    from .resource_group_totals import equipment_group
    months=editable_months(version);can_work,can_resource=permissions(user,version)
    works,resources=allocations(version);buckets={};sections=[]
    for row in works+resources:
        key=identity(row);bucket=buckets.setdefault(key,{'source':row,'months':{}});bucket['months'][row.month]=row
    groups=defaultdict(lambda:defaultdict(list))
    for key,bucket in buckets.items():
        source=bucket['source'];iswork=isinstance(source,WorkMonthAllocation);kind='work' if iswork else source.kind
        cells_by_month=bucket['months'];selected=set();full=False
        if iswork:
            work=source.work
            for allocation in cells_by_month.values():
                if not allocation.selected_item_ids:full=True
                selected.update(allocation.selected_item_ids)
            items=list(work.items.all());included=[i for i in items if full or i.pk in selected]
            fields=[('quantity','Объём · '+work.unit,None)] if work.kind!='COMPOSITE' else [('ready','Готовые комплексы · '+work.unit,None)]+[('item:'+str(i.pk),i.name+' · '+i.unit,i) for i in included]
            group=' → '.join([work.section.name,work.complex_object.name if work.complex_object_id else 'Самостоятельные работы'])
            label=work.name
        else:
            group=source.brigade.group.name if kind=='labor' and source.brigade.group_id else 'Без группы' if kind=='labor' else equipment_group(source.equipment_type,version.company)[0] if kind=='equipment' else 'Общие ресурсы объекта'
            label=str(source.brigade if kind=='labor' else source.equipment_type if kind=='equipment' else source.get_fuel_type_display())
            if source.equipment_number:label+=' · '+source.equipment_number
            fields=[('count','Средняя численность · чел.' if kind=='labor' else 'Среднее количество · ед.',None)] if kind!='fuel' else [('liters','Расход · л',None),('balance','Остаток · л',None)]
        entry={'norms':json.dumps({str(i.pk):str(i.quantity_per_unit) for i in items}) if iswork and work.kind=='COMPOSITE' else '{}','fractional':bool(iswork and work.allow_fractional),'key':key,'source':source.pk,'kind':'work' if iswork else 'resource','label':label,'measures':[]}
        if iswork:
            from types import SimpleNamespace
            from .plan_workbench import locate_work
            located=SimpleNamespace(metadata={'id':work.pk,'kind':work.kind},label=label)
            locate_work(located,work)
            entry.update(vars(located))
        for field,caption,item in fields:
            measure={'field':field,'caption':caption,'item_id':item.pk if item else '', 'norm':item.quantity_per_unit if item else '', 'cells':[],'step':'0.000001' if item else '1' if field=='count' else '0.001' if iswork else '0.01'}
            for month in months:
                row=cells_by_month.get(month);allowed=(can_work if iswork else can_resource) and field!='ready'
                if item and row and row.selected_item_ids and item.pk not in row.selected_item_ids:allowed=False;value=None
                elif row:value=item_values(row).get(str(item.pk),ZERO) if item else row.quantity if iswork else getattr(row,field)
                else:value=None
                measure['cells'].append({'month':month,'value':value,'id':row.pk if row else '', 'revision':row.updated_at.isoformat() if row else 'missing','editable':allowed})
            entry['measures'].append(measure)
        groups[kind][group].append(entry)
    for kind,title in [('work','Работы'),('labor','Должности'),('equipment','Техника'),('fuel','ГСМ')]:
        order=labor_group_order if kind=='labor' else equipment_group_order if kind=='equipment' else lambda name:(name.casefold(),)
        sections.append({'kind':kind,'label':title,'groups':[{'label':name,'entries':sorted(entries,key=lambda e:(e['label'].casefold(),e['key']))} for name,entries in sorted(groups[kind].items(),key=lambda x:order(x[0]))]})
    # Shared assignments retain their own dates and appear once, as read-only rows.
    from .work_resources import owner_rows,expand
    from calendar import monthrange
    from apps.resources.models import Brigade,EquipmentType
    from .resource_group_totals import labor_groups,equipment_groups
    labor_labels=labor_groups({b.pk:b for b in Brigade.objects.filter(company=version.company).select_related('group')},version.company)
    machine_labels=equipment_groups({e.pk:e for e in EquipmentType.objects.filter(company=version.company).select_related('category','parent__category')},version.company)
    for assignment in owner_rows(version):
        for token,(kind,label,rows) in expand(assignment).items():
            field='planned_liters' if kind=='fuel' else 'planned_count';values=[]
            for month in months:
                value=sum((Decimal(str(r.get(field,0))) for r in rows if r['date'].startswith(month.strftime('%Y-%m'))),ZERO)
                if kind!='fuel':value=value/Decimal(monthrange(month.year,month.month)[1])
                values.append({'month':month,'value':value,'editable':False})
            section=next(s for s in sections if s['kind']==kind)
            first=next(iter(rows),{})
            label_group=labor_labels.get(first.get('brigade_id'),'Без группы') if kind=='labor' else machine_labels.get(first.get('equipment_type_id'),('Без группы','none'))[0] if kind=='equipment' else 'Связи работ и ресурсов'
            group=next((g for g in section['groups'] if g['label']==label_group),None)
            if not group:group={'label':label_group,'entries':[]};section['groups'].append(group)
            group['entries'].append({'label':label,'assignment':True,'measures':[{'field':'liters' if kind=='fuel' else 'count','caption':'Расход · л' if kind=='fuel' else 'Среднее количество','cells':values}]})
    from types import SimpleNamespace
    from .plan_workbench import attach_work_hierarchy
    for section in sections:
        if section['kind']=='work':
            adapted={'kind':'work','groups':[{'entries':[SimpleNamespace(**entry,pk=entry['source'],comparison=[{'item_id_value':m['item_id']} for m in entry['measures']]) for entry in group['entries']]} for group in section['groups']]}
            attach_work_hierarchy([adapted])
            section['hierarchy']=adapted.get('hierarchy',[])
        if section['kind'] in {'labor','equipment'}:
            order=labor_group_order if section['kind']=='labor' else equipment_group_order
            section['groups'].sort(key=lambda group:order(group['label']))
            for group in section['groups']:
                group['unit']='чел.' if section['kind']=='labor' else 'ед.'
                group['totals']=[{'month':month,'value':sum((Decimal(str(measure['cells'][index]['value'] or 0)) for entry in group['entries'] for measure in entry['measures'] if measure.get('field')=='count'),ZERO)} for index,month in enumerate(months)]
        section['count']=sum(len(group['entries']) for group in section['groups'])
    return {'months':months,'sections':sections,'can_work':can_work,'can_resource':can_resource}


def form_data(row):
    from django.forms.models import model_to_dict
    cls=WorkAllocationForm if isinstance(row,WorkMonthAllocation) else ResourceAllocationForm
    values=model_to_dict(row,fields=cls._meta.fields)
    values={name:'' if value is None else json.dumps(value) if name=='selected_item_ids' else value for name,value in values.items()}
    return cls,values


@retry_sqlite_busy
@transaction.atomic
def save(user,version,changes):
    version=GlobalPlanVersion.objects.select_for_update().select_related('workspace').get(pk=version.pk,company=user.company)
    require_planning_object(user,version.construction_object_id)
    can_work,can_resource=permissions(user,version)
    if not can_work and not can_resource:raise PermissionDenied('План недоступен для изменения.')
    if not isinstance(changes,list) or len(changes)>20000:raise ValidationError('Неверный размер изменений.')
    months={m.isoformat():m for m in editable_months(version)};pending={}
    for patch in changes:
        if not isinstance(patch,dict) or patch.get('kind') not in {'work','resource'} or patch.get('month') not in months:raise ValidationError('Недоступный месяц или строка.')
        iswork=patch['kind']=='work'
        if not (can_work if iswork else can_resource):raise PermissionDenied('Нет прав на этот раздел плана.')
        model=WorkMonthAllocation if iswork else ResourceMonthAllocation
        try:source=model.objects.select_related('work').get(pk=patch.get('source'),version=version,company=version.company) if iswork else model.objects.get(pk=patch.get('source'),version=version,company=version.company)
        except (model.DoesNotExist,ValueError,TypeError):raise ValidationError('Состав плана изменился. Обновите таблицу.')
        if patch.get('key')!=identity(source):raise ValidationError('Строка изменилась. Обновите таблицу.')
        key=(patch['kind'],patch['key'],patch['month'])
        if key not in pending:
            lookup={'work_id':source.work_id} if iswork else {name:getattr(source,name) for name in ['kind','brigade_id','equipment_type_id','equipment_number','fuel_type','equipment_ref']}
            row=model.objects.select_for_update().filter(version=version,company=version.company,month=months[patch['month']],**lookup).first()
            if str(patch.get('id') or '')!=str(row.pk if row else '') or patch.get('revision')!=(row.updated_at.isoformat() if row else 'missing'):raise ValidationError('Данные изменены другим пользователем. Обновите таблицу перед сохранением.')
            if not row:
                row=model(company=version.company,version=version,month=months[patch['month']],**lookup)
                if iswork:
                    choices=list(version.work_allocations.filter(work_id=source.work_id).values_list('selected_item_ids',flat=True))
                    row.selected_item_ids=[] if any(not selection for selection in choices) else sorted({pk for selection in choices for pk in selection})
                else:
                    row.rate=source.rate;row.load_profile=source.load_profile
                    if source.kind=='equipment':row.crew_data=deepcopy(source.crew_data)
            cls,data=form_data(row);pending[key]={'row':row,'cls':cls,'data':data,'fields':set()}
        entry=pending[key];field=patch.get('field');row=entry['row'];value=patch.get('value')
        if not isinstance(value,(str,int,float)) or not str(value).strip():raise ValidationError('Введите число или 0. Пустое поле не сохраняется.')
        value=str(value).replace('\u00a0','').replace(' ','').replace(',','.')
        if not isinstance(field,str):raise ValidationError('Неверное поле ячейки.')
        if field in entry['fields']:raise ValidationError('Ячейка передана дважды.')
        entry['fields'].add(field)
        if iswork and field and field.startswith('item:') and row.work.kind=='COMPOSITE':
            values=entry.setdefault('items',item_values(row))
            if field[5:] not in values:raise ValidationError('Работа не входит в состав этого месяца.')
            values[field[5:]]=value;entry['data']['edited_item_quantities']=json.dumps(values,default=str)
        elif field in ({'quantity'} if iswork and row.work.kind!='COMPOSITE' else {'liters','balance'} if not iswork and row.kind=='fuel' else {'count'} if not iswork else set()):entry['data'][field]=value
        else:raise ValidationError('Это поле нельзя менять в таблице.')
    forms=[]
    for entry in pending.values():
        form=entry['cls'](entry['data'],instance=entry['row'],version=version,month=entry['row'].month,user=user)
        if not form.is_valid():raise ValidationError([f'{entry["row"].month:%m.%Y}: '+str(error) for errors in form.errors.values() for error in errors])
        forms.append(form)
    crew_edits=defaultdict(dict)
    for form in forms:
        row=form.save()
        if isinstance(row,ResourceMonthAllocation) and row.kind=='labor' and 'count' in form.changed_data:crew_edits[row.month][row.pk]=form.cleaned_data['count']
    from .table_resource_crew import sync_monthly
    for month in {form.instance.month for form in forms if isinstance(form.instance,ResourceMonthAllocation)}:sync_monthly(user,version,month,edited_counts=crew_edits[month])
    if forms:version.snapshot={};version.save(update_fields=['snapshot'])
    return len(forms)
