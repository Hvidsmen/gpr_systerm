"""Administrator editing and shared historical opening-quantity readers."""
from datetime import timedelta
import hashlib
import json
from django.core.exceptions import ValidationError
from django.db import transaction, connection
from django.db.models import F
from .entry_concurrency import sqlite_serialized
from core.permissions import require_roles
from core.fact_history import history_actor
from apps.works.models import ProjectWork
from .models import WorkOpeningBalance


def opening_rows(company, work_ids, until=None):
    """Return stock just before accounting starts; never create a daily fact."""
    rows = WorkOpeningBalance.objects.filter(company=company, project_work_id__in=work_ids)
    if until is not None:
        # A balance on the morning of D is the cumulative stock at the end of D-1.
        from datetime import date
        if until < date.max:
            rows = rows.filter(date__lte=until + timedelta(days=1))
    return [(r.project_work_id, r.date - timedelta(days=1), r.work_item_id, r.quantity) for r in rows.order_by('date', 'pk')]


def revision(work):
    value = [work.pk, work.kind, work.unit, work.allow_fractional, str(work.updated_at),
        list(work.items.order_by('pk').values_list('pk', 'unit', 'quantity_per_unit', 'updated_at')),
        list(work.opening_balances.order_by('pk').values_list('pk', 'date', 'work_item_id', 'quantity', 'updated_at'))]
    return hashlib.sha256(json.dumps(value, default=str).encode()).hexdigest()


@sqlite_serialized
@transaction.atomic
def save(user, work_id, values, expected, reason):
    require_roles(user, {'ADMIN'})
    if connection.vendor == 'sqlite':
        ProjectWork.objects.filter(company=user.company, pk=work_id).update(name=F('name'))
    work = ProjectWork.objects.select_for_update().get(company=user.company, pk=work_id)
    list(work.items.select_for_update().order_by('pk'))
    existing = {r.work_item_id: r for r in work.opening_balances.select_for_update()}
    if revision(work) != expected:
        raise ValidationError('Остатки или состав работы изменились. Обновите страницу перед сохранением.')
    if hasattr(work, 'merged_source'):
        raise ValidationError('Исходная работа архивная. Откройте остатки комплекса.')
    keys = set(work.items.values_list('pk', flat=True)) if work.kind == 'COMPOSITE' else {None}
    if set(values) != keys or not keys:
        raise ValidationError('Состав работы изменился. Обновите страницу.')
    if not reason.strip():
        raise ValidationError('Укажите основание ввода или исправления.')
    with history_actor(user, reason.strip()):
        for item_id, (day, quantity) in values.items():
            row = existing.get(item_id)
            if not row and not quantity:
                continue
            if row and (row.date, row.quantity) == (day, quantity):
                continue
            row = row or WorkOpeningBalance(company=user.company, project_work=work, work_item_id=item_id)
            row.date, row.quantity = day, quantity
            row.full_clean()
            row.save()
    # Fresh drafts must rebuild their completion seed; frozen approved snapshots remain intact.
    from apps.planning.models import GlobalPlanVersion
    GlobalPlanVersion.objects.filter(company=user.company, status__in=['DRAFT', 'REJECTED'],
        work_allocations__work=work).update(snapshot={})
    return work
