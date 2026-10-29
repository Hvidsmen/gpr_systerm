"""SMG object/month lifecycle, with an optional immutable GPR reference."""
from copy import deepcopy
from django.core.exceptions import ValidationError, PermissionDenied
from django.db import transaction
from django.db.models import Max
from core.permissions import PLAN_ROLES, require_roles, require_planning_object
from apps.projects.models import ConstructionObject
from .models import DailySchedule, DailyScheduleEntry, GlobalPlanVersion
from . import daily_schedule as service


def audit(user, schedule, action, details):
    from apps.audit.models import AuditLog
    AuditLog.objects.create(user=user,action=action,entity_type='planning.dailyschedule',entity_id=str(schedule.pk),new_value=details,comment='Самостоятельное планирование СМГ')


def check(user, schedule):
    require_roles(user, PLAN_ROLES)
    if schedule.company_id != user.company_id: raise PermissionDenied
    require_planning_object(user, schedule.construction_object_id)


@transaction.atomic
def create(user, obj, month, source=None, copy_from_gpr=False, *, name=''):
    require_roles(user, PLAN_ROLES)
    require_planning_object(user, obj.pk)
    obj = ConstructionObject.objects.select_for_update().get(pk=obj.pk, company=user.company)
    if month.day != 1: raise ValidationError('Выберите первый день месяца.')
    if copy_from_gpr and not source: raise ValidationError('Для копирования выберите утвержденный ГПР.')
    revision = (DailySchedule.objects.filter(construction_object=obj, month=month).aggregate(n=Max('revision'))['n'] or 0) + 1
    if len(name.strip())>150:raise ValidationError('Название варианта должно быть не длиннее 150 символов.')
    schedule = DailySchedule.objects.create(company=user.company, construction_object=obj, month=month, revision=revision, created_by=user,name=name.strip())
    if source:
        schedule = reference(user, schedule, source)
        if copy_from_gpr:
            from .subobject_smg import parent_reference
            if parent_reference(schedule):
                from .subobject_smg import add_parent_structure
                add_parent_structure(user,schedule)
                audit(user,schedule,'CREATE',{'object_id':obj.pk,'month':str(month),'source_id':schedule.source_id,'copied_structure_only':True})
                return schedule
            for entry in schedule.entries.all():
                entry.rows = deepcopy(entry.original)
                entry.save(update_fields=['rows'])
            from .work_resources import copy_assignments
            copy_assignments(source,schedule,copy_original=True)
            from .resource_links import restore_schedule
            restore_schedule(schedule)
    audit(user,schedule,'CREATE',{'object_id':obj.pk,'month':str(month),'source_id':schedule.source_id,'copy_from_gpr':copy_from_gpr})
    return schedule



def source_specs(source,month):
    specs={}
    from .report_projection import period_snapshot
    import hashlib, json
    snapshot = period_snapshot(source, *service.bounds(month))
    prior = service.history(source, month)
    from datetime import timedelta
    for spec in snapshot.get('works', []):
        original = deepcopy(spec.get('plans', []))
        if not original: continue
        metadata = {k:deepcopy(v) for k,v in spec.items() if k not in {'plans','daily','context_plans'}}
        metadata['history'] = [dict(date=(month-timedelta(days=1)).isoformat(),item_id=i,quantity=str(q)) for i,q in prior[spec['id']].items()]
        metadata['original_daily'] = deepcopy(spec.get('daily', []))
        specs['work:'+str(spec['id'])] = ('work',spec['name'],metadata,original)
    from collections import defaultdict
    for kind in ['labor','equipment','fuel']:
        groups = defaultdict(list)
        for row in snapshot.get('resources',{}).get(kind,[]):groups[service.resource_identity(row,kind)].append(row)
        for identity, original in groups.items():
            key = kind+':'+hashlib.sha256(json.dumps(identity).encode()).hexdigest()
            specs[key] = (kind,original[0].get('label',str(identity)),{},deepcopy(original))
    return specs

@transaction.atomic
def reference(user, schedule, source):
    schedule = DailySchedule.objects.select_for_update().get(pk=schedule.pk, company=user.company)
    check(user, schedule)
    if schedule.status != 'DRAFT': raise ValidationError('Ссылку на ГПР можно менять только в черновике СМГ.')
    if schedule.source_id == (source.pk if source else None):return schedule
    old_source_id=schedule.source_id
    specs = {}
    if source:
        source = GlobalPlanVersion.objects.get(pk=source.pk, company=user.company)
        from .subobject_smg import valid_reference
        if not valid_reference(schedule.construction_object,source):raise ValidationError('Выберите ГПР этого объекта или его родительского объекта.')
        service.validate_source(source, schedule.month)
        specs=source_specs(source,schedule.month) if source.construction_object_id==schedule.construction_object_id else {}
    schedule.source=source
    present = set()
    for entry in schedule.entries.all():
        entry.schedule=schedule
        present.add(entry.key)
        spec = specs.get(entry.key)
        entry.original = deepcopy(spec[3]) if spec else []
        if entry.kind == 'work':
            # Keep the SMG's own composition and rows; only comparison data changes.
            entry.metadata['original_daily'] = deepcopy(spec[2].get('original_daily',[])) if spec else []
            if spec:
                ids = {i['id'] for i in entry.metadata.get('items',[])}
                entry.metadata.setdefault('items',[]).extend(deepcopy(i) for i in spec[2].get('items',[]) if i['id'] not in ids)
            from .subobject_smg import parent_reference,annotate_entry
            if parent_reference(schedule):
                from apps.works.models import ProjectWork
                work=ProjectWork.objects.filter(company=user.company,pk=entry.metadata.get('id'),section__construction_object_id=schedule.construction_object_id).first()
                if work:annotate_entry(entry,work)
        entry.revision += 1
        entry.save(update_fields=['original','metadata','revision','gpr_work'])
    for key, (kind,label,metadata,original) in specs.items():
        if key not in present:
            entry = DailyScheduleEntry(schedule=schedule,key=key,kind=kind,label=label,metadata=metadata,original=original)
            values = [{field:0 for field,_,_ in service.FIELDS[kind]} for _ in service.identities(entry)]
            entry.rows = service.value_rows(entry, values)
            entry.save()
    schedule.source = source
    schedule.save(update_fields=['source','updated_at'])
    # Changing the comparison basis requires a fresh estimator confirmation.
    latest=schedule.estimate_reviews.order_by('-pk').first()
    if latest and latest.status=='APPROVED':
        latest.status='OPEN';latest.fingerprint='';latest.save(update_fields=['status','fingerprint','updated_at'])
        from .estimate_review import event
        event(latest,user,'REFERENCE_CHANGED',{'source_id':schedule.source_id})
    audit(user,schedule,'UPDATE',{'old_source_id':old_source_id,'source_id':schedule.source_id})
    return schedule


@transaction.atomic
def revise(user, schedule, *, independent=False):
    schedule = DailySchedule.objects.get(pk=schedule.pk, company=user.company)
    check(user, schedule)
    if not independent and schedule.status not in {'APPLIED','ARCHIVED'}: raise ValidationError('Сначала завершите текущую редакцию СМГ.')
    obj = ConstructionObject.objects.select_for_update().get(pk=schedule.construction_object_id)
    schedule = DailySchedule.objects.select_for_update().select_related('source').defer('source__snapshot').get(pk=schedule.pk,company=user.company)
    list(schedule.entries.select_for_update().order_by('pk'))
    revision = DailySchedule.objects.filter(construction_object=obj,month=schedule.month).aggregate(n=Max('revision'))['n']+1
    new = DailySchedule.objects.create(company=user.company,construction_object=obj,source=schedule.source,month=schedule.month,revision=revision,created_by=user,replaces=schedule,locked_before=schedule.locked_before,name=((schedule.name+' — копия')[:150] if schedule.name and independent else schedule.name))
    DailyScheduleEntry.objects.bulk_create([DailyScheduleEntry(schedule=new,gpr_work_id=e.gpr_work_id,key=e.key,kind=e.kind,label=e.label,metadata=deepcopy(e.metadata),original=deepcopy(e.original),rows=deepcopy(e.rows)) for e in schedule.entries.all()])
    from .work_resources import copy_assignments
    copy_assignments(schedule,new)
    from .resource_links import restore_schedule
    restore_schedule(new)
    audit(user,new,'CREATE',{'replaces':schedule.pk,'revision':new.revision,'source_id':new.source_id})
    return new
