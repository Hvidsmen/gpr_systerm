"""Revenue uses parent completion, frozen plan rates and effective dated actual rates."""

from collections import defaultdict
from datetime import date
import json
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
        user, {**filters, "daily": True, "month": None, "dashboard_compact": True, "sections": ["works"]}
    )
    work_ids = {int(row["key"].split("-w")[1]) for obj in report["objects"]
                for row in rows_in(obj["sections"][0]["groups"])}
    catalog = {
        w.pk: w
        for w in ProjectWork.objects.filter(company=user.company, pk__in=work_ids)
        .select_related("section__construction_object", "work_group")
        .prefetch_related("items")
    }
    prices = defaultdict(list)
    for price in WorkPrice.objects.filter(company=user.company, work_id__in=work_ids).order_by(
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
                "group": next((specs[id(source)][work_id].get("section_name") for source in sources if work_id in specs[id(source)] and specs[id(source)][work_id].get("section_name")), work.section.name if work else "Без раздела"),
                "volume_plan": sum((cell["plan_quantity"] for cell in daily if cell["plan_quantity"] is not None), ZERO) if any(cell["plan_quantity"] is not None for cell in daily) else None,
                "volume_plan_to_date": sum((cell["plan_quantity"] for cell in daily if cell["date"] <= cutoff and cell["plan_quantity"] is not None), ZERO) if any(cell["date"] <= cutoff and cell["plan_quantity"] is not None for cell in daily) else None,
                "volume_fact": sum((cell["fact_quantity"] for cell in daily if cell["date"] <= cutoff and cell["fact_quantity"] is not None), ZERO) if any(cell["date"] <= cutoff and cell["fact_quantity"] is not None for cell in daily) else None,
                "daily": daily,
                "has_price": any(cell["plan_rate"] > ZERO or cell["fact_rate"] > ZERO for cell in daily),
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
        rows.sort(key=lambda row: (row["plan"] is None, -(row["plan"] or ZERO), row["name"].casefold()))
        groups = defaultdict(list)
        for row in rows:
            groups[row["group"]].append(row)
        for section_rows in groups.values():
            section_rows[0]["section_total"] = metrics(combined_daily(section_rows, days), cutoff)
        objects.append(
            {
                "object": obj["object"],
                "name": obj["name"],
                "rows": rows,
                "groups": dict(sorted(groups.items(), key=lambda group: (-sum((row["plan"] or ZERO for row in group[1]), ZERO), group[0].casefold()))),
                "warnings": obj["warnings"],
                "daily": daily,
                **metrics(daily, cutoff),
            }
        )
    objects.sort(key=lambda obj: (obj["plan"] is None, -(obj["plan"] or ZERO), obj["name"].casefold()))
    all_rows.sort(key=lambda row: (row["plan"] is None, -(row["plan"] or ZERO), row["name"].casefold()))
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
    # Release the detailed work matrix before building the resource matrix.
    del report
    # Snapshot values, never sum headcounts or fuel balance across dates.
    resource_objects = []
    if cutoff >= start:
        resource_report = build_matrix(
            user, {**filters, "start": start, "end": end, "daily": True,
                   "month": None, "dashboard_compact": True, "sections": ["labor", "equipment", "fuel"]}
        )
        cutoff_index = (cutoff - start).days
        for original in resource_report["objects"]:
            obj = {**original, "sections": [section for section in original["sections"] if section["kind"] != "works"]}
            for section in obj["sections"]:
                for row in rows_in(section["groups"]):
                    row["total"] = row["cells"][cutoff_index]
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
                            "groups": resource_group_totals((
                                balance_groups(section["groups"])
                                if section["kind"] == "fuel"
                                else section["groups"]
                            ), section["kind"]),
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
    data["resource_sections"] = resource_sections(resource_objects, data["resources"], days, cutoff)
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


def resource_group_totals(groups, kind):
    result = []
    for group in groups:
        totals = resource_totals([
            {"kind": kind, "cell": row["total"]}
            for row in rows_in([group])
        ])[kind]
        result.append({
            **group,
            "children": resource_group_totals(group["children"], kind),
            "total": totals,
        })
    return result


def resource_sections(objects, totals, days, cutoff):
    labels = [day.strftime("%d.%m.%Y") for day in days]

    def trend(rows, kind):
        plan, fact = [], []
        for index, day in enumerate(days):
            cells = [row["cells"][index] for row in rows]
            planned = [cell["plan"] for cell in cells if cell["plan"] is not None]
            actual = [cell["fact"] for cell in cells if cell["fact"] is not None] if day <= cutoff else []
            plan.append(float(sum(planned, ZERO)) if planned else None)
            fact.append(float(sum(actual, ZERO)) if actual else None)
        return json.dumps({"labels": labels, "plan": plan, "fact": fact}, ensure_ascii=False)

    def active(row):
        return any(cell.get("plan") or (day <= cutoff and cell.get("fact"))
                   for day, cell in zip(days, row["cells"]))

    def visible_groups(nodes):
        result = []
        for node in nodes:
            rows = [row for row in node["rows"] if active(row)]
            children = visible_groups(node["children"])
            if rows or children:
                result.append({**node, "rows": rows, "children": children})
        return result

    def groups(nodes, kind):
        return [{
            **node, "trend": trend(rows_in([node]), kind),
            "children": groups(node["children"], kind),
            "rows": [{**row, "trend": trend([row], kind)} for row in node["rows"]],
        } for node in nodes]

    sections = []
    for kind, label in [("labor", "Люди"), ("equipment", "Техника"), ("fuel", "ГСМ: остаток")]:
        sites, all_rows = [], []
        for obj in objects:
            section = next((section for section in obj["groups"] if section["kind"] == kind), None)
            if not section:
                continue
            visible = visible_groups(section["groups"])
            rows = rows_in(visible)
            if not rows:
                continue
            all_rows.extend(rows)
            sites.append({"name": obj["name"], "total": obj["totals"][kind],
                          "trend": trend(rows, kind), "groups": groups(visible, kind)})
        if not sites:
            continue
        sections.append({"kind": kind, "label": label, "total": totals[kind],
                         "trend": trend(all_rows, kind), "objects": sites})
    return sections
