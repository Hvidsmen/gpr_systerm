"""Atomic conversion of simple works into composite items with explicit lineage."""

from collections import defaultdict
from copy import deepcopy
from datetime import date
from decimal import Decimal, ROUND_DOWN
import hashlib, json
from django.core.exceptions import ValidationError
from django.db import transaction
from core.permissions import PLAN_ROLES, require_roles
from .models import ProjectWork, ProjectWorkItem, WorkMergeSource, WorkMergePlanRevision
from .progress import work_specification, quantity_from_totals, cumulative_series
from .merge_planning import transform_snapshot, preserved_snapshot


def source_works(user, ids, lock=False):
    ids = set(int(value) for value in ids)
    query = ProjectWork.objects.filter(company=user.company, pk__in=ids).select_related(
        "section", "load_profile"
    )
    if lock:
        query = query.select_for_update()
    works = list(query.order_by("pk"))
    if len(works) != len(ids) or len(works) < 2:
        raise ValidationError("Выберите не менее двух доступных работ.")
    if len(works) > 100:
        raise ValidationError("За одну операцию можно объединить до 100 работ.")
    if len({work.section.construction_object_id for work in works}) != 1:
        raise ValidationError(
            "Работы должны принадлежать одному строительному объекту."
        )
    if any(
        work.kind != "SIMPLE" or hasattr(work, "merged_source") or work.items.exists()
        for work in works
    ):
        raise ValidationError("Выберите простые работы, которые ещё не объединены.")
    return works


def fingerprint(works):
    from apps.production.models import DailyFact
    from apps.planning.models import WorkMonthAllocation, GlobalPlanVersion, DailyPlan

    ids = [w.pk for w in works]
    data = {
        "works": list(ProjectWork.objects.filter(pk__in=ids).order_by("pk").values()),
        "facts": list(
            DailyFact.objects.filter(project_work_id__in=ids).order_by("pk").values()
        ),
        "allocations": list(
            WorkMonthAllocation.objects.filter(work_id__in=ids).order_by("pk").values()
        ),
        "versions": list(
            GlobalPlanVersion.objects.filter(
                construction_object_id=works[0].section.construction_object_id
            )
            .order_by("pk")
            .values("id", "status", "snapshot", "updated_at")
        ),
        "daily": list(
            DailyPlan.objects.filter(
                plan_version__monthly_plan__project_work_id__in=ids
            )
            .order_by("pk")
            .values()
        ),
    }
    return hashlib.sha256(
        json.dumps(data, default=str, sort_keys=True).encode()
    ).hexdigest()


def preview(user, works, values):
    from apps.production.models import DailyFact
    from apps.planning.models import WorkMonthAllocation, GlobalPlanVersion

    spec = {
        "kind": "COMPOSITE",
        "allow_fractional": values["allow_fractional"],
        "items": [{"id": w.pk, "norm": str(values[f"norm_{w.pk}"])} for w in works],
    }
    rows = [
        (r.date, r.project_work_id, r.actual_quantity)
        for r in DailyFact.objects.filter(
            company=user.company, project_work__in=works
        ).order_by("date", "pk")
    ]
    series = cumulative_series(spec, rows)
    months = defaultdict(dict)
    for row in WorkMonthAllocation.objects.filter(
        company=user.company, work__in=works
    ).select_related("version"):
        months[(row.version_id, row.month)][row.work_id] = row.quantity
    plan_rows = [
        {
            "version": GlobalPlanVersion.objects.get(pk=version_id),
            "month": month,
            "quantity": quantity_from_totals(spec, totals),
            "incomplete": len(totals) != len(works),
        }
        for (version_id, month), totals in sorted(months.items())
    ]
    return {
        "fact_quantity": (
            next(reversed(series.values()))["cumulative"] if series else Decimal(0)
        ),
        "fact_days": [{"date": day, **result} for day, result in series.items()],
        "plans": plan_rows,
        "fact_records": len(rows),
        "fingerprint": fingerprint(works),
    }


def copy_allocations(source, target):
    from apps.planning.models import WorkMonthAllocation, ResourceMonthAllocation

    for model, relation in [
        (WorkMonthAllocation, "work_allocations"),
        (ResourceMonthAllocation, "resource_allocations"),
    ]:
        for row in getattr(source, relation).all():
            fields = {
                field.name: deepcopy(getattr(row, field.name))
                for field in model._meta.fields
                if field.name
                not in ["id", "company", "version", "created_at", "updated_at"]
            }
            if (
                model == WorkMonthAllocation
                and source.version_kind == "BASELINE"
                and source.snapshot
            ):
                spec = next(
                    (
                        s
                        for s in source.snapshot.get("works", [])
                        if s["id"] == row.work_id
                    ),
                    None,
                )
                if spec:
                    fields["daily_override"] = [
                        deepcopy(r)
                        for r in spec.get("plans", [])
                        if r["date"][:7] == row.month.isoformat()[:7]
                        and target.start_date.isoformat()
                        <= r["date"]
                        <= target.end_date.isoformat()
                    ]
            model.objects.create(company=source.company, version=target, **fields)


def convert_allocations(version, parent, items, snapshot):
    from apps.planning.models import WorkMonthAllocation

    rows = list(
        version.work_allocations.filter(work_id__in=items).select_related("work")
    )
    months = defaultdict(dict)
    specs = {spec["id"]: spec for spec in snapshot.get("works", [])}
    for row in rows:
        months[row.month][row.work_id] = row.quantity
    for month, totals in months.items():
        values = {items[source].pk: totals.get(source, Decimal(0)) for source in items}
        daily = []
        for source, item in items.items():
            daily.extend(
                {**row, "item_id": item.pk}
                for row in specs.get(source, {}).get("plans", [])
                if row["date"][:7] == month.isoformat()[:7]
            )
        WorkMonthAllocation.objects.create(
            company=version.company,
            version=version,
            work=parent,
            month=month,
            quantity=quantity_from_totals(work_specification(parent), values),
            item_quantities={str(k): str(v) for k, v in values.items()},
            daily_override=daily,
        )
    version.work_allocations.filter(pk__in=[row.pk for row in rows]).delete()
    version.snapshot = {}
    version.save(update_fields=["snapshot"])


def merge_lower_plans(user, parent, items):
    from apps.planning.models import MonthlyPlan, PlanVersion, DailyPlan

    months = defaultdict(list)
    for plan in MonthlyPlan.objects.filter(
        company=user.company, project_work_id__in=items
    ).prefetch_related("versions__daily_plans"):
        months[(plan.year, plan.month)].append(plan)
    for (year, month), plans in months.items():
        target = MonthlyPlan.objects.create(
            company=user.company,
            project_work=parent,
            year=year,
            month=month,
            start_date=min(p.start_date for p in plans),
            end_date=max(p.end_date for p in plans),
            planned_quantity=quantity_from_totals(
                work_specification(parent),
                {items[p.project_work_id].pk: p.planned_quantity for p in plans},
            ),
        )
        version = PlanVersion.objects.create(
            company=user.company,
            monthly_plan=target,
            version_number=1,
            created_by=user,
            comment="Объединение простых работ; требуется согласование.",
        )
        for plan in plans:
            source = plan.versions.order_by("-version_number").first()
            if source:
                for row in source.daily_plans.all():
                    DailyPlan.objects.create(
                        company=user.company,
                        plan_version=version,
                        work_item=items[plan.project_work_id],
                        date=row.date,
                        workday_number=row.workday_number,
                        planned_quantity=row.planned_quantity,
                        planned_value=row.planned_value,
                    )


@transaction.atomic
def apply_merge(user, ids, values, expected):
    from apps.projects.models import ConstructionObject
    from apps.production.models import DailyFact
    from apps.planning.models import GlobalPlanVersion
    from apps.planning.global_services import build_snapshot
    from apps.planning.workspace_services import WorkspaceService

    require_roles(user, PLAN_ROLES)
    ids = [int(value) for value in ids]
    works = source_works(user, ids, lock=True)
    if (
        values["section"].company_id != user.company_id
        or values["section"].construction_object_id
        != works[0].section.construction_object_id
    ):
        raise ValidationError("Выберите раздел того же объекта.")
    ConstructionObject.objects.select_for_update().get(
        pk=works[0].section.construction_object_id, company=user.company
    )
    if fingerprint(works) != expected:
        raise ValidationError(
            "Планы или факты изменились после просмотра. Выполните предварительный расчёт заново."
        )
    affected = []
    for version in (
        GlobalPlanVersion.objects.select_for_update()
        .filter(
            company=user.company,
            construction_object_id=works[0].section.construction_object_id,
        )
        .order_by("version_number")
    ):
        if version.workspace_id:
            contains = version.work_allocations.filter(work__in=works).exists() or any(
                s["id"] in ids for s in version.snapshot.get("works", [])
            )
        else:
            contains = (
                any(s["id"] in ids for s in version.snapshot.get("works", []))
                or version.source_versions.filter(
                    monthly_plan__project_work__in=works
                ).exists()
            )
        if contains:
            snapshot = (
                build_snapshot(version)
                if version.status in ["DRAFT", "REJECTED"]
                else deepcopy(version.snapshot)
            )
            affected.append((version, snapshot))
    parent = ProjectWork(
        company=user.company,
        section=values["section"],
        name=values["name"],
        unit=values["unit"],
        kind="COMPOSITE",
        allow_fractional=values["allow_fractional"],
        work_group=values.get("work_group"),
    )
    total_price = sum(values[f"norm_{w.pk}"] * w.unit_price for w in works)
    parent.unit_price = total_price.quantize(Decimal(".01"))
    parent.full_clean()
    parent.save()
    items = {}
    assigned_weight = Decimal(0)
    for index, work in enumerate(works):
        norm = values[f"norm_{work.pk}"]
        weight = (
            (norm * work.unit_price / total_price * 100).quantize(
                Decimal(".01"), rounding=ROUND_DOWN
            )
            if total_price
            else (Decimal(100) / len(works)).quantize(
                Decimal(".01"), rounding=ROUND_DOWN
            )
        )
        if index == len(works) - 1:
            weight = Decimal(100) - assigned_weight
        assigned_weight += weight
        item = ProjectWorkItem(
            company=user.company,
            project_work=parent,
            name=work.name,
            unit=work.unit,
            quantity_per_unit=norm,
            load_profile=values[f"profile_{work.pk}"],
            weight=weight,
            sequence=index + 1,
        )
        item.full_clean()
        item.save()
        items[work.pk] = item
        WorkMergeSource.objects.create(
            company=user.company, source_work=work, item=item, created_by=user
        )
    # Move the original records, preserving IDs, dates, comments, reporters and values.
    for source, item in items.items():
        DailyFact.objects.filter(company=user.company, project_work_id=source).update(
            project_work=parent, work_item=item
        )
    latest = {}
    editable_workspaces = {
        v.workspace_id
        for v, _ in affected
        if v.workspace_id and v.status in ["DRAFT", "REJECTED"]
    }
    for version, snapshot in affected:
        if version.status in ["DRAFT", "REJECTED"]:
            if version.workspace_id:
                convert_allocations(version, parent, items, snapshot)
                if version.version_kind == "FORECAST":
                    WorkMergePlanRevision.objects.update_or_create(
                        target_version=version,
                        defaults={
                            "company": user.company,
                            "source_version": version,
                            "parent_work": parent,
                            "seed_snapshot": preserved_snapshot(snapshot, version),
                        },
                    )
            else:
                seed = transform_snapshot(snapshot, user.company)
                WorkMergePlanRevision.objects.update_or_create(
                    target_version=version,
                    defaults={
                        "company": user.company,
                        "source_version": version,
                        "parent_work": parent,
                        "seed_snapshot": seed,
                    },
                )
                version.snapshot = seed
                version.save(update_fields=["snapshot"])
        elif version.workspace_id not in editable_workspaces:
            latest[version.workspace_id or -version.pk] = (version, snapshot)
    revisions = []
    for source, snapshot in latest.values():
        if source.workspace_id and source.version_kind == "FORECAST":
            target = WorkspaceService.new_version(
                source.workspace,
                user,
                "FORECAST",
                source.planning_month,
                source.scenario,
                source,
            )
            target.baseline_review = source.baseline_review
            target.save(update_fields=["baseline_review"])
            copy_allocations(source, target)
            convert_allocations(target, parent, items, snapshot)
        elif source.workspace_id:
            workspace = WorkspaceService.create(
                user,
                source.construction_object,
                (source.title + " · объединение")[:255],
                source.start_date,
                source.end_date,
            )
            target = workspace.baseline_version
            target.previous_version = source
            target.save(update_fields=["previous_version"])
            copy_allocations(source, target)
            convert_allocations(target, parent, items, snapshot)
        else:
            target = GlobalPlanVersion(
                company=user.company,
                construction_object=source.construction_object,
                start_date=source.start_date,
                end_date=source.end_date,
                title=source.title,
                previous_version=source,
                created_by=user,
                version_number=(
                    GlobalPlanVersion.objects.filter(
                        construction_object=source.construction_object
                    )
                    .order_by("-version_number")
                    .first()
                    .version_number
                    + 1
                ),
            )
            target.save()
            seed = transform_snapshot(snapshot, user.company)
            target.snapshot = seed
            target.save(update_fields=["snapshot"])
        seed = (
            preserved_snapshot(snapshot, target)
            if source.workspace_id and source.version_kind == "FORECAST"
            else (
                transform_snapshot(snapshot, user.company)
                if not source.workspace_id
                else {}
            )
        )
        WorkMergePlanRevision.objects.create(
            company=user.company,
            source_version=source,
            target_version=target,
            parent_work=parent,
            seed_snapshot=seed,
        )
        revisions.append(target)
    merge_lower_plans(user, parent, items)
    return parent, revisions
