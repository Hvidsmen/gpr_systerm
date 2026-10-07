from collections import defaultdict
from decimal import Decimal, ROUND_FLOOR, ROUND_DOWN

from django.core.exceptions import ValidationError

ZERO = Decimal("0")


def work_specification(work):
    from .prices import price_on
    return {
        "id": work.pk,
        "name": work.name,
        "unit": work.unit,
        "unit_price": str(price_on(work)),
        "kind": work.kind,
        "allow_fractional": work.allow_fractional,
        "items": [
            {
                "id": item.pk,
                "name": item.name,
                "unit": item.unit,
                "norm": str(item.quantity_per_unit),
                "weight": str(item.weight),
            }
            for item in work.items.order_by("sequence", "pk")
        ],
    }


def quantity_from_totals(spec, totals):
    if spec["kind"] == "SIMPLE":
        return totals.get(None, ZERO)
    items = spec["items"]
    if not items or any(Decimal(item["norm"]) <= 0 for item in items):
        raise ValidationError(
            "У составной работы должны быть подработы с положительными нормативами."
        )
    value = min(totals.get(item["id"], ZERO) / Decimal(item["norm"]) for item in items)
    if not spec["allow_fractional"]:
        return value.to_integral_value(rounding=ROUND_FLOOR)
    return value.quantize(Decimal("0.001"), rounding=ROUND_DOWN)


def cumulative_series(spec, rows):
    """Rows: (date, item_id, quantity); include all history before clipping a period."""
    by_date = defaultdict(lambda: defaultdict(lambda: ZERO))
    for day, item_id, quantity in rows:
        by_date[day][item_id] += Decimal(quantity)
    totals = defaultdict(lambda: ZERO)
    previous = ZERO
    result = {}
    for day in sorted(by_date):
        for item_id, quantity in by_date[day].items():
            totals[item_id] += quantity
        completed = quantity_from_totals(spec, totals)
        result[day] = {"daily": completed - previous, "cumulative": completed}
        previous = completed
    return result


def historical_fact_rows(company, work_id, until=None):
    from apps.production.models import DailyFact
    from .models import WorkMergeSource
    link = WorkMergeSource.objects.filter(company=company,source_work_id=work_id).first()
    qs = DailyFact.objects.filter(company=company)
    qs = qs.filter(work_item_id=link.item_id) if link else qs.filter(project_work_id=work_id)
    if until:
        qs=qs.filter(date__lte=until)
    return [(day,None if link else item,quantity) for day,item,quantity in qs.values_list('date','work_item_id','actual_quantity')]


class WorkProgressService:
    @staticmethod
    def facts(work, until=None, specification=None):
        return cumulative_series(
            specification or work_specification(work),
            historical_fact_rows(work.company, work.pk, until),
        )

    @staticmethod
    def completed(work, until=None):
        series = WorkProgressService.facts(work, until)
        return list(series.values())[-1]["cumulative"] if series else ZERO

    @staticmethod
    def planned(work, versions=None):
        from apps.planning.models import DailyPlan, PlanVersion

        explicit_versions = versions is not None
        if versions is None:
            candidates = PlanVersion.objects.filter(
                company=work.company,
                monthly_plan__project_work=work,
                status__in=["APPROVED", "COMPLETED"],
            ).order_by("monthly_plan_id", "-version_number")
            selected = {}
            for version in candidates:
                selected.setdefault(version.monthly_plan_id, version.pk)
            versions = list(selected.values())
        rows = DailyPlan.objects.filter(
            company=work.company, plan_version_id__in=versions
        ).values_list("date", "work_item_id", "planned_quantity")
        result = cumulative_series(work_specification(work), rows)
        if explicit_versions:
            return result
        from apps.planning.models import GlobalPlanVersion

        versions = GlobalPlanVersion.objects.filter(
            company=work.company,
            construction_object=work.section.construction_object,
            workspace__isnull=False,
            status__in=["APPROVED", "COMPLETED"],
        ).order_by("-approved_at", "-pk")
        daily = {day: values["daily"] for day, values in result.items()}
        covered = []
        for version in versions:
            spec = next(
                (s for s in version.snapshot.get("works", []) if s["id"] == work.pk),
                None,
            )
            for day in list(daily):
                if version.start_date <= day <= version.end_date and not any(
                    start <= day <= end for start, end in covered
                ):
                    daily.pop(day)
            if spec:
                from datetime import date

                for row in spec["daily"]:
                    day = date.fromisoformat(row["date"])
                    if not any(start <= day <= end for start, end in covered):
                        daily[day] = Decimal(row["quantity"])
            covered.append((version.start_date, version.end_date))
        total = ZERO
        result = {}
        for day in sorted(daily):
            total += daily[day]
            result[day] = {"daily": daily[day], "cumulative": total}
        return result
