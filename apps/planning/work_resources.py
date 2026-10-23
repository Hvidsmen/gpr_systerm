"""Captured equipment crews and explicit work/object resource assignments."""
from copy import deepcopy
from datetime import timedelta
from decimal import Decimal
from django.core.exceptions import ValidationError, PermissionDenied
from django.db import transaction
from core.permissions import PLAN_ROLES, require_roles, require_planning_object
from apps.resources.models import Position, PositionPlanningBrigade, Brigade
from .models import WorkResourceAssignment, DailySchedule, GlobalPlanVersion, DailyScheduleEntry


def owner_rows(owner):
    return owner.resource_assignments.select_related('work','work_item','equipment_type','position').order_by('pk')


def editable(user, owner):
    from .preparation import require_resource_editor
    require_resource_editor(user,owner)
    if owner.company_id!=user.company_id:raise PermissionDenied
    require_planning_object(user,owner.construction_object_id)
    if owner.status not in ['DRAFT','REJECTED']:raise ValidationError('Ресурсы меняются только в черновике.')
    if isinstance(owner,GlobalPlanVersion) and owner.workspace_id and owner.workspace.is_archived:raise PermissionDenied('ГПР в архиве.')


def brigade_for(position):
    from apps.resources.position_catalog import brigade_for as shared_brigade_for
    return shared_brigade_for(position)


def capture(assignment):
    positions=list(assignment.equipment_type.crew_positions.filter(company=assignment.company).select_related('position')) if assignment.equipment_type_id else []
    if assignment.equipment_type_id and not positions:raise ValidationError('У техники не настроен экипаж. Добавьте должности в справочнике техники.')
    if not assignment.equipment_type_id:
        from types import SimpleNamespace
        positions=[SimpleNamespace(position=assignment.position,count=1,is_replacement=False)]
    return [{'position_id':r.position.pk,'name':r.position.name,'count':r.count,'is_replacement':r.is_replacement,'hourly_rate':str(r.position.default_hourly_rate),'brigade_id':brigade_for(r.position)} for r in positions]


def expand(assignment):
    result={};a=assignment
    target=(a.work_item.name if a.work_item_id else a.work.name if a.work_id else 'Общие ресурсы объекта')
    common={'assignment_key':str(a.key),'resource_assignment_id':a.pk,'work_id':a.work_id,'work_item_id':a.work_item_id,'target_label':target,'shifts_per_day':a.shifts_per_day,'hours_per_shift':str(a.hours_per_shift),'origin':'PLAN','comment':''}
    day=a.start_date
    while day<=a.end_date:
        base={**common,'date':day.isoformat()}
        if a.equipment_type_id:
            key='assignment:'+str(a.key)+':equipment'
            result.setdefault(key,('equipment',str(a.equipment_type),[]))[2].append({**base,'equipment_type_id':a.equipment_type_id,'equipment_number':'','label':str(a.equipment_type),'planned_count':str(a.count),'planned_machine_hours':str(a.count*a.shifts_per_day*a.hours_per_shift),'hourly_rate':'0'})
        for member in a.crew_snapshot:
            count=a.count*a.shifts_per_day*member['count']
            label=member['name']+(' *' if member['is_replacement'] else '')
            key='assignment:'+str(a.key)+':position:'+str(member['position_id'])
            result.setdefault(key,('labor',label,[]))[2].append({**base,'position_id':member['position_id'],'is_replacement':member['is_replacement'],'brigade_id':member['brigade_id'],'label':label,'planned_count':str(count),'planned_workers':str(count),'planned_hours':str(count*a.hours_per_shift),'hourly_rate':member['hourly_rate'],'crew_per_shift':a.count*member['count']})
        day+=timedelta(days=1)
    return result


def sync_schedule(schedule):
    generated={}
    for assignment in owner_rows(schedule):
        for key,(kind,label,rows) in expand(assignment).items():
            generated[key]=(assignment,kind,label,rows)
    for entry in schedule.entries.filter(key__startswith='assignment:'):
        if entry.key not in generated:
            past=[deepcopy(r) for r in entry.rows if schedule.locked_before and r['date']<schedule.locked_before.isoformat()]
            if entry.original or past:entry.rows=past;entry.revision+=1;entry.save(update_fields=['rows','revision'])
            else:entry.delete()
    for key,(assignment,kind,label,rows) in generated.items():
        old=schedule.entries.filter(key=key).first()
        if schedule.locked_before:
            cutoff=schedule.locked_before.isoformat()
            rows=([deepcopy(r) for r in old.rows if r['date']<cutoff] if old else [])+[r for r in rows if r['date']>=cutoff]
        metadata={'resource_assignment_id':assignment.pk,'target_label':rows[0]['target_label'] if rows else '', 'crew_snapshot':deepcopy(assignment.crew_snapshot)}
        if old:
            old.kind=kind;old.label=label;old.metadata=metadata;old.rows=rows;old.revision+=1
            old.save(update_fields=['kind','label','metadata','rows','revision'])
        else:DailyScheduleEntry.objects.create(schedule=schedule,key=key,kind=kind,label=label,metadata=metadata,rows=rows)


@transaction.atomic
def save(user, owner, assignment, refresh_crew=False):
    owner=type(owner).objects.select_for_update().get(pk=owner.pk,company=user.company)
    editable(user,owner)
    if assignment.pk:
        original=WorkResourceAssignment.objects.select_for_update().get(pk=assignment.pk,company=user.company)
        if (original.version_id,original.schedule_id)!=(getattr(owner,'pk') if isinstance(owner,GlobalPlanVersion) else None,getattr(owner,'pk') if isinstance(owner,DailySchedule) else None):raise PermissionDenied
    else:original=None
    assignment.company=owner.company
    assignment.version=owner if isinstance(owner,GlobalPlanVersion) else None
    assignment.schedule=owner if isinstance(owner,DailySchedule) else None
    assignment.full_clean()
    if isinstance(owner,DailySchedule) and owner.locked_before:
        cutoff=owner.locked_before
        if original and original.start_date<cutoff:
            if assignment.start_date!=original.start_date or assignment.end_date<cutoff or original.end_date<cutoff:raise ValidationError('Прошедшие дни закрыты. Изменяйте только действующее назначение на оставшиеся дни.')
            if any(getattr(original,field)!=getattr(assignment,field) for field in ['work_id','work_item_id','equipment_type_id','position_id']):raise ValidationError('Для другой техники или работы сначала снимите старое назначение с оставшихся дней и добавьте новое.')
        elif assignment.start_date<cutoff:raise ValidationError('Новое назначение должно начинаться не раньше открытого дня.')
    if not original or refresh_crew or (original.equipment_type_id,original.position_id)!=(assignment.equipment_type_id,assignment.position_id):assignment.crew_snapshot=capture(assignment)
    else:assignment.crew_snapshot=original.crew_snapshot
    assignment.save()
    if isinstance(owner,DailySchedule):sync_schedule(owner)
    from apps.audit.models import AuditLog
    AuditLog.objects.create(user=user,action='UPDATE' if original else 'CREATE',entity_type='planning.workresourceassignment',entity_id=str(assignment.pk),new_value={'count':assignment.count,'shifts_per_day':assignment.shifts_per_day,'crew_snapshot':assignment.crew_snapshot},comment='Назначение ресурсов на работу / объект')
    return assignment


@transaction.atomic
def remove(user,owner,assignment):
    owner=type(owner).objects.select_for_update().get(pk=owner.pk,company=user.company);editable(user,owner)
    assignment=getattr(owner,'resource_assignments').get(pk=assignment.pk)
    if isinstance(owner,DailySchedule) and owner.locked_before and assignment.start_date<owner.locked_before:
        if assignment.end_date<owner.locked_before:raise ValidationError('Назначение содержит только закрытые дни.')
        from apps.audit.models import AuditLog
        AuditLog.objects.create(user=user,action='UPDATE',entity_type='planning.workresourceassignment',entity_id=str(assignment.pk),new_value={'end_date':str(owner.locked_before-timedelta(days=1))},comment='Снято назначение на открытые дни, история сохранена')
        assignment.end_date=owner.locked_before-timedelta(days=1)
        assignment.save(update_fields=['end_date'])
        sync_schedule(owner)
        return
    from apps.audit.models import AuditLog
    AuditLog.objects.create(user=user,action='DELETE',entity_type='planning.workresourceassignment',entity_id=str(assignment.pk),old_value={'count':assignment.count,'shifts_per_day':assignment.shifts_per_day},comment='Удалено назначение ресурсов на работу / объект')
    assignment.delete()
    if isinstance(owner,DailySchedule):sync_schedule(owner)


def apply_snapshot(version,snapshot):
    from .estimate_review import review_period
    start,end=review_period(version)
    for kind in ['labor','equipment']:
        rows=snapshot.setdefault('resources',{}).setdefault(kind,[])
        rows[:]=[r for r in rows if not r.get('assignment_key') or not start.isoformat()<=r['date']<=end.isoformat()]
    for assignment in owner_rows(version):
        for key,(kind,label,rows) in expand(assignment).items():
            snapshot['resources'][kind].extend(rows)
    snapshot['resource_assignments']=list(owner_rows(version).values('key','work_id','work_item_id','equipment_type_id','position_id','start_date','end_date','count','shifts_per_day','hours_per_shift','crew_snapshot'))
    return snapshot


def copy_assignments(source,target,*,copy_original=False):
    if source.company_id!=target.company_id or source.construction_object_id!=target.construction_object_id:raise PermissionDenied('Назначения ресурсов копируются только внутри одного объекта и компании.')
    from .estimate_review import review_period
    start,end=review_period(target)
    for old in owner_rows(source):
        if old.end_date<start or old.start_date>end:continue
        if target.resource_assignments.filter(key=old.key).exists():continue
        data={field:getattr(old,field) for field in ['key','work_id','work_item_id','equipment_type_id','position_id','count','shifts_per_day','hours_per_shift','crew_snapshot']}
        data.update(start_date=max(start,old.start_date),end_date=min(end,old.end_date),company=target.company)
        data['schedule' if isinstance(target,DailySchedule) else 'version']=target
        WorkResourceAssignment.objects.create(**data)
    if isinstance(target,DailySchedule):
        if copy_original and isinstance(source,GlobalPlanVersion):
            for entry in target.entries.exclude(kind='work').exclude(key__startswith='assignment:'):
                entry.rows=[r for r in entry.rows if not r.get('assignment_key')]
                entry.original=[r for r in entry.original if not r.get('assignment_key')]
                if not entry.rows and not entry.original:entry.delete()
                else:entry.save(update_fields=['rows','original'])
        sync_schedule(target)
        if copy_original:
            for entry in target.entries.filter(key__startswith='assignment:'):
                entry.original=deepcopy(entry.rows);entry.save(update_fields=['original'])
