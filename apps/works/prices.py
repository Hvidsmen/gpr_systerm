"""Effective dated prices, immutable audit entries and frozen planning prices."""

from datetime import date
from decimal import Decimal
from copy import deepcopy
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from core.permissions import PLAN_ROLES, require_roles, role_code


def can_backdate_price(user):
    return bool(user and user.is_authenticated and role_code(user) == 'ADMIN')


def history_prices(work):
    return list(work.price_history.order_by("effective_from", "pk"))


def rate_on(entries, day, fallback=Decimal(0)):
    value = Decimal(fallback)
    for entry in entries:
        effective = (
            entry["effective_from"] if isinstance(entry, dict) else entry.effective_from
        )
        if isinstance(effective, str):
            effective = date.fromisoformat(effective)
        if effective > day:
            break
        value = Decimal(entry["price"] if isinstance(entry, dict) else entry.price)
    return value


def price_on(work, day=None):
    return rate_on(history_prices(work), day or timezone.localdate(), work.unit_price)


def freeze_prices(snapshot, company, *, in_place=False):
    from .models import WorkPrice

    result = snapshot if in_place else deepcopy(snapshot)
    entries = {}
    for entry in WorkPrice.objects.filter(
        company=company, work_id__in=[s["id"] for s in result.get("works", [])]
    ).order_by("effective_from", "pk"):
        entries.setdefault(entry.work_id, []).append(
            {
                "effective_from": entry.effective_from.isoformat(),
                "price": str(entry.price),
                "id": entry.pk,
            }
        )
    for spec in result.get("works", []):
        spec["revenue_prices"] = entries.get(spec["id"], [])
    return result


@transaction.atomic
def change_price(user, work_id, price, effective_from, reason="", corrects=None, review_task=None):
    from .models import ProjectWork, WorkPrice

    if role_code(user) == 'ESTIMATOR':
        if review_task is None:
            from django.core.exceptions import PermissionDenied
            raise PermissionDenied('Изменение цены доступно только в назначенном задании.')
        from apps.planning.estimate_review import lock_task, scoped_work
        review_task = lock_task(user, review_task)
        scoped_work(review_task, work_id)
    else:
        require_roles(user, PLAN_ROLES)
    work = ProjectWork.objects.select_for_update().get(pk=work_id, company=user.company)
    if hasattr(work, "merged_source"):
        raise ValidationError(
            "Исходная работа архивная. Измените цену комплекса работ."
        )
    if corrects:
        if corrects.work_id != work.pk or corrects.company_id != user.company_id:
            raise ValidationError("Исправляется цена другой работы.")
        if effective_from != corrects.effective_from or not reason.strip():
            raise ValidationError(
                "Исправление сохраняет дату действия цены и требует основания."
            )
    elif effective_from < timezone.localdate() and not can_backdate_price(user):
        raise ValidationError(
            "Прошлую цену можно изменить только отдельным исправлением с основанием."
        )
    entry = WorkPrice(
        company=user.company,
        work=work,
        price=price,
        effective_from=effective_from,
        created_by=user,
        reason=reason,
        corrects=corrects,
    )
    entry.save()
    ProjectWork.objects.filter(pk=work.pk).update(unit_price=price_on(work))
    return entry


def reprice_plan_rows(spec, rows, *, start=None, end=None):
    """Value only mutable plan rows at their dated rate, rounded per item/rate.

    Group rounding avoids multiplying a cent error by the number of days.
    Captured composition is used even when the live catalogue has changed.
    """
    from collections import defaultdict
    from decimal import ROUND_HALF_UP
    from apps.planning.workspace_services import allocate
    items={str(item['id']):item for item in spec.get('items',[])}
    groups=defaultdict(list)
    for row in rows:
        day=date.fromisoformat(row['date'])
        if row.get('origin','PLAN')!='PLAN' or (start and day<start) or (end and day>end):continue
        rate=rate_on(spec.get('revenue_prices',[]),day,spec.get('unit_price',0))
        groups[(str(row.get('item_id')),rate)].append(row)
    for (item_id,rate),selected in groups.items():
        item=items.get(item_id)
        norm=Decimal(str(item['norm'])) if item else Decimal(1)
        weight=Decimal(str(item['weight']))/100 if item else Decimal(1)
        if norm<=0:raise ValidationError('Для расчёта стоимости нужен положительный норматив.')
        quantities=[Decimal(str(row['quantity'])) for row in selected]
        total=(sum(quantities,Decimal(0))/norm*weight*rate).quantize(Decimal('.01'),rounding=ROUND_HALF_UP)
        for row,value in zip(selected,allocate(total,quantities,Decimal('.01'))):row['value']=str(value)
    return rows
