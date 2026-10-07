"""Revenue uses parent completion, frozen plan rates and effective dated actual rates."""

from collections import defaultdict
from datetime import date
from decimal import Decimal
from django.utils import timezone
from apps.planning.report_services import build_matrix, days_between
from apps.works.models import ProjectWork, WorkPrice
from apps.works.prices import rate_on

ZERO = Decimal(0)


def rows_in(groups):
    return [
        row for group in groups for row in [*group["rows"], *rows_in(group["children"])]
    ]


def metrics(daily, cutoff):
    p = [r["plan"] for r in daily if r["plan"] is not None]
    to_date = [r for r in daily if r["date"] <= cutoff]
    pd = [r["plan"] for r in to_date if r["plan"] is not None]
    f = sum((r["fact"] or ZERO for r in to_date), ZERO)
    full, current = (sum(p, ZERO) if p else None), (sum(pd, ZERO) if pd else None)
    return {
        "plan": full,
        "plan_to_date": current,
        "fact": f,
        "percent_date": f / current * 100 if current else None,
        "percent_full": f / full * 100 if full else None,
        "delta": f - current if current is not None else None,
        "volume_delta": sum((r["volume_delta"] or ZERO for r in to_date), ZERO),
        "price_delta": sum((r["price_delta"] or ZERO for r in to_date), ZERO),
    }


def combined_daily(rows, dates):
    index = defaultdict(list)
    for row in rows:
        for cell in row["daily"]:
            index[cell["date"]].append(cell)
    result = []
    for day in dates:
        cells = index[day]

        def total(key):
            values = [c[key] for c in cells if c.get(key) is not None]
            return sum(values, ZERO) if values else None

        result.append(
            {
                "date": day,
                **{
                    key: total(key)
                    for key in ["plan", "fact", "volume_delta", "price_delta"]
                },
            }
        )
    return result


def resource_totals(rows):
    result = {}
    for kind in ["labor", "equipment", "fuel"]:
        values = [r["cell"] for r in rows if r["kind"] == kind]

        def total(key):
            known = [c[key] for c in values if c[key] is not None]
            return sum(known, ZERO) if known else None

        plan, fact = total("plan"), total("fact")
        result[kind] = {
            "plan": plan,
            "fact": fact,
            "delta": fact - plan if plan is not None and fact is not None else None,
            "known": sum(c["fact"] is not None for c in values),
            "count": len(values),
            "partial": any(c["fact"] is None for c in values),
        }
    return result


def build_dashboard(user, filters, today=None):
    start, end = filters["start"], filters["end"]
    cutoff = min(today or timezone.localdate(), end)
    days = days_between(start, end)
    report = build_matrix(
        user, {**filters, "daily": True, "month": None, "sections": ["works"]}
    )
    catalog = {
        w.pk: w
        for w in ProjectWork.objects.filter(company=user.company)
        .select_related("section__construction_object", "work_group")
        .prefetch_related("items")
    }
    prices = defaultdict(list)
    for price in WorkPrice.objects.filter(company=user.company).order_by(
        "effective_from", "pk"
    ):
        prices[price.work_id].append(price)
    all_rows, objects = [], []
    for obj in report["objects"]:
        rows = []
        sources = obj["sources"]
        chosen = {
            day: next((s for s in sources if s.start <= day <= s.end), None)
            for day in days
        }
        specs = {
            id(s): {spec["id"]: spec for spec in s.snapshot.get("works", [])}
            for s in sources
        }
        for row in rows_in(obj["sections"][0]["groups"]):
            work_id = int(row["key"].split("-w")[1])
            work = catalog.get(work_id)
            daily = []
            for day, cell in zip(days, row["cells"]):
                source = chosen[day]
                spec = specs[id(source)].get(work_id, {}) if source else {}
                fact_rate = rate_on(
                    prices[work_id],
                    day,
                    work.unit_price if work else spec.get("unit_price", 0),
                )
                plan_rate = rate_on(
                    spec.get("revenue_prices", []),
                    day,
                    spec.get("unit_price", fact_rate),
                )
                pq, fq = cell["plan"], cell["fact"] if day <= cutoff else None
                # A covered plan that omits this work means zero planned quantity.
                p = pq * plan_rate if pq is not None else None
                f = fq * fact_rate if fq is not None else None
                v = (
                    ((fq or ZERO) - pq) * plan_rate
                    if pq is not None and day <= cutoff
                    else None
                )
                price_diff = (
                    (fq or ZERO) * (fact_rate - plan_rate)
                    if pq is not None and day <= cutoff
                    else None
                )
                daily.append(
                    {
                        "date": day,
                        "plan": p,
                        "fact": f,
                        "plan_quantity": pq,
                        "fact_quantity": fq,
                        "plan_rate": plan_rate,
                        "fact_rate": fact_rate,
                        "volume_delta": v,
                        "price_delta": price_diff,
                    }
                )
            entry = {
                "id": work_id,
                "name": row["label"],
                "unit": row["unit"],
                "work": work,
                "group": (
                    work.work_group.name
                    if work and work.work_group_id
                    else "Без группы"
                ),
                "daily": daily,
                "children": row["children"],
                "sources": sources,
                **metrics(daily, cutoff),
            }
            rows.append(entry)
            all_rows.append(entry)
        daily = combined_daily(rows, days)
        # A resource-only approved snapshot still has a known zero work plan.
        if not rows:
            for cell in daily:
                cell["plan"] = ZERO if chosen[cell["date"]] else None
        groups = defaultdict(list)
        for row in rows:
            groups[row["group"]].append(row)
        objects.append(
            {
                "object": obj["object"],
                "name": obj["name"],
                "rows": rows,
                "groups": dict(groups),
                "warnings": obj["warnings"],
                "daily": daily,
                **metrics(daily, cutoff),
            }
        )
    total_daily = combined_daily(objects, days)
    data = {
        "objects": objects,
        "works": all_rows,
        "start": start,
        "end": end,
        "cutoff": cutoff,
        "future": cutoff < start,
        "daily": total_daily,
        **metrics(total_daily, cutoff),
    }
    # Snapshot values, never sum headcounts or fuel balance across dates.
    resource_objects = []
    if cutoff >= start:
        resource_report = build_matrix(
            user,
            {
                **filters,
                "start": cutoff,
                "end": cutoff,
                "daily": True,
                "month": None,
                "sections": ["labor", "equipment", "fuel"],
            },
        )
        for obj in resource_report["objects"]:
            entries = []
            for section in obj["sections"]:
                for row in rows_in(section["groups"]):
                    if section["kind"] == "fuel" and row["operation"] != "last":
                        continue
                    entries.append(
                        {
                            "kind": section["kind"],
                            "label": row["label"],
                            "unit": row["unit"],
                            "cell": row["total"],
                        }
                    )
            resource_objects.append(
                {
                    "object": obj["object"],
                    "name": obj["name"],
                    "rows": entries,
                    "groups": [
                        {
                            **section,
                            "groups": (
                                balance_groups(section["groups"])
                                if section["kind"] == "fuel"
                                else section["groups"]
                            ),
                        }
                        for section in obj["sections"]
                    ],
                    "totals": resource_totals(entries),
                }
            )
    data["resource_objects"] = resource_objects
    data["resources"] = resource_totals(
        [r for obj in resource_objects for r in obj["rows"]]
    )
    return data


def balance_groups(groups):
    return [
        {
            **g,
            "rows": [r for r in g["rows"] if r["operation"] == "last"],
            "children": balance_groups(g["children"]),
        }
        for g in groups
    ]
