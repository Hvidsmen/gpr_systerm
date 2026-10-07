"""Effective dated prices, immutable audit entries and frozen planning prices."""

from datetime import date
from decimal import Decimal
from copy import deepcopy
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from core.permissions import PLAN_ROLES, require_roles


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


def freeze_prices(snapshot, company):
    from .models import WorkPrice

    result = deepcopy(snapshot)
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
def change_price(user, work_id, price, effective_from, reason="", corrects=None):
    from .models import ProjectWork, WorkPrice

    require_roles(user, PLAN_ROLES)
    work = ProjectWork.objects.select_for_update().get(pk=work_id, company=user.company)
    if hasattr(work, "merged_source"):
        raise ValidationError(
            "Исходная работа архивная. Измените цену составной работы."
        )
    if corrects:
        if corrects.work_id != work.pk or corrects.company_id != user.company_id:
            raise ValidationError("Исправляется цена другой работы.")
        if effective_from != corrects.effective_from or not reason.strip():
            raise ValidationError(
                "Исправление сохраняет дату действия цены и требует основания."
            )
    elif effective_from < timezone.localdate():
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
