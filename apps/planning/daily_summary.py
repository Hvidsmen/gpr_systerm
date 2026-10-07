"""Read-only daily distribution of a specific lower monthly work version."""

from collections import defaultdict
from datetime import date
from decimal import Decimal
from django.core.exceptions import ValidationError
from apps.works.progress import WorkProgressService
from .global_services import source_versions
from .report_services import days_between

ZERO = Decimal(0)


def daily_summary(version):
    plan = version.monthly_plan
    work = plan.project_work
    dates = days_between(plan.start_date, plan.end_date)
    raw = list(version.daily_plans.select_related("work_item").order_by("date", "pk"))
    history = [
        v
        for v in source_versions(
            work.section.construction_object, date.min, plan.start_date
        )
        if v.monthly_plan.project_work_id == work.pk
        and v.monthly_plan.end_date < plan.start_date
    ]
    context = {
        "work": work,
        "daily_plans": raw,
        "distribution_dates": dates,
        "distribution_target": plan.planned_quantity,
        "distribution_generated": bool(raw),
        "distribution_rows": [],
    }
    try:
        planned = WorkProgressService.planned(work, history + [version])
        facts = WorkProgressService.facts(work, until=plan.end_date)
    except ValidationError as error:
        context["distribution_error"] = "; ".join(error.messages)
        return context
    context["days"] = [
        {
            "date": day,
            "plan": planned.get(day, {}).get("daily", ZERO) if raw else None,
            "fact": facts.get(day, {}).get("daily"),
        }
        for day in dates
    ]
    context["total_planned_quantity"] = (
        sum((d["plan"] or ZERO for d in context["days"]), ZERO) if raw else None
    )
    context["total_fact"] = sum((d["fact"] or ZERO for d in context["days"]), ZERO)
    context["distribution_rows"].append(
        {
            "label": work.name,
            "unit": work.unit,
            "main": True,
            "values": [d["plan"] for d in context["days"]],
            "total": context["total_planned_quantity"],
        }
    )
    if work.kind == "COMPOSITE":
        quantities = defaultdict(lambda: defaultdict(lambda: ZERO))
        for row in raw:
            quantities[row.work_item_id][row.date] += row.planned_quantity
        for item in work.items.order_by("sequence", "pk"):
            values = (
                [quantities[item.pk][day] for day in dates]
                if raw
                else [None] * len(dates)
            )
            context["distribution_rows"].append(
                {
                    "label": item.name,
                    "unit": item.unit,
                    "norm": item.quantity_per_unit,
                    "values": values,
                    "total": sum(values, ZERO) if raw else None,
                }
            )
    context["distribution_difference"] = (
        context["total_planned_quantity"] - plan.planned_quantity if raw else None
    )
    return context
