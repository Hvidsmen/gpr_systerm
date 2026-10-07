"""Plan/fact matrices. Calendar averages and cumulative composite completion."""

from collections import defaultdict
from copy import deepcopy
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from calendar import monthrange
from apps.projects.models import ConstructionObject
from apps.works.models import ProjectWork
from apps.works.progress import work_specification, cumulative_series
from apps.resources.models import Brigade, EquipmentType
from apps.production.models import DailyFact, LaborFact, EquipmentFact, FuelFact
from .models import GlobalPlanVersion
from .workspace_services import months_between

ZERO = Decimal(0)


def days_between(start, end):
    return [start + timedelta(days=n) for n in range((end - start).days + 1)]


@dataclass
class Source:
    version: GlobalPlanVersion
    snapshot: dict
    start: date
    end: date
    member: object = None


def selected_objects(user, filters):
    query = ConstructionObject.objects.filter(company=user.company).select_related(
        "project"
    )
    if filters.get("consolidated"):
        query = query.filter(
            pk__in=filters["consolidated"].members.values("construction_object_id")
        )
    elif filters.get("project"):
        query = query.filter(project=filters["project"])
    if filters.get("objects"):
        query = query.filter(pk__in=filters["objects"].values("pk"))
    return list(query.order_by("project__name", "name", "pk"))


def select_sources(user, filters, objects):
    result = defaultdict(list)
    if filters.get("consolidated"):
        parent = filters["consolidated"]
        for member in parent.members.filter(
            construction_object__in=objects
        ).select_related("version", "review"):
            result[member.construction_object_id].append(
                Source(
                    member.version,
                    member.snapshot,
                    parent.start_date,
                    parent.end_date,
                    member,
                )
            )
        return result
    candidates = GlobalPlanVersion.objects.filter(
        company=user.company,
        construction_object__in=objects,
        status__in=["APPROVED", "COMPLETED"],
        start_date__lte=filters["end"],
        end_date__gte=filters["start"],
    ).order_by("-approved_at", "-pk")
    if filters["mode"] == "baseline":
        candidates = candidates.filter(version_kind="BASELINE")
    for version in candidates:
        result[version.construction_object_id].append(
            Source(version, version.snapshot, version.start_date, version.end_date)
        )
    for obj in objects:
        explicit = filters.get(f"version_{obj.pk}")
        if explicit:
            result[obj.pk] = [
                Source(
                    explicit, explicit.snapshot, explicit.start_date, explicit.end_date
                )
            ]
    return result


def empty_plan_value(source, day):
    if source is None:
        return None
    snapshot = source.snapshot
    if (
        snapshot.get("version_kind") == "FORECAST"
        and snapshot.get("scenario") == "REMAINING"
    ):
        target = snapshot.get("planning_month")
        if target and day < date.fromisoformat(target):
            return None  # Missing historical facts are not zero planned headcount.
    return ZERO


def resource_plan_value(index, chosen, kind, key, day):
    source = chosen[day]
    if source is None:
        return None
    return index[kind][id(source)][key].get(day, empty_plan_value(source, day))


def aggregate(values, dates, operation):
    present = [(day, values[day]) for day in dates if values.get(day) is not None]
    if not present:
        return None, 0, None
    if operation == "average":
        value = sum((value for _, value in present), ZERO) / len(present)
    elif operation == "last":
        value = present[-1][1]
    else:
        value = sum((value for _, value in present), ZERO)
    return value, len(present), present[-1][0]


def make_cell(plan, fact, dates, operation):
    p, p_days, _ = aggregate(plan, dates, operation)
    f, f_days, last = aggregate(fact, dates, operation)
    return {
        "plan": p,
        "fact": f,
        "delta": f - p if p is not None and f is not None else None,
        "plan_days": p_days,
        "fact_days": f_days,
        "days": len(dates),
        "stock_date": last if operation == "last" and last != dates[-1] else None,
    }


def make_row(key, label, unit, plan, fact, buckets, operation="sum", children=None):
    all_days = [day for bucket in buckets for day in bucket["dates"]]
    return {
        "key": key,
        "label": label,
        "unit": unit,
        "operation": operation,
        "cells": [
            make_cell(plan, fact, bucket["dates"], operation) for bucket in buckets
        ],
        "total": make_cell(plan, fact, all_days, operation),
        "children": children or [],
    }


def grouped(rows):
    root = {}
    for path, row in rows:
        branch = root
        for name in path:
            node = branch.setdefault(name, {"label": name, "rows": [], "nodes": {}})
            branch = node["nodes"]
        node["rows"].append(row)

    def finish(nodes):
        return [
            {**node, "children": finish(node["nodes"])}
            for _, node in sorted(nodes.items(), key=lambda pair: pair[0].casefold())
        ]

    return finish(root)


def work_matches(spec, filters):
    query = filters.get("works_q", "").casefold().strip()
    text = " ".join([spec["name"], *[i["name"] for i in spec.get("items", [])]])
    return (
        (not query or query in text.casefold())
        and (not filters.get("work_kind") or filters["work_kind"] == spec["kind"])
        and (
            not filters.get("work_group")
            or filters["work_group"].pk == spec.get("group_id")
        )
    )


def build_matrix(user, filters, *, source_overrides=None):
    start, end = filters["start"], filters["end"]
    if filters.get("month"):
        month = filters["month"]
        start, end = max(start, month), min(
            end, month.replace(day=monthrange(month.year, month.month)[1])
        )
    if filters.get("month") or filters.get("daily"):
        buckets = [
            {"label": day.strftime("%d.%m"), "date": day, "dates": [day]}
            for day in days_between(start, end)
        ]
    else:
        buckets = []
        for month in months_between(start, end):
            a, b = max(start, month), min(
                end, month.replace(day=monthrange(month.year, month.month)[1])
            )
            buckets.append(
                {
                    "label": month.strftime("%m.%Y"),
                    "date": month,
                    "dates": days_between(a, b),
                }
            )
    all_days = [day for bucket in buckets for day in bucket["dates"]]
    objects = selected_objects(user, filters)
    if source_overrides is None:
        sources = select_sources(user, filters, objects)
    else:
        # Only internal detail views supply drafts; ordinary reports still require approval.
        from django.core.exceptions import PermissionDenied

        allowed = {obj.pk for obj in objects}
        for object_id, entries in source_overrides.items():
            if object_id not in allowed or any(
                source.version.company_id != user.company_id
                or source.version.construction_object_id != object_id
                for source in entries
            ):
                raise PermissionDenied
        sources = source_overrides
    work_catalog = {
        w.pk: w
        for w in ProjectWork.objects.filter(
            company=user.company, section__construction_object__in=objects
        )
        .select_related("work_group", "section")
        .prefetch_related("items")
    }
    brigade_catalog = {
        r.pk: r
        for r in Brigade.objects.filter(company=user.company).select_related(
            "group", "macro_group"
        )
    }
    equipment_catalog = {
        r.pk: r
        for r in EquipmentType.objects.filter(company=user.company).select_related(
            "category"
        )
    }
    from apps.works.models import WorkMergeSource
    merge_links = list(WorkMergeSource.objects.filter(company=user.company).select_related('item'))
    item_sources = {link.item_id: link.source_work_id for link in merge_links}
    source_parents = {link.source_work_id: link.item.project_work_id for link in merge_links}
    parent_sources = defaultdict(set)
    for source_id, parent_id in source_parents.items():
        parent_sources[parent_id].add(source_id)
    work_facts = defaultdict(list)
    for row in DailyFact.objects.filter(
        company=user.company,
        project_work__section__construction_object__in=objects,
        date__lte=end,
    ).values("project_work_id", "date", "work_item_id", "actual_quantity"):
        work_facts[row["project_work_id"]].append(
            (row["date"], row["work_item_id"], row["actual_quantity"])
        )
        if row['work_item_id'] in item_sources:
            work_facts[item_sources[row['work_item_id']]].append((row['date'], None, row['actual_quantity']))
    resource_facts = {
        kind: defaultdict(lambda: defaultdict(lambda: ZERO))
        for kind in ["labor", "equipment", "fuel", "balance"]
    }
    for row in LaborFact.objects.filter(
        company=user.company, construction_object__in=objects, date__range=(start, end)
    ).values("construction_object_id", "brigade_id", "date", "actual_workers"):
        if row["actual_workers"] is not None:
            resource_facts["labor"][(row["construction_object_id"], row["brigade_id"])][
                row["date"]
            ] += Decimal(row["actual_workers"])
    for row in EquipmentFact.objects.filter(
        company=user.company, construction_object__in=objects, date__range=(start, end)
    ).values(
        "construction_object_id",
        "equipment_type_id",
        "equipment_number",
        "date",
        "actual_count",
    ):
        resource_facts["equipment"][
            (
                row["construction_object_id"],
                row["equipment_type_id"],
                row["equipment_number"],
            )
        ][row["date"]] += Decimal(row["actual_count"])
    for row in FuelFact.objects.filter(
        company=user.company, construction_object__in=objects, date__range=(start, end)
    ).values(
        "construction_object_id", "fuel_type", "date", "actual_liters", "actual_balance"
    ):
        key = (row["construction_object_id"], row["fuel_type"])
        resource_facts["fuel"][key][row["date"]] += row["actual_liters"]
        resource_facts["balance"][key][row["date"]] += row["actual_balance"]
    result = []
    for obj in objects:
        available = sources[obj.pk]
        chosen = {
            day: next(
                (source for source in available if source.start <= day <= source.end),
                None,
            )
            for day in all_days
        }
        covered = {day for day, source in chosen.items() if source is not None}
        available = [
            source
            for source in available
            if any(selected is source for selected in chosen.values())
        ]
        work_specs = {}
        plan_index = {}
        resource_meta = {"labor": {}, "equipment": {}}
        resource_index = {"labor": {}, "equipment": {}, "fuel": {}, "balance": {}}
        resource_keys = {kind: set() for kind in ["labor", "equipment", "fuel"]}
        for source in available:
            idx = id(source)
            plan_index[idx] = {}
            for spec in source.snapshot.get("works", []):
                work_specs.setdefault(spec["id"], spec)
                plan_index[idx][spec["id"]] = {
                    "spec": spec,
                    "daily": {
                        date.fromisoformat(r["date"]): Decimal(r["quantity"])
                        for r in spec.get("daily", [])
                    },
                    "items": defaultdict(lambda: defaultdict(lambda: ZERO)),
                }
                for row in spec.get("plans", []):
                    plan_index[idx][spec["id"]]["items"][row["item_id"]][
                        date.fromisoformat(row["date"])
                    ] += Decimal(row["quantity"])
            for kind in ["labor", "equipment"]:
                for key, meta in (
                    source.snapshot.get("catalogs", {}).get(kind, {}).items()
                ):
                    resource_meta[kind].setdefault(int(key), meta)
            for kind in ["labor", "equipment", "fuel"]:
                data = defaultdict(lambda: defaultdict(lambda: ZERO))
                balances = defaultdict(lambda: defaultdict(lambda: ZERO))
                for row in source.snapshot.get("resources", {}).get(kind, []):
                    day = date.fromisoformat(row["date"])
                    key = (
                        row["brigade_id"]
                        if kind == "labor"
                        else (
                            (row["equipment_type_id"], row.get("equipment_number", ""))
                            if kind == "equipment"
                            else row["fuel_type"]
                        )
                    )
                    field = (
                        "planned_workers"
                        if kind == "labor"
                        else (
                            "planned_count" if kind == "equipment" else "planned_liters"
                        )
                    )
                    if row.get(field) is not None:
                        data[key][day] += Decimal(str(row[field]))
                    if kind == "fuel":
                        balances[key][day] += Decimal(
                            str(row.get("planned_balance") or 0)
                        )
                    resource_keys[kind].add(key)
                for row in source.snapshot.get("monthly_inputs", {}).get(
                    "resources", []
                ):
                    if row["kind"] == kind:
                        resource_keys[kind].add(
                            row["brigade_id"]
                            if kind == "labor"
                            else (
                                (
                                    row["equipment_type_id"],
                                    row.get("equipment_number", ""),
                                )
                                if kind == "equipment"
                                else row["fuel_type"]
                            )
                        )
                resource_index[kind][idx] = data
                if kind == "fuel":
                    resource_index["balance"][idx] = balances
        def actual_visible(work_id, day):
            selected=chosen.get(day)
            ids=set(plan_index[id(selected)]) if selected else set()
            parent=source_parents.get(work_id)
            if parent:
                return work_id in ids or bool(parent not in ids and ids.intersection(parent_sources[parent]))
            if work_id in parent_sources:
                return work_id in ids or not ids.intersection(parent_sources[work_id])
            return True

        for work_id, work in work_catalog.items():
            if work.section.construction_object_id == obj.pk and any(
                start <= day <= end and actual_visible(work_id, day) for day, _, _ in work_facts[work_id]
            ):
                work_specs.setdefault(work_id, work_specification(work))
        sections = []
        if "works" in filters["sections"]:
            rows = []
            for work_id, stored in work_specs.items():
                spec = deepcopy(stored)
                work = work_catalog.get(work_id)
                if "group_name" not in spec:
                    spec.update(
                        group_id=work.work_group_id if work else None,
                        group_name=(
                            work.work_group.name
                            if work and work.work_group_id
                            else "Без группы"
                        ),
                    )
                if not work_matches(spec, filters):
                    continue
                actual_rows = work_facts[work_id]
                actual_by_item = defaultdict(dict)
                actual_days = set()
                for day, item, quantity in actual_rows:
                    if start <= day <= end and actual_visible(work_id, day):
                        actual_by_item[item][day] = (
                            actual_by_item[item].get(day, ZERO) + quantity
                        )
                        actual_days.add(day)
                series_cache = {}
                plan, fact = {}, {}
                for day in all_days:
                    source = chosen[day]
                    entry = plan_index[id(source)].get(work_id) if source else None
                    plan[day] = (
                        entry["daily"].get(day, empty_plan_value(source, day))
                        if entry
                        else empty_plan_value(source, day)
                    )
                    day_spec = entry["spec"] if entry else spec
                    fingerprint = (
                        day_spec["kind"],
                        day_spec["allow_fractional"],
                        tuple((i["id"], i["norm"]) for i in day_spec.get("items", [])),
                    )
                    if fingerprint not in series_cache:
                        series_cache[fingerprint] = cumulative_series(
                            day_spec, actual_rows
                        )
                    if day in actual_days:
                        fact[day] = (
                            series_cache[fingerprint].get(day, {}).get("daily", ZERO)
                        )
                children = []
                items = {item["id"]: item for item in spec.get("items", [])}
                for source in available:
                    entry = plan_index[id(source)].get(work_id)
                    if entry:
                        for item in entry["spec"].get("items", []):
                            items.setdefault(item["id"], item)
                if spec["kind"] == "COMPOSITE":
                    for item_id, item in items.items():
                        item_plan = {}
                        for day in all_days:
                            source = chosen[day]
                            entry = (
                                plan_index[id(source)].get(work_id) if source else None
                            )
                            item_plan[day] = (
                                entry["items"][item_id].get(
                                    day, empty_plan_value(source, day)
                                )
                                if entry
                                else empty_plan_value(source, day)
                            )
                        children.append(
                            make_row(
                                f"w{work_id}-i{item_id}",
                                item["name"],
                                item["unit"],
                                item_plan,
                                actual_by_item[item_id],
                                buckets,
                            )
                        )
                rows.append(
                    (
                        [spec.get("group_name", "Без группы")],
                        make_row(
                            f"o{obj.pk}-w{work_id}",
                            spec["name"],
                            spec["unit"],
                            plan,
                            fact,
                            buckets,
                            children=children,
                        ),
                    )
                )
            sections.append(
                {"kind": "works", "label": "Работы", "groups": grouped(rows)}
            )
        for kind, label in [
            ("labor", "Люди"),
            ("equipment", "Техника"),
            ("fuel", "ГСМ"),
        ]:
            if kind not in filters["sections"]:
                continue
            rows = []
            for fact_key in resource_facts[kind]:
                if fact_key[0] == obj.pk:
                    resource_keys[kind].add(
                        fact_key[1] if kind != "equipment" else fact_key[1:]
                    )
            for key in sorted(resource_keys[kind], key=str):
                if kind == "labor":
                    row = brigade_catalog.get(key)
                    meta = resource_meta[kind].get(key) or (
                        {
                            "name": row.name,
                            "unit": row.unit,
                            "macro_group_id": row.macro_group_id,
                            "macro_group_name": (
                                row.macro_group.name
                                if row.macro_group_id
                                else "Без макрогруппы"
                            ),
                            "group_id": row.group_id,
                            "group_name": (
                                row.group.name if row.group_id else "Без группы"
                            ),
                        }
                        if row
                        else {"name": "Удалённая бригада", "unit": "чел."}
                    )
                    if (
                        filters.get("labor_q", "").casefold().strip()
                        not in meta["name"].casefold()
                    ):
                        continue
                    if (
                        filters.get("macro_group")
                        and filters["macro_group"].pk != meta.get("macro_group_id")
                        or filters.get("brigade_group")
                        and filters["brigade_group"].pk != meta.get("group_id")
                    ):
                        continue
                    path = [
                        meta.get("macro_group_name", "Без макрогруппы"),
                        meta.get("group_name", "Без группы"),
                    ]
                    identity = (obj.pk, key)
                    row_label, unit = meta["name"], meta.get("unit", "чел.")
                elif kind == "equipment":
                    pk, number = key
                    row = equipment_catalog.get(pk)
                    meta = resource_meta[kind].get(pk) or (
                        {
                            "name": row.name,
                            "unit": row.unit,
                            "category_id": row.category_id,
                            "category_name": (
                                row.category.name
                                if row.category_id
                                else "Без категории"
                            ),
                        }
                        if row
                        else {"name": "Удалённый вид техники", "unit": "ед."}
                    )
                    row_label, unit = (meta["name"] + " " + number).strip(), meta.get(
                        "unit", "ед."
                    )
                    if (
                        filters.get("equipment_q", "").casefold().strip()
                        not in row_label.casefold()
                        or filters.get("equipment_category")
                        and filters["equipment_category"].pk != meta.get("category_id")
                    ):
                        continue
                    path, identity = [meta.get("category_name", "Без категории")], (
                        obj.pk,
                        pk,
                        number,
                    )
                else:
                    if filters.get("fuel_type") and filters["fuel_type"] != key:
                        continue
                    row_label, unit = (
                        str(dict(FuelFact.FUEL_TYPE_CHOICES).get(key, key)),
                        "л",
                    )
                    path, identity = [row_label], (obj.pk, key)
                plan = {
                    day: resource_plan_value(resource_index, chosen, kind, key, day)
                    for day in all_days
                }
                fact = resource_facts[kind].get(identity, {})
                operation = "sum" if kind == "fuel" else "average"
                rows.append(
                    (
                        path,
                        make_row(
                            f"o{obj.pk}-{kind}-{key}",
                            row_label if kind != "fuel" else "Расход",
                            unit,
                            plan,
                            fact,
                            buckets,
                            operation,
                        ),
                    )
                )
                if kind == "fuel":
                    balance = {
                        day: resource_plan_value(
                            resource_index, chosen, "balance", key, day
                        )
                        for day in all_days
                    }
                    rows.append(
                        (
                            path,
                            make_row(
                                f"o{obj.pk}-balance-{key}",
                                "Остаток",
                                unit,
                                balance,
                                resource_facts["balance"].get(identity, {}),
                                buckets,
                                "last",
                            ),
                        )
                    )
            sections.append({"kind": kind, "label": label, "groups": grouped(rows)})
        used = [s for s in available if any(chosen[day] is s for day in all_days)]
        warnings = []
        for source in used:
            if source.member:
                if source.version.status not in ["APPROVED", "COMPLETED"]:
                    warnings.append(
                        f"План объекта v{source.version.version_number} возвращён на доработку. Используется сохранённый снимок сводной версии."
                    )
                elif (
                    source.member.review_id
                    and source.member.review.number != source.version.approval_round
                ):
                    warnings.append(
                        f"План объекта v{source.version.version_number} пересогласован. Используется сохранённый раунд сводной версии."
                    )
        if not covered:
            warnings.append(
                "Согласованный план для выбранного периода отсутствует; план показан прочерком."
            )
        elif len(covered) < len(all_days):
            warnings.append(
                f"План покрывает {len(covered)} из {len(all_days)} дней периода."
            )
        result.append(
            {
                "object": obj,
                "name": (
                    used[0].snapshot.get("object_name", obj.name)
                    if used and used[0].member
                    else obj.name
                ),
                "sections": sections,
                "sources": used,
                "warnings": warnings,
            }
        )
    return {
        "objects": result,
        "columns": buckets,
        "daily": bool(filters.get("month")),
        "start": start,
        "end": end,
        "column_count": 1 + 3 * (len(buckets) + 1),
    }
