"""Replace table resource identities in place, preserving quantities and work links."""
from collections import defaultdict
from copy import deepcopy
from datetime import date
from decimal import Decimal
import hashlib
import json
from django.core import signing
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.forms.models import model_to_dict
from core.permissions import require_planning_object
from .models import GlobalPlanVersion, DailySchedule, ResourceMonthAllocation, DailyScheduleEntry
from .preparation import require_resource_input, resource_signature
from . import table_resource_crew as crew

SALT = 'plan-resource-replacement-v1'


def choices(company, kind):
    if kind=='labor':
        from apps.resources.position_catalog import planning_brigades
        return planning_brigades(company).order_by('name','pk')
    from apps.resources.models import EquipmentType
    return EquipmentType.objects.filter(company=company,is_active=True,parent__isnull=True).exclude(catalogue_review__status__in=['PENDING','REJECTED','REPLACED']).order_by('name','pk')



def authorize(user, owner):
    require_resource_input(user, owner)
    require_planning_object(user, owner.construction_object_id)
    if owner.status not in {'DRAFT', 'REJECTED'} or isinstance(owner, DailySchedule) and owner.status != 'DRAFT':
        raise PermissionDenied('Заменить ресурс можно только в редактируемом плане.')
    if isinstance(owner, GlobalPlanVersion) and owner.workspace_id and owner.workspace.is_archived:
        raise PermissionDenied('План в архиве.')
    if isinstance(owner, DailySchedule) and owner.locked_before and owner.locked_before > owner.month:
        raise ValidationError('В этой редакции СМГ есть закрытые дни. Замена всей строки затронула бы сохранённую историю. Создайте независимый вариант СМГ без закрытых дней.')


def sources(owner, source_id, month=None):
    if isinstance(owner, DailySchedule):
        source = owner.entries.select_for_update().filter(pk=source_id).first()
        if not source or source.kind not in {'labor', 'equipment'} or source.metadata.get('resource_assignment_id'):
            raise ValidationError('Выберите строку должности или техники, введённую в таблице плана.')
        return [source]
    from .bulk_add import editable_months
    allowed = editable_months(owner)
    source = owner.resource_allocations.select_for_update().filter(pk=source_id, company=owner.company).first()
    if not source or source.kind not in {'labor', 'equipment'}:
        raise ValidationError('Строка ресурса недоступна.')
    if month is not None:
        if month not in allowed or source.month != month:
            raise ValidationError('Выбранный месяц недоступен для замены.')
        return [source]
    identity = {name:getattr(source, name) for name in ['kind', 'brigade_id', 'equipment_type_id', 'equipment_number', 'fuel_type', 'equipment_ref']}
    result = list(owner.resource_allocations.select_for_update().filter(company=owner.company, month__in=allowed, **identity).order_by('month','pk'))
    if not result:raise ValidationError('У этой позиции нет редактируемых месяцев.')
    return result


def current_id(source):
    if isinstance(source, ResourceMonthAllocation):
        return source.brigade_id if source.kind == 'labor' else source.equipment_type_id
    return next(iter(source.rows or source.original),{}).get('brigade_id' if source.kind == 'labor' else 'equipment_type_id')


def label(source):
    if isinstance(source, DailyScheduleEntry):return source.label
    return str(source.brigade if source.kind == 'labor' else source.equipment_type)


def stamp(owner):
    controls = {key:str(getattr(owner,key,None)) for key in ['status','month','locked_before','source_id','start_date','end_date','planning_month','scenario']}
    if isinstance(owner, GlobalPlanVersion) and owner.workspace_id:
        controls.update(archived=owner.workspace.is_archived, continuation=str(owner.workspace.continuation_start))
    return hashlib.sha256(json.dumps([resource_signature(owner),controls],sort_keys=True).encode()).hexdigest()


def target_stamp(target):
    data={'target':model_to_dict(target)}
    if target._meta.label=='resources.EquipmentType':
        data['crew']=list(target.crew_positions.order_by('position_id').values('position_id','count','position__name','position__default_hourly_rate'))
    return hashlib.sha256(json.dumps(data,sort_keys=True,default=str).encode()).hexdigest()


def token(user, owner, source_id, month=None, *, target=None, add_crew=True, target_state=None):
    return signing.dumps({'actor':user.pk,'company':user.company_id,'kind':'smg' if isinstance(owner,DailySchedule) else 'gpr','owner':owner.pk,'source':source_id,'month':str(month) if month else None,'state':stamp(owner),'target':target,'add_crew':add_crew,'target_state':target_state},salt=SALT,compress=True)


def check_token(value,user,owner,source_id,month,*,target=None,add_crew=True,confirmation=False,target_state=None):
    try:data=signing.loads(value,salt=SALT,max_age=1800)
    except (signing.BadSignature,TypeError):raise ValidationError('Форма устарела. Откройте замену заново.')
    expected={'actor':user.pk,'company':user.company_id,'kind':'smg' if isinstance(owner,DailySchedule) else 'gpr','owner':owner.pk,'source':source_id,'month':str(month) if month else None}
    if any(data.get(key)!=value for key,value in expected.items()):raise ValidationError('Форма относится к другому пользователю или плану.')
    if data.get('state')!=stamp(owner):raise ValidationError('План изменился после открытия замены. Обновите страницу и проверьте новые значения.')
    if confirmation and (data.get('target')!=target or data.get('add_crew')!=add_crew or data.get('target_state')!=target_state):raise ValidationError('Параметры замены изменились. Сначала обновите предварительный расчёт.')


def _labor_counts(owner):
    result = defaultdict(Decimal)
    if isinstance(owner, DailySchedule):
        for entry in owner.entries.filter(kind='labor'):
            if entry.metadata.get('resource_assignment_id'):continue
            for row in entry.rows:
                key=(row['date'],row.get('brigade_id'),entry.label)
                result[key]+=Decimal(str(row.get('planned_workers',row.get('planned_count',0))))
    else:
        for row in owner.resource_allocations.filter(kind='labor',company=owner.company).select_related('brigade'):
            result[(row.month.isoformat(),row.brigade_id,str(row.brigade))]+=row.count
    return result


def _remap(members, old_id, new_id):
    result=deepcopy(members)
    for member in result:
        if member['brigade_id']==old_id:member['brigade_id']=new_id
    return result


def _monthly(user,owner,selected,target,add_crew):
    for source in selected:
        field='brigade_id' if source.kind=='labor' else 'equipment_type_id'
        duplicate=owner.resource_allocations.filter(company=owner.company,month=source.month,kind=source.kind,**{field:target.pk}).exclude(pk=source.pk)
        if source.kind=='equipment':duplicate=duplicate.filter(equipment_number=source.equipment_number)
        if duplicate.exists():raise ValidationError(f'«{target}» уже есть в плане за {source.month:%m.%Y}. Выберите другую позицию; значения не объединены.')
    for source in selected:
        if source.kind=='equipment':
            source.equipment_type=target
            source.crew_data={**source.crew_data,'members':crew.recipe(user.company,target,{}) if add_crew else []}
        else:
            old_id=source.brigade_id
            for machine in owner.resource_allocations.select_for_update().filter(company=owner.company,month=source.month,kind='equipment').select_related('equipment_type'):
                members=crew.recipe(user.company,machine.equipment_type,machine.crew_data)
                mapped=_remap(members,old_id,target.pk)
                if mapped!=members:
                    machine.crew_data={**machine.crew_data,'members':mapped};machine.save(update_fields=['crew_data','updated_at'])
            source.brigade=target
        source.full_clean();source.save()
        crew.sync_monthly(user,owner,source.month)
    owner.snapshot={};owner.save(update_fields=['snapshot','updated_at'])


def _baseline(owner,entry,rows):
    """A new identity compares with its own frozen GPR baseline, never the old one."""
    from .daily_schedule import FIELDS,bounds,resource_identity
    expected=resource_identity(rows[0],entry.kind)
    grouped=defaultdict(list)
    if owner.source_id and owner.source.construction_object_id==owner.construction_object_id:
        from .report_projection import period_snapshot
        for row in period_snapshot(owner.source,*bounds(owner.month)).get('resources',{}).get(entry.kind,[]):
            if resource_identity(row,entry.kind)==expected:grouped[row['date']].append(row)
    result=[]
    for row in rows:
        original=deepcopy(row)
        for field,_,_ in FIELDS[entry.kind]:
            original[field]=str(sum((Decimal(str(r.get('planned_workers',r.get('planned_count',0)) if entry.kind=='labor' and field=='planned_count' else r.get(field,0))) for r in grouped[row['date']]),Decimal(0)))
        if entry.kind=='labor':original['planned_workers']=original['planned_count']
        result.append(original)
    return result


def _daily(user,owner,source,target,add_crew):
    field='brigade_id' if source.kind=='labor' else 'equipment_type_id'
    first=next(iter(source.rows or source.original),{})
    identity={**first,field:target.pk}
    from .daily_schedule import resource_identity
    wanted=resource_identity(identity,source.kind)
    for other in owner.entries.select_for_update().filter(kind=source.kind).exclude(pk=source.pk):
        row=next(iter(other.rows or other.original),{})
        if resource_identity(row,other.kind)==wanted:raise ValidationError(f'«{target}» уже есть в этом СМГ. Выберите другую позицию; значения не объединены.')
    if source.kind=='labor':
        old_id=current_id(source)
        for machine in owner.entries.select_for_update().filter(kind='equipment'):
            if machine.metadata.get('resource_assignment_id'):continue
            members=crew.daily_crew_input(machine);mapped=_remap(members,old_id,target.pk)
            if mapped!=members:
                machine.metadata={**machine.metadata,'table_crew':mapped};machine.revision+=1;machine.save(update_fields=['metadata','revision'])
    else:
        source.metadata={**source.metadata,'table_crew':crew.recipe(user.company,target,{}) if add_crew else []}
    rows=deepcopy(source.rows)
    if not rows:raise ValidationError('В строке нет дневных значений. Обновите СМГ перед заменой.')
    for row in rows:
        row[field]=target.pk;row['label']=str(target)
        # Older rows can carry a duplicate captured recipe; keep it consistent.
        if source.kind=='equipment' and 'table_crew' in row:row['table_crew']=deepcopy(source.metadata['table_crew'])
    source.label=str(target)
    source.key=source.kind+':'+hashlib.sha256(json.dumps(wanted).encode()).hexdigest()
    source.rows=rows;source.original=_baseline(owner,source,rows)
    source.revision+=1;source.save(update_fields=['label','key','rows','original','metadata','revision'])
    crew.sync_daily(user,owner)


@transaction.atomic
def replace_resource(user,owner,source_id,target,*,month=None,add_crew=True,signed,preview=False):
    owner=type(owner).objects.select_for_update().get(pk=owner.pk,company=user.company)
    authorize(user,owner)
    selected=sources(owner,source_id,month)
    kind=selected[0].kind
    # Validate again under the plan lock, including inactive and pending entries.
    target=choices(user.company,kind).select_for_update().filter(pk=target.pk).first()
    if not target:raise ValidationError('Выбранная позиция недоступна для планирования.')
    if kind=='equipment':list(target.crew_positions.select_for_update().values_list('pk',flat=True))
    if current_id(selected[0])==target.pk:raise ValidationError('Выберите другую позицию.')
    check_token(signed,user,owner,source_id,month,target=target.pk,add_crew=add_crew,confirmation=not preview,target_state=target_stamp(target))
    before=_labor_counts(owner)
    old=[model_to_dict(row) for row in selected]
    if isinstance(owner,DailySchedule):_daily(user,owner,selected[0],target,add_crew)
    else:_monthly(user,owner,selected,target,add_crew)
    after=_labor_counts(owner)
    changes=[{'date':key[0],'name':key[2],'before':before[key],'after':after[key]} for key in sorted(before.keys()|after.keys()) if before[key]!=after[key]]
    from apps.audit.models import AuditLog
    AuditLog.objects.create(user=user,action='UPDATE',entity_type='planning.resource_replacement',entity_id=str(owner.pk),old_value=json.loads(json.dumps(old,default=str)),new_value={'target':target.pk,'kind':kind,'sources':[row.pk for row in selected],'month':str(month) if month else 'all','add_crew':add_crew},comment='Замена ресурса в плане с сохранением количеств и связей')
    result={'changes':changes,'target':str(target),'count':len(selected),'empty_crew':kind=='equipment' and add_crew and not list(crew.crew_positions(user.company,target))}
    if preview:transaction.set_rollback(True)
    return result
