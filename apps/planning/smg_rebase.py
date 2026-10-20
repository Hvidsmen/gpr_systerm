"""Planner handoff after final GPR carryover approval; no fact writes."""
from copy import deepcopy
from datetime import timedelta
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from core.permissions import PLAN_ROLES,require_roles,role_code
from .models import CarryoverReviewTask,DailySchedule,DailyScheduleEntry,WorkTask


def old_schedule(record):
    return DailySchedule.objects.filter(company_id=record.company_id,source_id=record.version_id,month=record.month,status='APPLIED').order_by('-revision').first()


def changes(record):
    from .daily_schedule import bounds
    from .report_projection import period_snapshot
    from decimal import Decimal
    def quantities(version):
        snapshot=period_snapshot(version,*bounds(record.month));result={}
        for spec in snapshot.get('works',[]):
            values={}
            for row in spec.get('plans',[]):
                key=str(row.get('item_id'));values[key]=values.get(key,Decimal(0))+Decimal(str(row.get('quantity') or 0))
            if any(values.values()):result[spec['id']]=(spec['name'],values)
        return result
    before,after=quantities(record.version),quantities(record.correction_version)
    return [f'{(after.get(key) or before[key])[0]}: ГПР изменен' for key in sorted(before.keys()|after.keys()) if (before.get(key) or ('',{}))[1]!=(after.get(key) or ('',{}))[1]]


def sync(record):
    from .task_services import ensure,state,ACTIVE
    key=f'smg-update:{record.pk}:{record.correction_version_id}'
    if not record.correction_version_id or record.correction_version.status not in {'APPROVED','COMPLETED'}:return
    for task in WorkTask.objects.filter(company_id=record.company_id,carryover=record,key__startswith=f'smg-update:{record.pk}:',status__in=ACTIVE).exclude(key=key):state(task,'SUPERSEDED',text='Утверждена другая корректировка ГПР.')
    parent=WorkTask.objects.filter(company_id=record.company_id,key=f'carryover:{record.pk}').first()
    if parent:state(parent,'DONE',text='ГПР с фактическими остатками утвержден.')
    existing=WorkTask.objects.filter(company_id=record.company_id,key=key).first()
    if not existing:
        assigned=record.assigned_to if record.assigned_to.is_active and role_code(record.assigned_to) in PLAN_ROLES else None
        description=f'ГПР с фактическими остатками утвержден. Актуализируйте дневной СМГ за {record.month:%m.%Y} относительно ГПР v{record.correction_version.version_number}. Прошедшие дни сохраняются. Распределите изменения по оставшимся дням и направьте СМГ сметчику. При совпадении месячных итогов с ГПР сохраните оперативный план; при отклонении запустите согласование.'
        changed=changes(record)
        description+='\n\nИзменения работ:\n'+ ('\n'.join(changed) if changed else 'Месячные объемы работ не изменились. Проверьте ресурсы и распределение по дням.')
        existing=ensure(record.company,key,kind='SUBMISSION',title=f'Актуализировать СМГ после уточнения остатков · {record.month:%m.%Y}',description=description,parent=parent,assigned_to=assigned,target_role='' if assigned else 'PLANNER',version=record.correction_version,carryover=record,construction_object_id=record.correction_version.construction_object_id,due_date=timezone.localdate()+timedelta(days=1))
    if not existing.schedule_id:
        applied=DailySchedule.objects.filter(source_id=record.correction_version_id,month=record.month,status='APPLIED').first()
        if applied:existing.schedule=applied;existing.save(update_fields=['schedule'])
    if existing.status in ACTIVE and existing.assigned_to_id and (not existing.assigned_to.is_active or role_code(existing.assigned_to) not in PLAN_ROLES):
        existing.assigned_to=None;existing.target_role='PLANNER';existing.save(update_fields=['assigned_to','target_role'])
    if existing.schedule_id:
        if existing.schedule.status in {'APPLIED','ARCHIVED'}:state(existing,'DONE',text='Актуализированный СМГ применен к оперативному плану.')
    return existing


@transaction.atomic
def start(user,task):
    from .daily_schedule import create,bounds
    from .report_projection import period_snapshot
    from .workspace_services import resource_identity
    import hashlib,json
    require_roles(user,PLAN_ROLES)
    task=WorkTask.objects.select_for_update().select_related('carryover__correction_version','carryover__version').get(pk=task.pk,company=user.company)
    from .task_services import is_executor
    if not is_executor(user,task) or task.status!='OPEN' or not task.key.startswith('smg-update:'):raise ValidationError('Задание актуализации недоступно.')
    record=task.carryover
    if not record or record.correction_version_id!=task.version_id or record.correction_version.status not in {'APPROVED','COMPLETED'}:raise ValidationError('Дождитесь утверждения актуальной корректировки ГПР.')
    if task.schedule_id:return task.schedule
    # Lock the original GPR too, so activation of its operative SMG cannot race the copy.
    from .models import GlobalPlanVersion
    GlobalPlanVersion.objects.select_for_update().only('pk').get(pk=record.version_id)
    prior=old_schedule(record)
    existing=DailySchedule.objects.filter(source_id=record.correction_version_id,month=record.month).exists()
    schedule=create(user,record.correction_version,record.month,_skip_rebase=True)
    if not schedule.locked_before and schedule.status in {'SUBMITTED','APPROVED'}:
        raise ValidationError('Сначала верните существующий СМГ на доработку для сохранения прошедших дней.')
    if not schedule.locked_before and schedule.status=='DRAFT':
        previous={e.key:e for e in prior.entries.all()} if prior else {}
        fallback={};fallback_specs={}
        if not prior:
            snapshot=period_snapshot(record.version,*bounds(record.month))
            for spec in snapshot.get('works',[]):
                key='work:'+str(spec['id']);fallback[key]=spec.get('plans',[])
                fallback_specs[key]=('work',spec['name'],{k:deepcopy(v) for k,v in spec.items() if k not in {'plans','daily','context_plans'}})
            for kind,rows in snapshot.get('resources',{}).items():
                for row in rows:
                    key=kind+':'+hashlib.sha256(json.dumps(resource_identity(row,kind)).encode()).hexdigest()
                    fallback.setdefault(key,[]).append(row)
                    fallback_specs[key]=(kind,row.get('label',kind),{})
        cutoff=timezone.localdate().isoformat()
        included=set()
        for entry in schedule.entries.all():
            included.add(entry.key);old=previous.get(entry.key)
            if old:
                entry.rows=([deepcopy(r) for r in entry.rows if r['date']>=cutoff]+[deepcopy(r) for r in old.rows if r['date']<cutoff]) if existing else deepcopy(old.rows)
                # Retain removed subworks needed to display frozen historical days.
                if entry.kind=='work' and old.metadata.get('items'):
                    ids={i['id'] for i in entry.metadata.get('items',[])}
                    entry.metadata.setdefault('items',[]).extend(deepcopy(i) for i in old.metadata['items'] if i['id'] not in ids)
            else:
                entry.rows=[r for r in entry.rows if r['date']>=cutoff]+[deepcopy(r) for r in fallback.get(entry.key,[]) if r['date']<cutoff]
            entry.save(update_fields=['rows','metadata'])
        for key,entry in previous.items():
            if key not in included:
                DailyScheduleEntry.objects.create(schedule=schedule,key=key,kind=entry.kind,label=entry.label,metadata=deepcopy(entry.metadata),original=[],rows=deepcopy(entry.rows))
        for key,rows in fallback.items():
            past=[deepcopy(r) for r in rows if r['date']<cutoff]
            if key not in included and past:
                kind,label,metadata=fallback_specs[key]
                DailyScheduleEntry.objects.create(schedule=schedule,key=key,kind=kind,label=label,metadata=metadata,original=[],rows=past)
        schedule.replaces=prior;schedule.locked_before=timezone.localdate();schedule.save(update_fields=['replaces','locked_before'])
    task.schedule=schedule;task.save(update_fields=['schedule'])
    from .task_services import event
    event(task,user,'Актуализация СМГ начата',f'Создана/открыта редакция {schedule.revision} на основе уточненного ГПР.')
    return schedule


def effective_schedules(source,start,end):
    """Use an old operative SMG until a replacement for corrected GPR is applied."""
    schedules=list(DailySchedule.objects.filter(source=source,company_id=source.company_id,status='APPLIED',month__lte=end,month__gte=start.replace(day=1)).prefetch_related('entries'))
    months={s.month for s in schedules}
    for record in CarryoverReviewTask.objects.filter(company_id=source.company_id,correction_version=source,month__gte=start.replace(day=1),month__lte=end).order_by('-pk'):
        if record.month not in months:
            previous=old_schedule(record)
            if previous:schedules.append(previous);months.add(record.month)
    return schedules
