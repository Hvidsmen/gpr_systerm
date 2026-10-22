"""Adapt live draft planning to merge lineage without modifying approved snapshots."""

from collections import defaultdict
from copy import deepcopy
from datetime import date
from decimal import Decimal
from .models import WorkMergeSource
from .progress import cumulative_series, quantity_from_totals


def transform_snapshot(snapshot, company):
    from apps.planning.workspace_services import specification

    result = deepcopy(snapshot)
    links = list(
        WorkMergeSource.objects.filter(company=company).select_related(
            "item__project_work"
        )
    )
    mapping = {link.source_work_id: link.item for link in links}
    parent_ids = {item.project_work_id for item in mapping.values()}
    originals = {spec["id"]: spec for spec in result.get("works", [])}
    grouped = defaultdict(list)
    retained = []
    for spec in result.get("works", []):
        item = mapping.get(spec["id"])
        if item:
            grouped[item.project_work_id].append((spec, item))
        elif spec["id"] in parent_ids:
            grouped[spec["id"]].append((spec, None))
        else:
            retained.append(spec)
    for parent_id, sources in grouped.items():
        from .models import ProjectWork
        parent = ProjectWork.objects.get(company=company,pk=parent_id)
        spec = specification(parent)
        rows, context = [], []
        for original, item in sources:
            for field, dest in [("plans", rows), ("context_plans", context)]:
                dest.extend(
                    ({**row, "item_id": item.pk} if item else deepcopy(row)) for row in original.get(field, [])
                )
        spec["plans"], spec["context_plans"] = rows, context
        spec["daily"] = [
            {
                "date": day.isoformat(),
                "quantity": str(values["daily"]),
                "cumulative": str(values["cumulative"]),
            }
            for day, values in cumulative_series(
                spec,
                [
                    (
                        date.fromisoformat(r["date"]),
                        r["item_id"],
                        Decimal(r["quantity"]),
                    )
                    for r in context + rows
                ],
            ).items()
            if snapshot["start"] <= day.isoformat() <= snapshot["end"]
        ]
        retained.append(spec)
    result["works"] = retained
    inputs = result.get("monthly_inputs", {}).get("works", [])
    kept, monthly = [], defaultdict(dict)
    for row in inputs:
        item = mapping.get(row["work_id"])
        if item:
            monthly[(item.project_work_id, row["month"])][item.pk] = Decimal(
                row["quantity"]
            )
        elif row['work_id'] in parent_ids:
            quantities = row.get('item_quantities')
            if not quantities:
                quantities = {str(item['id']):str(Decimal(item['norm'])*Decimal(row['quantity'])) for item in originals.get(row['work_id'],{}).get('items',[])}
            monthly[(row['work_id'],row['month'])].update({int(key):Decimal(value) for key,value in quantities.items()})
        else:
            kept.append(row)
    specs = {spec["id"]: spec for spec in retained}
    for (parent_id, month), totals in monthly.items():
        spec = specs[parent_id]
        values = {
            item["id"]: totals.get(item["id"], Decimal(0)) for item in spec["items"]
        }
        kept.append(
            {
                "work_id": parent_id,
                "month": month,
                "quantity": str(quantity_from_totals(spec, values)),
                "item_quantities": {str(k): str(v) for k, v in values.items()},
                "load_profile_id": None,
                "profile": None,
            }
        )
    if "monthly_inputs" in result:
        result["monthly_inputs"]["works"] = kept
    return result


def input_fingerprint(version):
    import hashlib, json

    works = list(
        version.work_allocations.order_by("work_id", "month").values(
            "work_id",
            "month",
            "quantity",
            "item_quantities",
            "load_profile_id",
            "daily_override",
        )
    )
    resources = list(
        version.resource_allocations.order_by("kind", "month", "pk").values()
    )
    for row in resources:
        for field in ["id", "company_id", "version_id", "created_at", "updated_at"]:
            row.pop(field, None)
    from apps.planning.workspace_services import specification

    from .models import ProjectWork

    metadata = [
        specification(work)
        for work in ProjectWork.objects.filter(
            company=version.company, pk__in={row["work_id"] for row in works}
        ).order_by("pk")
    ]
    history = []
    if version.version_kind == "FORECAST" and version.scenario in {"REMAINING", "ENTERED"}:
        from apps.production.models import DailyFact, LaborFact, EquipmentFact, FuelFact

        for model in [DailyFact, LaborFact, EquipmentFact, FuelFact]:
            scope = (
                {
                    "project_work__section__construction_object_id": version.construction_object_id
                }
                if model == DailyFact
                else {"construction_object_id": version.construction_object_id}
            )
            history.append(
                list(
                    model.objects.filter(
                        company=version.company,
                        date__lt=version.planning_month,
                        **scope
                    )
                    .order_by("pk")
                    .values()
                )
            )
    return hashlib.sha256(
        json.dumps(
            {
                "works": works,
                "resources": resources,
                **({"resource_assignments":list(version.resource_assignments.order_by("pk").values())} if version.resource_assignments.exists() else {}),
                "metadata": metadata,
                "history": history,
            },
            sort_keys=True,
            default=str,
        ).encode()
    ).hexdigest()


def preserved_snapshot(source_snapshot, version):
    result = transform_snapshot(source_snapshot, version.company)
    result["_merge_input_fingerprint"] = input_fingerprint(version)
    result["workspace_id"] = version.workspace_id
    return result
