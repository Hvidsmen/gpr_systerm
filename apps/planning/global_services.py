from copy import deepcopy
from core.permissions import require_roles, PLAN_ROLES
import json
from collections import defaultdict
from datetime import date
from decimal import Decimal

from django.core.exceptions import ValidationError, PermissionDenied
from django.core.serializers.json import DjangoJSONEncoder
from django.db import transaction
from django.db.models import Max

from apps.projects.models import ConstructionObject
from apps.production.models import (
    LaborPlan,
    EquipmentPlan,
    FuelPlan,
    LaborFact,
    EquipmentFact,
    FuelFact,
    DailyFact,
    LegacyResourceRecord,
)
from apps.works.progress import (
    work_specification,
    cumulative_series,
    quantity_from_totals,
)
from .models import GlobalPlanVersion, PlanVersion, DailyPlan


def json_copy(value):
    return json.loads(json.dumps(value, cls=DjangoJSONEncoder))


def source_versions(obj, start, end):
    candidates = PlanVersion.objects.filter(
        company=obj.company, monthly_plan__project_work__merged_source__isnull=True,
        monthly_plan__project_work__section__construction_object=obj,
        status__in=["APPROVED", "COMPLETED"],
        monthly_plan__start_date__lte=end,
        monthly_plan__end_date__gte=start,
    ).order_by("monthly_plan_id", "-version_number")
    chosen = {}
    for version in candidates:
        chosen.setdefault(version.monthly_plan_id, version)
    return list(chosen.values())


def build_snapshot(version):
    from apps.works.prices import freeze_prices
    return freeze_prices(_build_snapshot(version), version.company)


def _build_snapshot(version):
    if not version.workspace_id:
        from apps.works.models import WorkMergePlanRevision
        revision = WorkMergePlanRevision.objects.filter(company=version.company, target_version=version).first()
        if revision and revision.seed_snapshot:
            return deepcopy(revision.seed_snapshot)
    if version.workspace_id:
        from .workspace_services import build_workspace_snapshot

        return build_workspace_snapshot(version)
    obj = version.construction_object
    if LegacyResourceRecord.objects.filter(
        company=version.company, resolved=False
    ).exists():
        raise ValidationError(
            "В компании есть ресурсные записи без назначенного объекта. Сначала завершите перенос данных."
        )
    versions = list(
        version.source_versions.select_related("monthly_plan__project_work")
    )
    if not versions:
        versions = source_versions(obj, version.start_date, version.end_date)
    monthly_ids = set()
    by_work = defaultdict(list)
    for lower in versions:
        work = lower.monthly_plan.project_work
        if (
            lower.company_id != version.company_id
            or work.section.construction_object_id != obj.pk
            or lower.status not in ["APPROVED", "COMPLETED"]
        ):
            raise ValidationError(
                "В глобальную версию можно включать только утверждённые планы выбранного объекта."
            )
        if lower.monthly_plan_id in monthly_ids:
            raise ValidationError("Нельзя включить две версии одного месячного плана.")
        monthly_ids.add(lower.monthly_plan_id)
        if (
            lower.monthly_plan.start_date > version.end_date
            or lower.monthly_plan.end_date < version.start_date
        ):
            raise ValidationError("План не пересекается с периодом глобальной версии.")
        by_work[work.pk].append(lower)
    works = []
    for work_id, lower_versions in by_work.items():
        work = lower_versions[0].monthly_plan.project_work
        spec = work_specification(work)
        quantity_from_totals(spec, {})
        historical = source_versions(obj, date.min, version.start_date)
        context = [
            v
            for v in historical
            if v.monthly_plan.project_work_id == work_id
            and v.monthly_plan.end_date < version.start_date
        ]
        all_versions = context + lower_versions
        all_rows = list(
            DailyPlan.objects.filter(
                company=version.company,
                plan_version__in=all_versions,
                date__lte=version.end_date,
            ).order_by("date", "pk")
        )
        occupied = set()
        for lower in all_versions:
            for row in lower.daily_plans.filter(date__lte=version.end_date):
                key = (row.date, row.work_item_id)
                if key in occupied:
                    raise ValidationError(
                        "Планы одной работы перекрываются по датам. Выберите непересекающиеся версии."
                    )
                occupied.add(key)
        if not all_rows:
            raise ValidationError(f"У работы «{work.name}» нет дневного плана.")
        series = cumulative_series(
            spec,
            [(row.date, row.work_item_id, row.planned_quantity) for row in all_rows],
        )
        spec["versions"] = [
            {
                "id": lower.pk,
                "number": lower.version_number,
                "historical": lower in context,
            }
            for lower in all_versions
        ]
        spec["plans"] = [
            {
                "date": row.date.isoformat(),
                "item_id": row.work_item_id,
                "quantity": str(row.planned_quantity),
                "value": str(row.planned_value),
            }
            for row in all_rows
            if version.start_date <= row.date <= version.end_date
        ]
        spec["daily"] = [
            {
                "date": day.isoformat(),
                "quantity": str(values["daily"]),
                "cumulative": str(values["cumulative"]),
            }
            for day, values in series.items()
            if version.start_date <= day <= version.end_date
        ]
        works.append(spec)
    resources = {}
    definitions = [
        (
            "labor",
            LaborPlan,
            ["brigade_id", "planned_workers", "planned_hours", "hourly_rate"],
        ),
        (
            "equipment",
            EquipmentPlan,
            [
                "equipment_type_id",
                "equipment_number",
                "planned_count",
                "planned_machine_hours",
                "hourly_rate",
            ],
        ),
        (
            "fuel",
            FuelPlan,
            ["fuel_type", "equipment_ref", "planned_balance", "planned_liters", "price_per_liter"],
        ),
    ]
    for kind, model, fields in definitions:
        resources[kind] = []
        for row in model.objects.filter(
            company=version.company,
            construction_object=obj,
            date__range=(version.start_date, version.end_date),
        ).order_by("date", "pk"):
            payload = {name: getattr(row, name) for name in fields}
            payload.update(
                id=row.pk,
                date=row.date.isoformat(),
                label=(
                    str(row.brigade)
                    if kind == "labor"
                    else (
                        str(row.equipment_type)
                        if kind == "equipment"
                        else row.get_fuel_type_display()
                    )
                ),
                comment=row.comment,
            )
            resources[kind].append(json_copy(payload))
    if not works and not any(resources.values()):
        raise ValidationError(
            "В выбранном периоде нет утверждённых планов работ или ресурсных планов."
        )
    return {
        "schema": 1,
        "object_id": obj.pk,
        "object_name": obj.name,
        "start": version.start_date.isoformat(),
        "end": version.end_date.isoformat(),
        "works": works,
        "resources": resources,
    }


class GlobalPlanService:
    @staticmethod
    @transaction.atomic
    def create(user, obj, start, end, title="", versions=None, previous=None):
        require_roles(user, PLAN_ROLES)
        if obj.company_id != user.company_id:
            raise PermissionDenied("Объект другой компании.")
        ConstructionObject.objects.select_for_update().get(pk=obj.pk)
        number = (
            GlobalPlanVersion.objects.filter(construction_object=obj).aggregate(
                value=Max("version_number")
            )["value"]
            or 0
        ) + 1
        result = GlobalPlanVersion(
            company=user.company,
            construction_object=obj,
            start_date=start,
            end_date=end,
            title=title,
            version_number=number,
            created_by=user,
            previous_version=previous,
        )
        result.full_clean(exclude=["snapshot"])
        result.save()
        result.source_versions.set(versions or source_versions(obj, start, end))
        result.snapshot = build_snapshot(result)
        result.save(update_fields=["snapshot"])
        return result

    @staticmethod
    @transaction.atomic
    def transition(version, user, action, comment=""):
        from .approval_workflow import transition
        return transition(version, user, action, comment)

    @staticmethod
    def revision(version, user):
        require_roles(user, PLAN_ROLES)
        if version.company_id != user.company_id or not version.is_immutable:
            raise ValidationError(
                "Редакцию можно создать только от своей утверждённой версии."
            )
        if version.workspace_id:
            from .workspace_services import WorkspaceService

            if version.version_kind != "FORECAST":
                raise ValidationError(
                    "Для нового базового плана создайте новое рабочее пространство."
                )
            return WorkspaceService.forecast(
                version.workspace,
                user,
                version.planning_month,
                version.scenario,
                previous=version,
            )
        # Use the currently approved lower versions; the previous snapshot remains unchanged.
        return GlobalPlanService.create(
            user,
            version.construction_object,
            version.start_date,
            version.end_date,
            version.title,
            previous=version,
        )


def comparison(version):
    start, end = version.start_date, version.end_date
    works = []
    for spec in version.snapshot.get("works", []):
        from apps.works.progress import historical_fact_rows
        series = cumulative_series(spec, historical_fact_rows(version.company, spec["id"], end))
        planned = {row["date"]: Decimal(row["quantity"]) for row in spec["daily"]}
        actual = {
            day.isoformat(): values["daily"]
            for day, values in series.items()
            if start <= day <= end
        }
        days = sorted(set(planned) | set(actual))
        works.append(
            {
                "spec": spec,
                "plan": sum(planned.values(), Decimal("0")),
                "fact": sum(actual.values(), Decimal("0")),
                "days": [
                    {
                        "date": day,
                        "plan": planned.get(day, Decimal("0")),
                        "fact": actual.get(day, Decimal("0")),
                    }
                    for day in days
                ],
            }
        )
    resources = []
    for kind, fact_model, identity, plan_field, fact_field in (
        ("labor", LaborFact, ["brigade_id"], "planned_workers", "actual_workers"),
        (
            "equipment",
            EquipmentFact,
            ["equipment_type_id", "equipment_number"],
            "planned_count",
            "actual_count",
        ),
        (
            "fuel",
            FuelFact,
            ["fuel_type", "equipment_ref"],
            "planned_liters",
            "actual_liters",
        ),
    ):
        planned_rows = {
            tuple([r["date"]] + [r[k] for k in identity]): r
            for r in version.snapshot.get("resources", {}).get(kind, [])
        }
        facts = {
            tuple([r.date.isoformat()] + [getattr(r, k) for k in identity]): r
            for r in fact_model.objects.filter(
                company=version.company,
                construction_object=version.construction_object,
                date__range=(start, end),
            )
        }
        for key in sorted(set(planned_rows) | set(facts), key=str):
            planned = planned_rows.get(key)
            fact = facts.get(key)
            resources.append(
                {
                    "kind": kind,
                    "date": key[0],
                    "label": planned["label"] if planned else str(fact),
                    "plan": (
                        Decimal(str(planned.get(plan_field) or 0))
                        if planned
                        else Decimal("0")
                    ),
                    "fact": (
                        getattr(fact, fact_field) or Decimal("0")
                        if fact
                        else Decimal("0")
                    ),
                    "planned_balance": Decimal(str(planned.get("planned_balance") or 0)) if planned and kind == "fuel" else Decimal("0"),
                    "actual_balance": fact.actual_balance if fact and kind == "fuel" else Decimal("0"),
                    "plan_details": planned,
                    "fact_record": fact,
                }
            )
    for row in works:
        rate = Decimal(row["spec"]["unit_price"])
        row["plan_value"] = (row["plan"] * rate).quantize(Decimal("0.01"))
        row["fact_value"] = (row["fact"] * rate).quantize(Decimal("0.01"))
    for row in resources:
        plan = row["plan_details"] or {}
        fact = row["fact_record"]
        kind = row["kind"]
        planned_hours = plan.get(
            "planned_hours" if kind == "labor" else "planned_machine_hours"
        )
        actual_hours = (
            getattr(fact, "actual_hours" if kind == "labor" else "machine_hours", None)
            if fact
            else None
        )
        row["planned_hours"] = Decimal(str(planned_hours or 0))
        row["actual_hours"] = actual_hours or Decimal("0")
        row["planned_rate"] = Decimal(
            str(plan.get("price_per_liter" if kind == "fuel" else "hourly_rate") or 0)
        )
        row["actual_rate"] = (
            (
                getattr(fact, "price_per_liter" if kind == "fuel" else "hourly_rate")
                or Decimal("0")
            )
            if fact
            else Decimal("0")
        )
        row["plan_value"] = (
            (row["plan"] if kind == "fuel" else row["planned_hours"])
            * row["planned_rate"]
        ).quantize(Decimal("0.01"))
        row["fact_value"] = (
            (row["fact"] if kind == "fuel" else row["actual_hours"])
            * row["actual_rate"]
        ).quantize(Decimal("0.01"))
    return {"works": works, "resources": resources}
