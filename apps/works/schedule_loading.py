"""Scoped matching and atomic confirmation for work schedule imports."""
from collections import defaultdict
from datetime import date
from decimal import Decimal
import hashlib
import json
from django.core.exceptions import ValidationError
from django.db import transaction, connection
from django.db.models import F
from django.shortcuts import get_object_or_404
from core.permissions import PLAN_ROLES, require_roles, require_planning_object, scope_queryset
from apps.projects.models import ConstructionObject
from apps.audit.models import AuditLog
from apps.production.entry_concurrency import sqlite_serialized
from .models import ProjectWork


def key(value):
    return ' '.join(str(value or '').split()).casefold()


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def catalogue_revision(work):
    return digest([work.pk, work.name, work.kind, work.unit, work.allow_fractional, work.section_id,
        work.complex_object_id, work.complex_object.name if work.complex_object_id else '', work.rd_section, work.rdc_point,
        str(work.updated_at), [(i.pk, i.name, i.unit, str(i.quantity_per_unit), str(i.updated_at), i.parent_id) for i in work.items.all()]])


def match_rows(user, obj, rows, section=None):
    from .unit_aliases import aliases
    from .planning_access import planning_works
    works = planning_works(scope_queryset(ProjectWork.objects.filter(section__construction_object=obj, merged_source__isnull=True), user), user, whole=True)
    if section:
        works = works.filter(section=section)
    works = works.filter(is_legacy_structure=False)
    works = list(works.select_related('complex_object', 'section').prefetch_related('items'))
    index = defaultdict(list)
    versions = {}
    for work in works:
        if work.kind != 'COMPOSITE' or not work.complex_object_id:
            continue
        versions[work.pk] = catalogue_revision(work)
        for item in work.items.all():
            if item.company_id == user.company_id and item.parent_id is None and not item.is_legacy_structure:
                index[(key(work.complex_object.name), key(work.name), key(item.name))].append((work, item))
    unit_aliases = aliases(user.company)
    result = []
    for source in rows:
        row = dict(source, issue='')
        found = index[(key(row['object']), key(row['package']), key(row['work']))]
        if row.get('section'):found=[pair for pair in found if key(pair[0].section.name)==key(row['section'])]
        if not found:
            row['issue'] = 'Не найдена в справочнике выбранного объекта. Сначала загрузите состав работ.'
        elif len(found) != 1:
            row['issue'] = 'Несколько совпадений. Выберите раздел для точного сопоставления.'
        else:
            work, item = found[0]
            row.update(work_id=work.pk, item_id=item.pk, catalogue_revision=versions[work.pk])
            if (key(work.rd_section), key(work.rdc_point)) != (key(row['rd_section']), key(row['rdc_point'])):
                row['issue'] = 'Реквизиты РД / РДЦ отличаются от справочника.'
            elif key(item.unit) != key(unit_aliases.get(row['unit'], row['unit'])):
                row['issue'] = 'Единица измерения отличается от справочника.'
            elif item.quantity_per_unit != Decimal(row['quantity']):
                row['issue'] = 'Норматив G отличается от справочника. План и ВНО не меняют нормативы.'
        result.append(row)
    return result


def checked_rows(user, obj, data):
    from apps.projects.models import Section
    section = None
    if data.get('section'):
        section = get_object_or_404(scope_queryset(Section.objects.all(), user), pk=data['section'], construction_object=obj)
    current = match_rows(user, obj, data['rows'], section)
    for before, after in zip(data['rows'], current):
        if after['issue'] or any(before.get(field) != after.get(field) for field in ('work_id', 'item_id', 'catalogue_revision')):
            raise ValidationError('Справочник или доступ к работам изменился. Повторите предпросмотр.')
    return current


def plan_revision(plan):
    return digest([plan.pk, plan.updated_at, list(plan.rows.order_by('pk').values_list('pk', 'work_id', 'work_item_id', 'quantity', 'updated_at'))])


def locked_object(user, pk):
    obj = get_object_or_404(scope_queryset(ConstructionObject.objects.all(), user), pk=pk)
    require_planning_object(user, obj.pk)
    if connection.vendor == 'sqlite':
        ConstructionObject.objects.filter(pk=obj.pk, company=user.company).update(name=F('name'))
    return ConstructionObject.objects.select_for_update().get(pk=obj.pk, company=user.company)


def receipt(user, mode, nonce):
    return AuditLog.objects.filter(user=user, entity_type='works.schedule_' + mode, new_value__nonce=nonce).first()


def record(user, mode, target, data, changed):
    AuditLog.objects.create(user=user, action='UPDATE', entity_type='works.schedule_' + mode,
        entity_id=str(target), new_value={'nonce': data['nonce'], 'object': data['object'], 'file': data['filename'], 'changed': changed},
        comment=('Импорт объёмов плана на весь объект из H / План' if mode == 'plan' else 'Импорт входящего факта из H / Факт'))


@sqlite_serialized
@transaction.atomic
def apply_plan(user, data):
    from apps.planning.models import ObjectWorkPlan, ObjectWorkPlanRow
    from .progress import quantity_from_totals, work_specification
    require_roles(user, PLAN_ROLES)
    obj = locked_object(user, data['object'])
    old_receipt = receipt(user, 'plan', data['nonce'])
    if old_receipt:
        return get_object_or_404(scope_queryset(ObjectWorkPlan.objects.all(), user), pk=old_receipt.entity_id), False
    rows = checked_rows(user, obj, data)
    if data.get('plan'):
        plan = get_object_or_404(scope_queryset(ObjectWorkPlan.objects.select_for_update(), user), pk=data['plan'], construction_object=obj)
        if plan_revision(plan) != data['plan_revision']:
            raise ValidationError('План изменился после предпросмотра. Повторите загрузку файла.')
    else:
        plan = ObjectWorkPlan(company=user.company, construction_object=obj, name=data['name'], created_by=user)
        plan.full_clean(); plan.save()
    changed = 0
    touched = set()
    for row in rows:
        touched.add(row['work_id'])
        target = plan.rows.filter(work_id=row['work_id'], work_item_id=row['item_id']).first()
        value = Decimal(row['volume'])
        if target and target.quantity == value:
            continue
        target = target or ObjectWorkPlanRow(company=user.company, plan=plan, work_id=row['work_id'], work_item_id=row['item_id'])
        target.quantity = value; target.full_clean(); target.save(); changed += 1
    # A package count is derived from the imported component volumes, never from
    # G (the catalogue norm) or a daily/monthly allocation.
    for work in ProjectWork.objects.filter(pk__in=touched, company=user.company).prefetch_related('items'):
        totals = {r.work_item_id: r.quantity for r in plan.rows.filter(work=work, work_item__isnull=False)}
        value = quantity_from_totals(work_specification(work), totals)
        parent = plan.rows.filter(work=work, work_item__isnull=True).first()
        if not parent or parent.quantity != value:
            parent = parent or ObjectWorkPlanRow(company=user.company, plan=plan, work=work)
            parent.quantity = value; parent.full_clean(); parent.save()
    plan.save(update_fields=['updated_at'])
    record(user, 'plan', plan.pk, data, changed)
    return plan, True


@sqlite_serialized
@transaction.atomic
def apply_opening(user, data):
    from apps.production import work_opening
    require_roles(user, {'ADMIN'})
    obj = locked_object(user, data['object'])
    if receipt(user, 'opening', data['nonce']):
        return False
    rows = checked_rows(user, obj, data)
    groups = defaultdict(list)
    for row in rows:
        groups[row['work_id']].append(row)
    day = date.fromisoformat(data['date'])
    for work in ProjectWork.objects.filter(pk__in=groups, company=user.company).order_by('pk'):
        expected = data['opening_revisions'][str(work.pk)]
        current = {r.work_item_id: (r.date, r.quantity) for r in work.opening_balances.all()}
        values = {i.pk: current.get(i.pk, (day, Decimal(0))) for i in work.items.all()}
        for row in groups[work.pk]:
            values[row['item_id']] = (day, Decimal(row['volume']))
        work_opening.save(user, work.pk, values, expected, data['reason'])
    record(user, 'opening', obj.pk, data, len(rows))
    return True
