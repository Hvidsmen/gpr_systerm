"""Validated operational changes and read-only daily fact preparation."""

from core.fact_history import with_history_actor
from core.fact_edit_window import require_fact_day
import hashlib
import json
from datetime import timedelta
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import F, Q, OuterRef, Subquery, Exists
from django.utils import timezone
from core.permissions import require_roles, scope_queryset
from apps.projects.models import ConstructionObject
from apps.resources.models import Equipment
from .models import EquipmentAssignment, EquipmentStateChange, EquipmentActivity, EmployeeActivity, FactSectionDay

OPERATORS = {'ADMIN', 'MECHANIZATION_DISPATCHER', 'ALL_FACT_DISPATCHER'}


def _authorize(actor, obj):
    require_roles(actor, OPERATORS)
    if not scope_queryset(ConstructionObject.objects.all(), actor).filter(pk=obj.pk).exists():
        raise PermissionDenied('Объект не закреплён за пользователем.')


def _lock(obj, equipment_id):
    # Same order as daily fact saving: object, then machines. First write also
    # serializes SQLite transactions, where SELECT FOR UPDATE has no effect.
    ConstructionObject.objects.filter(pk=obj.pk, company_id=obj.company_id).update(name=F('name'))
    return Equipment.objects.select_for_update().get(pk=equipment_id, company_id=obj.company_id)


def _audit(actor, record, old, new, comment):
    from apps.audit.models import AuditLog
    AuditLog.objects.create(user=actor, action='UPDATE' if old else 'CREATE', entity_type=record._meta.label,
        entity_id=str(record.pk), old_value=old, new_value=new, comment=comment)


def _values(record):
    names = ['equipment_id', 'construction_object_id', 'start_date', 'end_date', 'shift', 'general',
             'work_id', 'work_item_id', 'activity', 'revision'] if isinstance(record, EquipmentAssignment) else [
             'equipment_id', 'assignment_id', 'effective_date', 'shift', 'state', 'reason']
    return {name: value.isoformat() if hasattr(value, 'isoformat') else value for name in names for value in [getattr(record, name)]}


def _state(actor, assignment, day, state, reason):
    if day > timezone.localdate():
        raise ValidationError('Состояние техники фиксируется на текущую или прошедшую дату.')
    record = EquipmentStateChange.objects.filter(equipment=assignment.equipment, effective_date=day, shift=assignment.shift, is_cancelled=False).first()
    old = _values(record) if record else None
    if record and record.assignment_id != assignment.pk:
        raise ValidationError('На эту дату уже записано состояние по другому назначению. Проверьте историю техники.')
    record = record or EquipmentStateChange(company=assignment.company, equipment=assignment.equipment,
        assignment=assignment, effective_date=day, shift=assignment.shift)
    record.state, record.reason, record.recorded_by = state, reason.strip(), actor
    record.full_clean(); record.save()
    _audit(actor, record, old, _values(record), 'Изменение состояния техники с указанной даты и смены')
    return record


@with_history_actor
def create_assignment(actor, assignment, state='KEEP', reason=''):
    _authorize(actor, assignment.construction_object)
    with transaction.atomic():
        machine = _lock(assignment.construction_object, assignment.equipment_id)
        if not machine.is_active:
            raise ValidationError('Нельзя создать назначение для неактивной техники.')
        if assignment.start_date and assignment.start_date > timezone.localdate():
            raise ValidationError('Для действующего назначения выберите текущую или прошедшую дату.')
        require_fact_day(actor,assignment.start_date)
        assignment.company = actor.company
        assignment.created_by = actor
        assignment.full_clean()
        # Existing daily details and crews remain authoritative locations.
        for model in [EquipmentActivity, EmployeeActivity]:
            conflicts = model.objects.filter(equipment=machine, date__gte=assignment.start_date, shift=assignment.shift).exclude(construction_object=assignment.construction_object)
            if assignment.end_date:
                conflicts = conflicts.filter(date__lte=assignment.end_date)
            if conflicts.exists():
                raise ValidationError('На этот период уже есть факт техники или экипажа на другом объекте.')
        assignment.save()
        if state == 'KEEP':
            boundary = Q(effective_date__lt=assignment.start_date) | Q(effective_date=assignment.start_date, shift__lte=assignment.shift)
            recorded = EquipmentStateChange.objects.filter(company=assignment.company, equipment=machine,
                is_cancelled=False, assignment__cancelled_at__isnull=True).filter(boundary).exists()
            if not recorded:
                confirmed = FactSectionDay.objects.filter(company=assignment.company,
                    construction_object_id=OuterRef('construction_object_id'), date=OuterRef('date'),
                    section='mechanization', confirmed_at__isnull=False)
                fact = EquipmentActivity.objects.filter(company=assignment.company, equipment=machine).filter(
                    Q(date__lt=assignment.start_date) | Q(date=assignment.start_date, shift__lte=assignment.shift)).annotate(
                    day_confirmed=Exists(confirmed)).filter(day_confirmed=True).order_by('-date', '-shift', '-pk').first()
                _state(actor, assignment, assignment.start_date, fact.state if fact else 'WORKING', fact.activity if fact else '')
        else:
            _state(actor, assignment, assignment.start_date, state, reason)
        _audit(actor, assignment, None, _values(assignment), 'Создано действующее назначение техники')
        return assignment


def _current(actor, assignment_id, revision):
    record = scope_queryset(EquipmentAssignment.objects.select_related('equipment', 'construction_object'), actor).get(pk=assignment_id)
    if record.revision != revision:
        raise ValidationError('Назначение изменилось в другой вкладке. Обновите страницу.')
    if record.cancelled_at:
        raise ValidationError('Назначение уже отменено.')
    return record


@with_history_actor
def change_state(actor, assignment, revision, day, state, reason):
    _authorize(actor, assignment.construction_object)
    with transaction.atomic():
        _lock(assignment.construction_object, assignment.equipment_id)
        current = _current(actor, assignment.pk, revision)
        require_fact_day(actor,day)
        record = _state(actor, current, day, state, reason)
        current.revision += 1
        current.save(update_fields=['revision', 'updated_at'])
        return record


@with_history_actor
def close_assignment(actor, assignment, revision, last_day):
    _authorize(actor, assignment.construction_object)
    with transaction.atomic():
        _lock(assignment.construction_object, assignment.equipment_id)
        current = _current(actor, assignment.pk, revision)
        require_fact_day(actor,last_day)
        if current.end_date and last_day > current.end_date:
            raise ValidationError('Чтобы продлить завершённый период, создайте новое назначение.')
        if current.state_changes.filter(effective_date__gt=last_day).exists():
            raise ValidationError('Позже этой даты уже есть изменения состояния. Выберите последний день с учётом истории.')
        old = _values(current)
        current.end_date = last_day; current.revision += 1
        current.full_clean(); current.save()
        _audit(actor, current, old, _values(current), 'Завершено назначение техники')
        return current


@with_history_actor
def change_assignment(actor, assignment, revision, day, *, work, work_item, general, activity):
    _authorize(actor, assignment.construction_object)
    with transaction.atomic():
        _lock(assignment.construction_object, assignment.equipment_id)
        current = _current(actor, assignment.pk, revision)
        require_fact_day(actor,day)
        if day > timezone.localdate() or day < current.start_date or current.end_date and day > current.end_date:
            raise ValidationError('Выберите текущую или прошедшую дату внутри назначения.')
        old = _values(current); previous_end = current.end_date
        if day > current.start_date:
            current.end_date = day-timedelta(days=1); current.revision += 1
            current.save(update_fields=['end_date', 'revision', 'updated_at'])
            _audit(actor, current, old, _values(current), 'Предыдущий период назначения завершён перед изменением работы')
            changed = EquipmentAssignment(company=current.company, equipment=current.equipment,
                construction_object=current.construction_object, start_date=day, end_date=previous_end,
                shift=current.shift, created_by=actor)
        else:
            changed = current; changed.revision += 1
        changed.work, changed.work_item, changed.general, changed.activity = work, work_item, general, activity
        changed.full_clean(); changed.save()
        if changed.pk != current.pk:
            current.state_changes.filter(effective_date__gte=day).update(assignment=changed)
        _audit(actor, changed, old if changed.pk == current.pk else None, _values(changed), 'Изменено назначение работы с указанного дня')
        return changed


@with_history_actor
def cancel_assignment(actor, assignment, revision, reason):
    _authorize(actor, assignment.construction_object)
    with transaction.atomic():
        _lock(assignment.construction_object, assignment.equipment_id)
        current = _current(actor, assignment.pk, revision)
        if not reason.strip():
            raise ValidationError('Укажите причину отмены ошибочного назначения.')
        require_fact_day(actor,current.start_date)
        own = current.daily_facts.all()
        confirmed = FactSectionDay.objects.filter(company=current.company,
            construction_object=current.construction_object, section='mechanization',
            date__in=own.values('date'), confirmed_at__isnull=False).exists()
        used_elsewhere = EquipmentActivity.objects.filter(source_state__assignment=current).exclude(standing_assignment=current).exists()
        if current.state_changes.filter(is_cancelled=False).exists():
            used_elsewhere = used_elsewhere or EquipmentAssignment.objects.filter(equipment=current.equipment,
                cancelled_at__isnull=True).exclude(pk=current.pk).filter(
                Q(end_date__isnull=True) | Q(end_date__gte=current.start_date)).exists()
        if confirmed or used_elsewhere:
            raise ValidationError('Назначение или его состояние уже используется в подтверждённом факте либо другом назначении. Завершите период или внесите исправление в факт.')
        old = _values(current)
        removed = [{key: value.isoformat() if hasattr(value, 'isoformat') else value for key, value in row.items()}
            for row in own.values('id', 'date', 'shift', 'state', 'general', 'work_id', 'work_item_id', 'activity', 'source_state_id')]
        dates = {row['date'] for row in removed}
        own.delete()
        current.cancelled_at, current.cancel_reason = timezone.now(), reason.strip()
        current.revision += 1
        current.save(update_fields=['cancelled_at', 'cancel_reason', 'revision', 'updated_at'])
        current.state_changes.update(is_cancelled=True)
        if dates:
            FactSectionDay.objects.filter(company=current.company,construction_object=current.construction_object,
                date__in=dates,section='mechanization').update(revision=F('revision')+1)
            from .dispatch import project_resources
            from datetime import date
            for day in dates:
                project_resources(current.construction_object, date.fromisoformat(day), 'mechanization')
        _audit(actor, current, old, {**_values(current), 'cancelled_at': current.cancelled_at.isoformat(),
            'cancel_reason': current.cancel_reason, 'removed_draft_facts': removed}, 'Отменено ошибочное назначение, история сохранена')


def prepare(obj, day):
    """No fact writes: return effective baselines and an optimistic form version."""
    last_state = EquipmentStateChange.objects.filter(company=obj.company,equipment_id=OuterRef('equipment_id'), is_cancelled=False,assignment__cancelled_at__isnull=True).filter(
        Q(effective_date__lt=day) | Q(effective_date=day, shift__lte=OuterRef('shift'))).order_by('-effective_date', '-shift', '-pk')
    assignments = list(EquipmentAssignment.objects.filter(company=obj.company, construction_object=obj, cancelled_at__isnull=True,
        start_date__lte=day).filter(Q(end_date__isnull=True) | Q(end_date__gte=day)).annotate(
        effective_state_id=Subquery(last_state.values('pk')[:1])).select_related('equipment__type__parent__category','equipment__type__category','equipment__catalogue_group', 'work', 'work_item'))
    states = EquipmentStateChange.objects.in_bulk([row.effective_state_id for row in assignments if row.effective_state_id])
    baselines = {}
    versions = []
    for assignment in assignments:
        record = states.get(assignment.effective_state_id)
        state = record.state if record else 'WORKING'
        values = dict(equipment=assignment.equipment_id, shift=assignment.shift, state=state,
            general=assignment.general if state == 'WORKING' else False,
            work=assignment.work_id if state == 'WORKING' else None,
            work_item=assignment.work_item_id if state == 'WORKING' else None,
            activity=assignment.activity if state == 'WORKING' else record.reason)
        baselines[(assignment.equipment_id, assignment.shift)] = {'assignment': assignment, 'state_record': record, 'values': values}
        versions.append([assignment.pk, assignment.revision, assignment.updated_at.isoformat(),
            record.pk if record else None, record.updated_at.isoformat() if record else None, values])
    version = hashlib.sha256(json.dumps(versions, sort_keys=True).encode()).hexdigest()
    return baselines, version
