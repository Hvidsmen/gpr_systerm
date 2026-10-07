from collections import defaultdict
from datetime import date
from decimal import Decimal
from apps.works.progress import (
    historical_fact_rows,
    quantity_from_totals,
    work_specification,
)
from apps.production.models import DailyFact
from apps.works.models import WorkMergeSource

ZERO = Decimal(0)


def work_detail(user, row, start, cutoff):
    work = row["work"]
    source = next((s for s in row["sources"] if s.start <= cutoff <= s.end), None)
    spec = (
        next(
            (s for s in source.snapshot.get("works", []) if s["id"] == row["id"]), None
        )
        if source
        else None
    )
    spec = spec or work_specification(work)
    facts = historical_fact_rows(user.company, row["id"], cutoff)
    totals = defaultdict(lambda: ZERO)
    for day, item, qty in facts:
        totals[item] += qty
    plans = defaultdict(lambda: ZERO)
    for record in spec.get("context_plans", []) + spec.get("plans", []):
        if date.fromisoformat(record["date"]) <= cutoff:
            plans[record["item_id"]] += Decimal(record["quantity"])
    target = quantity_from_totals(spec, plans)
    equivalents = [totals[i["id"]] / Decimal(i["norm"]) for i in spec.get("items", [])]
    minimum = min(equivalents) if equivalents else None
    children = []
    for item in spec.get("items", []):
        norm = Decimal(item["norm"])
        eq = totals[item["id"]] / norm
        children.append(
            {
                "name": item["name"],
                "unit": item["unit"],
                "norm": norm,
                "plan": plans[item["id"]],
                "fact": totals[item["id"]],
                "equivalent": eq,
                "limiting": eq == minimum,
                "missing": max(target * norm - totals[item["id"]], ZERO),
            }
        )
    link = WorkMergeSource.objects.filter(
        company=user.company, source_work_id=row["id"]
    ).first()
    records = DailyFact.objects.filter(
        company=user.company, date__range=(start, cutoff)
    )
    records = (
        records.filter(work_item_id=link.item_id)
        if link
        else records.filter(project_work_id=row["id"])
    )
    records = list(
        records.select_related("work_item", "reported_by", "deviation_reason").order_by(
            "date", "pk"
        )
    )
    by_date = defaultdict(list)
    for record in records:
        by_date[record.date].append(record)
    days = []
    fact_by_day = defaultdict(list)
    running = defaultdict(lambda: ZERO)
    for day, item, qty in facts:
        if day < start:
            running[item] += qty
        else:
            fact_by_day[day].append((item, qty))
    cumulative_plan = cumulative_fact = ZERO
    for cell in row["daily"]:
        if cell["date"] > cutoff:
            break
        for item, qty in fact_by_day[cell["date"]]:
            running[item] += qty
        equivalents = {
            i["name"]: running[i["id"]] / Decimal(i["norm"])
            for i in spec.get("items", [])
        }
        lowest = min(equivalents.values()) if equivalents else None
        limiting = [name for name, value in equivalents.items() if value == lowest]
        cumulative_plan += cell["plan_quantity"] or ZERO
        cumulative_fact += cell["fact_quantity"] or ZERO
        if (
            cell["plan_quantity"]
            or cell["fact_quantity"] is not None
            or by_date[cell["date"]]
        ):
            days.append(
                {
                    **cell,
                    "cumulative_plan": cumulative_plan,
                    "cumulative_fact": cumulative_fact,
                    "records": by_date[cell["date"]],
                    "limiting": limiting,
                }
            )
    return {
        "row": row,
        "children": children,
        "days": days,
        "target": target,
        "no_plan": source is None,
        "cutoff": cutoff,
        "records_count": len(records),
    }
