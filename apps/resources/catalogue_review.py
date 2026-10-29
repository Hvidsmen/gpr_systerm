"""Provisional plan resources and company-scoped catalogue decisions."""
import hashlib
import json
from copy import deepcopy
from datetime import date
from decimal import Decimal

from django.core import signing
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Q
from django.urls import reverse
from django.utils import timezone

from core.permissions import (role_code, require_roles, PERSONNEL_CATALOG_MANAGER, EQUIPMENT_CATALOG_MANAGER)
from .models import Brigade, EquipmentType, CatalogueReviewRequest
from apps.planning.models import GlobalPlanVersion, DailySchedule

BLOCKING = {'PENDING', 'REJECTED', 'REPLACED'}
REVIEW_ROLES = {'labor': PERSONNEL_CATALOG_MANAGER, 'equipment': EQUIPMENT_CATALOG_MANAGER}
SALT = 'plan-catalogue-replacement-v1'


def owner_key(owner):
    return ('smg' if isinstance(owner, DailySchedule) else 'gpr', owner.pk)


def owner_url(owner):
    return reverse('planning:daily_schedule_detail' if isinstance(owner, DailySchedule) else
                   'planning:workspace_edit' if owner.workspace_id else 'planning:global_detail', args=[owner.pk])


def resource_ids(owner):
    """Include table resources, explicit assignments and captured crews, even zero rows."""
    result = {'labor': set(), 'equipment': set()}
    def scan(value):
        if isinstance(value, list):
            for item in value: scan(item)
        elif isinstance(value, dict):
            for key, item in value.items():
                if key in {'brigade_id', 'equipment_type_id'} and item:
                    try: result['labor' if key == 'brigade_id' else 'equipment'].add(int(item))
                    except (TypeError,ValueError): pass
                elif isinstance(item, (list, dict)): scan(item)
    if isinstance(owner, DailySchedule):
        for entry in owner.entries.exclude(kind='work'):
            scan([entry.rows, entry.original, entry.metadata])
    else:
        for row in owner.resource_allocations.all():
            if row.brigade_id: result['labor'].add(row.brigade_id)
            if row.equipment_type_id: result['equipment'].add(row.equipment_type_id)
            scan(row.crew_data)
    for assignment in owner.resource_assignments.select_related('position__planning_mapping'):
        if assignment.equipment_type_id: result['equipment'].add(assignment.equipment_type_id)
        if assignment.position_id:
            mapping = getattr(assignment.position, 'planning_mapping', None)
            if mapping: result['labor'].add(mapping.brigade_id)
        scan(assignment.crew_snapshot)
    return result


def requests_for(owner, blocking=False):
    base = CatalogueReviewRequest.objects.filter(company_id=owner.company_id)
    if blocking: base = base.filter(status__in=BLOCKING)
    if not base.exists(): return base.none()
    ids = resource_ids(owner)
    query = base.filter(
        Q(brigade_id__in=ids['labor']) | Q(equipment_type_id__in=ids['equipment']))
    if blocking: query = query.filter(status__in=BLOCKING)
    return query.select_related('brigade', 'equipment_type', 'created_by', 'reviewed_by', 'target_brigade', 'target_equipment')


def require_reviewed(owner):
    pending = list(requests_for(owner, blocking=True))
    if pending:
        names = ', '.join(str(r.resource) for r in pending[:8])
        raise ValidationError('Нельзя отправить план на согласование: проверьте новые должности и технику — ' + names + '. Отклонённые позиции замените или удалите из плана.')


def context(user, owner):
    editable = True
    from apps.planning.work_resources import editable as require_editor
    try: require_editor(user, owner)
    except (PermissionDenied, ValidationError): editable = False
    kind, pk = owner_key(owner)
    editable = editable and (isinstance(owner,DailySchedule) or bool(owner.workspace_id))
    rows = list(requests_for(owner))
    from apps.planning.task_services import visible
    tasks=dict(visible(user).filter(catalogue_request_id__in=[row.pk for row in rows]).values_list('catalogue_request_id','pk'))
    for row in rows:
        row.task_url=reverse('planning:task_detail',args=[tasks[row.pk]]) if row.pk in tasks else ''
        row.fix_url = reverse('planning:catalogue_fix', args=[kind, pk, row.pk])
    return {'catalogue_month': owner.month if isinstance(owner,DailySchedule) else owner.planning_month or owner.start_date.replace(day=1), 'catalogue_requests': rows, 'catalogue_ready': not any(r.status in BLOCKING for r in rows),
            'catalogue_create_labor_url': reverse('planning:catalogue_propose', args=[kind, pk, 'labor']) if editable else '',
            'catalogue_create_equipment_url': reverse('planning:catalogue_propose', args=[kind, pk, 'equipment']) if editable else '',
            'catalogue_can_fix': editable}


def confirmed_choices(company, kind):
    model = Brigade if kind == 'labor' else EquipmentType
    query = model.objects.filter(company=company, is_active=True, merge_source__isnull=True)
    if kind == 'equipment': query = query.filter(parent__isnull=True)
    return query.exclude(catalogue_review__status__in=BLOCKING).order_by('name', 'pk')


@transaction.atomic
def propose(user, owner, kind, name, group=None, months=None):
    from apps.accounts.models import Company
    Company.objects.select_for_update().get(pk=user.company_id)
    owner = type(owner).objects.select_for_update().get(pk=owner.pk, company=user.company)
    from apps.planning.work_resources import editable
    editable(user, owner)
    if isinstance(owner,GlobalPlanVersion) and not owner.workspace_id: raise ValidationError('Создание новой позиции доступно в рабочем ГПР с помесячным планированием.')
    if kind not in REVIEW_ROLES: raise ValidationError('Неизвестный вид ресурса.')
    name = ' '.join(str(name).split())
    if not name or len(name) > 150: raise ValidationError('Укажите название до 150 символов.')
    if group and group.company_id != user.company_id: raise PermissionDenied
    model = Brigade if kind == 'labor' else EquipmentType
    candidates = model.objects.filter(company=user.company, merge_source__isnull=True)
    if any(item.name.casefold() == name.casefold() for item in candidates):
        raise ValidationError('Позиция с таким названием уже есть в справочнике или на проверке. Уточните название или выберите существующую.')
    fields = {'group': group, 'unit': 'чел.'} if kind == 'labor' else {'category': group, 'unit': 'ед.'}
    resource = model.objects.create(company=user.company, name=name, is_active=False, **fields)
    proposal = CatalogueReviewRequest.objects.create(company=user.company, kind=kind, created_by=user,
        brigade=resource if kind == 'labor' else None, equipment_type=resource if kind == 'equipment' else None,
        version=owner if isinstance(owner, GlobalPlanVersion) else None,
        schedule=owner if isinstance(owner, DailySchedule) else None,
        origin_label=f'{"СМГ" if isinstance(owner, DailySchedule) else "ГПР"} · {owner.construction_object}')
    if isinstance(owner, DailySchedule):
        from apps.planning.daily_schedule_table import add_entry
        add_entry(user, owner, {'kind': kind, 'brigade': resource if kind == 'labor' else None,
                              'equipment': resource if kind == 'equipment' else None, 'rate': Decimal(0)})
    else:
        from apps.planning.bulk_add import editable_months
        from apps.planning.models import ResourceMonthAllocation
        allowed = set(editable_months(owner))
        selected = set(months or [owner.planning_month or min(allowed)])
        if not selected or not selected <= allowed: raise ValidationError('Выберите месяцы редактируемого периода.')
        for month in sorted(selected):
            ResourceMonthAllocation.objects.create(company=user.company, version=owner, month=month, kind=kind,
                **({'brigade': resource} if kind == 'labor' else {'equipment_type': resource}))
    invalidate(owner)
    audit(user, proposal, 'CREATE', {'name': name, 'status': 'PENDING'})
    from .catalogue_tasks import sync_task
    sync_task(proposal)
    return proposal


def invalidate(owner):
    if isinstance(owner, GlobalPlanVersion):
        owner.snapshot = {}; owner.save(update_fields=['snapshot', 'updated_at'])
    else: owner.save(update_fields=['updated_at'])
    from apps.planning.preparation import record
    preparation = record(owner)
    if preparation:
        preparation.resources_ready_at = None; preparation.resources_fingerprint = ''
        preparation.save(update_fields=['resources_ready_at', 'resources_fingerprint', 'updated_at'])


def audit(user, proposal, action, data):
    from apps.audit.models import AuditLog
    AuditLog.objects.create(user=user, action=action, entity_type='resources.CatalogueReviewRequest',
        entity_id=str(proposal.pk), new_value=data, comment='Проверка новой позиции из плана')


def affected_owners(proposal, lock=False):
    # Find references in batches. Only lock the plans that actually use this item.
    from apps.planning.models import ResourceMonthAllocation, DailyScheduleEntry, WorkResourceAssignment
    identity = proposal.brigade_id if proposal.kind == 'labor' else proposal.equipment_type_id
    field = 'brigade_id' if proposal.kind == 'labor' else 'equipment_type_id'
    position_id = None
    if proposal.kind == 'labor':
        from .models import PositionPlanningBrigade
        position_id = PositionPlanningBrigade.objects.filter(company=proposal.company,brigade_id=identity).values_list('position_id',flat=True).first()
    def contains(value):
        if isinstance(value, list): return any(contains(v) for v in value)
        if not isinstance(value, dict): return False
        return str(value.get(field)) == str(identity) or any(contains(v) for v in value.values() if isinstance(v,(dict,list)))
    versions = set(ResourceMonthAllocation.objects.filter(company=proposal.company, **{field:identity}).values_list('version_id',flat=True))
    schedules = set()
    if proposal.kind == 'labor':
        for version_id, crew in ResourceMonthAllocation.objects.filter(company=proposal.company).exclude(crew_data={}).values_list('version_id','crew_data'):
            if contains(crew): versions.add(version_id)
    for entry in DailyScheduleEntry.objects.filter(schedule__company=proposal.company).exclude(kind='work').values('schedule_id','rows','original','metadata'):
        if contains([entry['rows'],entry['original'],entry['metadata']]): schedules.add(entry['schedule_id'])
    for row in WorkResourceAssignment.objects.filter(company=proposal.company).values('version_id','schedule_id','equipment_type_id','position_id','crew_snapshot'):
        if (proposal.kind=='equipment' and row['equipment_type_id']==identity or
                position_id and row['position_id']==position_id or contains(row['crew_snapshot'])):
            if row['version_id']: versions.add(row['version_id'])
            if row['schedule_id']: schedules.add(row['schedule_id'])
    owners = []
    for model, ids in [(GlobalPlanVersion,versions),(DailySchedule,schedules)]:
        query = model.objects.filter(company=proposal.company,pk__in=ids).order_by('pk')
        if lock: query = query.select_for_update()
        owners.extend(query)
    return owners


def fingerprint(proposal, owners):
    data = []
    for owner in owners:
        resources = list(owner.entries.values()) if isinstance(owner, DailySchedule) else list(owner.resource_allocations.values())
        from apps.planning.resource_links import binding_values
        from apps.planning.resource_targets import assignment_values
        data.append([owner_key(owner), owner.status, resources, assignment_values(owner), binding_values(owner)])
    return hashlib.sha256(json.dumps(data, sort_keys=True, default=str).encode()).hexdigest()


def preview(user, proposal, target):
    require_roles(user, {'ADMIN', REVIEW_ROLES[proposal.kind]})
    if proposal.company_id != user.company_id: raise PermissionDenied
    validate_target(proposal, target)
    owners = affected_owners(proposal)
    payload = {'user': user.pk, 'company': user.company_id, 'request': proposal.pk,
               'revision': proposal.revision, 'target': target.pk, 'fingerprint': fingerprint(proposal, owners), 'catalogue_stamp': catalogue_stamp(proposal,target)}
    return signing.dumps(payload, salt=SALT), owners


def catalogue_stamp(proposal, target):
    model = type(target)
    current = model.objects.filter(company=proposal.company,pk__in=[proposal.resource.pk,target.pk]).order_by('pk').values('pk','name','unit','is_active','updated_at')
    return hashlib.sha256(json.dumps(list(current),sort_keys=True,default=str).encode()).hexdigest()


def validate_target(proposal, target):
    if target is None or not confirmed_choices(proposal.company, proposal.kind).filter(pk=target.pk).exists():
        raise ValidationError('Выберите подтверждённую позицию своего справочника.')
    if proposal.resource.unit.strip().casefold() != target.unit.strip().casefold():
        raise ValidationError('Единицы измерения исходной и выбранной позиции должны совпадать.')


def rewrite(value, proposal, target):
    old = proposal.brigade_id if proposal.kind == 'labor' else proposal.equipment_type_id
    key = 'brigade_id' if proposal.kind == 'labor' else 'equipment_type_id'
    position_ids = None
    if proposal.kind == 'labor':
        from .position_catalog import position_for
        position_ids = (position_for(proposal.resource).pk, position_for(target).pk)
    def walk(item):
        if isinstance(item, list): return [walk(v) for v in item]
        if not isinstance(item, dict): return item
        row = {k:walk(v) for k,v in item.items()}
        if str(row.get(key)) == str(old):
            row[key] = target.pk
            for label in ['label', 'name']:
                if label in row: row[label] = target.name
        if position_ids and str(row.get('position_id')) == str(position_ids[0]): row['position_id'] = position_ids[1]
        return row
    return walk(deepcopy(value))


def merge_day_rows(rows, kind):
    grouped = {}
    count_field = 'planned_count'
    hours_field = 'planned_hours' if kind == 'labor' else 'planned_machine_hours'
    for row in rows:
        key = (row['date'], row.get('assignment_key'))
        if key not in grouped: grouped[key] = deepcopy(row); continue
        other = grouped[key]
        a, b = Decimal(str(other.get(hours_field, 0))), Decimal(str(row.get(hours_field, 0)))
        if a + b:
            other['hourly_rate'] = str((a * Decimal(str(other.get('hourly_rate',0))) + b * Decimal(str(row.get('hourly_rate',0)))) / (a+b))
        other[count_field] = str(Decimal(str(other.get(count_field,other.get('planned_workers',0)))) + Decimal(str(row.get(count_field,row.get('planned_workers',0)))))
        if kind == 'labor': other['planned_workers'] = other[count_field]
        other[hours_field] = str(a+b)
        other['table_crew_count'] = int(other.get('table_crew_count',0)) + int(row.get('table_crew_count',0))
    return list(grouped.values())


def replace_owner(owner, proposal, target):
    """Change resource identities only; keep quantities, dates, work links and crews."""
    from apps.planning.models import ResourceMonthAllocation
    from apps.planning.resource_links import copy_links
    kind = proposal.kind
    old = proposal.brigade_id if kind == 'labor' else proposal.equipment_type_id
    field = 'brigade' if kind == 'labor' else 'equipment_type'
    if isinstance(owner, GlobalPlanVersion):
        from apps.planning.bulk_add import editable_months
        allowed = set(editable_months(owner))
        if owner.resource_allocations.filter(**{field+'_id':old}).exclude(month__in=allowed).exists():
            raise ValidationError('Позиция используется в зафиксированном месяце. Сохранённый период нельзя заменить через справочник.')
        for frozen_row in owner.resource_allocations.exclude(month__in=allowed):
            if rewrite(frozen_row.crew_data,proposal,target) != frozen_row.crew_data:
                raise ValidationError('Сохранённый экипаж в зафиксированном месяце нельзя заменить через справочник.')
        for row in owner.resource_allocations.select_for_update().filter(**{field+'_id':old}):
            lookup = {'month':row.month,'kind':row.kind, field:target}
            if kind == 'equipment': lookup['equipment_number'] = row.equipment_number
            existing = owner.resource_allocations.filter(**lookup).exclude(pk=row.pk).first()
            if existing:
                if kind=='equipment' and existing.crew_data.get('members') and row.crew_data.get('members') and existing.crew_data['members'] != row.crew_data['members']:
                    raise ValidationError('У строк техники различается сохранённый экипаж. Сначала согласуйте состав экипажа в плане.')
                total = existing.hours + row.hours
                if total: existing.rate = ((existing.rate*existing.hours + row.rate*row.hours)/total).quantize(Decimal('.01'))
                existing.count += row.count; existing.hours = total
                if kind == 'labor': existing.crew_data = {'count':int(existing.crew_data.get('count',0)) + int(row.crew_data.get('count',0))}
                elif not existing.crew_data.get('members') and row.crew_data.get('members'): existing.crew_data = row.crew_data
                existing.full_clean(); existing.save()
                copy_links(row, existing, owner); row.delete()
            else:
                setattr(row, field, target); row.full_clean(); row.save()
        for row in owner.resource_allocations.all():
            changed = rewrite(row.crew_data, proposal, target)
            if changed != row.crew_data: row.crew_data = changed; row.save(update_fields=['crew_data','updated_at'])
    else:
        # Keep explicit assignment entries separate; merge matching table identities.
        from apps.planning.workspace_services import resource_identity
        by_identity = {}
        frozen = {'rows':{},'original':{}}
        if owner.locked_before:
            for assignment in owner.resource_assignments.filter(start_date__lt=owner.locked_before):
                if (kind == 'equipment' and assignment.equipment_type_id == old or kind == 'labor' and (assignment.position_id == proposal.resource.position_mapping.position_id or rewrite(assignment.crew_snapshot,proposal,target) != assignment.crew_snapshot)):
                    raise ValidationError('Назначение затрагивает закрытые дни. Сохранённую историю нельзя заменить через справочник.')
            for entry in owner.entries.filter(kind=kind).exclude(key__startswith='assignment:'):
                for name in frozen:
                    for row in getattr(entry,name):
                        if row['date'] >= owner.locked_before.isoformat(): continue
                        if str(row.get(field+'_id')) == str(old) and any(Decimal(str(row.get(k,0))) for k in ['planned_count','planned_workers','planned_hours','planned_machine_hours']):
                            raise ValidationError('В закрытых днях есть значения этой позиции. Сохранённую историю нельзя заменить через справочник.')
                        if str(row.get(field+'_id')) == str(target.pk): frozen[name][row['date']] = deepcopy(row)
        for entry in list(owner.entries.exclude(kind='work').order_by('pk')):
            original = [entry.rows, entry.original, entry.metadata]
            updated = rewrite(original, proposal, target)
            if updated != original:
                entry.rows, entry.original, entry.metadata = updated
                if entry.kind == kind and any(str(r.get(field+'_id')) == str(target.pk) for r in entry.rows+entry.original): entry.label = target.name
                entry.revision += 1
                entry.save(update_fields=['rows','original','metadata','label','revision'])
            if entry.kind != kind or entry.key.startswith('assignment:'): continue
            first = next(iter(entry.rows or entry.original), {})
            if str(first.get(field+'_id')) != str(target.pk): continue
            identity = resource_identity(first, kind)
            existing = by_identity.get(identity)
            if existing:
                if (kind == 'equipment' and existing.metadata.get('table_crew') and entry.metadata.get('table_crew')
                        and existing.metadata['table_crew'] != entry.metadata['table_crew']):
                    raise ValidationError('У строк техники различается сохранённый экипаж. Сначала согласуйте состав экипажа в плане.')
                existing.rows = merge_day_rows(existing.rows+entry.rows, kind)
                existing.original = merge_day_rows(existing.original+entry.original, kind)
                existing.metadata['table_crew_counts'] = {day:int(existing.metadata.get('table_crew_counts',{}).get(day,0))+int(entry.metadata.get('table_crew_counts',{}).get(day,0)) for day in set(existing.metadata.get('table_crew_counts',{}))|set(entry.metadata.get('table_crew_counts',{}))}
                if not existing.metadata.get('table_crew') and entry.metadata.get('table_crew'): existing.metadata['table_crew'] = entry.metadata['table_crew']
                existing.revision += 1
                existing.save(update_fields=['rows','original','metadata','revision'])
                copy_links(entry, existing, owner); entry.delete()
            else: by_identity[identity] = entry
        for entry in by_identity.values():
            for name, closed in frozen.items():
                setattr(entry,name,[deepcopy(closed.get(row['date'],row)) for row in getattr(entry,name)])
            entry.save(update_fields=['rows','original'])
            entry.key = f'catalogue-review:{proposal.pk}:{entry.pk}'; entry.save(update_fields=['key'])
        for identity, entry in by_identity.items():
            entry.key = kind+':'+hashlib.sha256(json.dumps(identity).encode()).hexdigest(); entry.save(update_fields=['key'])
    for assignment in owner.resource_assignments.select_related('position'):
        changes = []
        if kind == 'equipment' and assignment.equipment_type_id == old:
            assignment.equipment_type = target; changes.append('equipment_type')
        if kind == 'labor' and assignment.position_id:
            from .position_catalog import position_for
            if assignment.position_id == position_for(proposal.resource).pk:
                assignment.position = position_for(target); changes.append('position')
        crew = rewrite(assignment.crew_snapshot, proposal, target)
        if crew != assignment.crew_snapshot: assignment.crew_snapshot = crew; changes.append('crew_snapshot')
        if changes: assignment.save(update_fields=[*changes,'updated_at'])
    invalidate(owner)


@transaction.atomic
def decide(user, proposal, action, revision, comment='', target=None, token=None):
    require_roles(user, {'ADMIN', REVIEW_ROLES[proposal.kind]})
    if proposal.company_id != user.company_id: raise PermissionDenied
    owners = affected_owners(proposal, lock=True)
    proposal = CatalogueReviewRequest.objects.select_for_update().get(pk=proposal.pk, company=user.company)
    if proposal.status != 'PENDING' or proposal.revision != int(revision): raise ValidationError('Заявка уже изменена. Обновите страницу.')
    if any(o.status not in {'DRAFT','REJECTED'} for o in owners): raise ValidationError('Позиция используется в зафиксированном плане. Изменять его через справочник нельзя.')
    summary_before = replacement_summary(proposal,target,owners) if action == 'replace' and target else []
    if action == 'replace':
        validate_target(proposal, target)
        try: payload = signing.loads(token or '', salt=SALT, max_age=1800)
        except signing.BadSignature: raise ValidationError('Выполните предпросмотр замены заново.')
        expected = {'user':user.pk,'company':user.company_id,'request':proposal.pk,'revision':proposal.revision,
                    'target':target.pk,'fingerprint':fingerprint(proposal, owners),'catalogue_stamp':catalogue_stamp(proposal,target)}
        if payload != expected: raise ValidationError('План изменился после предпросмотра. Проверьте замену заново.')
        for owner in owners: replace_owner(owner, proposal, target)
        proposal.status = 'REPLACED'
        if proposal.kind == 'labor': proposal.target_brigade = target
        else: proposal.target_equipment = target
    elif action == 'confirm':
        resource = proposal.resource; resource.is_active = True; resource.save(update_fields=['is_active'])
        proposal.status = 'CONFIRMED'
    elif action == 'reject':
        if not comment.strip(): raise ValidationError('Укажите причину отказа.')
        proposal.status = 'REJECTED'
    else: raise ValidationError('Неизвестное решение.')
    proposal.comment = comment.strip(); proposal.reviewed_by = user; proposal.reviewed_at = timezone.now(); proposal.revision += 1
    summary = [{'plan':list(owner_key(row['owner'])),'period':row['period'],'source':str(row['source']),'target':str(row['target']),'total':str(row['total'])} for row in summary_before]
    proposal.history = [*proposal.history, {'summary':summary,'action':action,'actor':user.pk,'name':user.full_name,
        'at':proposal.reviewed_at.isoformat(),'comment':proposal.comment,'target':target.pk if target else None,
        'plans':[list(owner_key(o)) for o in owners]}]
    proposal.save()
    from .catalogue_tasks import sync_task
    sync_task(proposal)
    audit(user, proposal, 'UPDATE', {'status':proposal.status,'comment':proposal.comment,'target':target.pk if target else None})
    return proposal


def replacement_summary(proposal, target, owners):
    """Quantities shown before a decision: per month for GPR, per day for SMG."""
    result = []
    field = 'brigade_id' if proposal.kind == 'labor' else 'equipment_type_id'
    old = proposal.brigade_id if proposal.kind == 'labor' else proposal.equipment_type_id
    for owner in owners:
        periods = {}
        if isinstance(owner, GlobalPlanVersion):
            for row in owner.resource_allocations.filter(kind=proposal.kind, **{field+'__in':[old,target.pk]}):
                key = row.month.strftime('%m.%Y')
                values = periods.setdefault(key, [Decimal(0),Decimal(0)])
                values[int(getattr(row,field)==target.pk)] += row.count
        else:
            for entry in owner.entries.filter(kind=proposal.kind):
                for row in entry.rows:
                    identity = row.get(field)
                    if identity not in {old,target.pk}: continue
                    values = periods.setdefault(row['date'],[Decimal(0),Decimal(0)])
                    values[int(identity==target.pk)] += Decimal(str(row.get('planned_count',row.get('planned_workers',0))))
        for period, values in sorted(periods.items()):
            result.append({'owner':owner,'period':period,'source':values[0],'target':values[1],'total':sum(values)})
    return result
