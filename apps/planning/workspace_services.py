from core.permissions import require_roles, PLAN_ROLES, APPROVAL_ROLES
"""Period planning from monthly inputs, with immutable baseline and forecasts."""

from collections import defaultdict
from copy import deepcopy
from calendar import monthrange
from datetime import date, timedelta
from decimal import Decimal, ROUND_DOWN

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Max
from django.utils import timezone

from apps.projects.models import ConstructionObject
from apps.works.models import ProjectWork
from apps.works.progress import (
    cumulative_series,
    work_specification,
    quantity_from_totals,
)
from apps.production.models import (
    DailyFact,
    LaborFact,
    EquipmentFact,
    FuelFact,
    LegacyResourceRecord,
)
from .models import (
    PlanningWorkspace,
    GlobalPlanVersion,
    WorkMonthAllocation,
    ResourceMonthAllocation,
    CalendarDay,
    ProductionCalendar,
    LoadProfile,
)
from .global_services import json_copy

ZERO = Decimal("0")


def month_start(day):
    return day.replace(day=1)


def months_between(start, end):
    cursor = month_start(start)
    result = []
    while cursor <= end:
        result.append(cursor)
        if (cursor.year, cursor.month) == (end.year, end.month):
            break
        cursor = date(cursor.year + cursor.month // 12, cursor.month % 12 + 1, 1)
    return result


def month_bounds(workspace, month):
    return max(workspace.start_date, month), min(
        workspace.end_date,
        date(month.year, month.month, monthrange(month.year, month.month)[1]),
    )


def allocate(total, weights, quantum=Decimal(".001")):
    """Round downward and put the exact remainder on the last nonzero weight."""
    total = Decimal(total).quantize(quantum)
    weights = [Decimal(str(w)) for w in weights]
    if total < 0 or any(not w.is_finite() or w < 0 for w in weights):
        raise ValidationError(
            "Объёмы и веса распределения должны быть неотрицательными."
        )
    if not weights or sum(weights) == 0:
        if total:
            raise ValidationError(
                "Невозможно распределить ненулевой остаток по нулевым пропорциям."
            )
        return [ZERO for _ in weights]
    result = [
        (total * w / sum(weights)).quantize(quantum, rounding=ROUND_DOWN)
        for w in weights
    ]
    result[max(i for i, w in enumerate(weights) if w)] += total - sum(result)
    return result


def working_days(workspace, month):
    start, end = month_bounds(workspace, month)
    calendar = ProductionCalendar.objects.filter(
        company=workspace.company, year=month.year, is_default=True
    ).first()
    if not calendar:
        raise ValidationError(f"Нужен календарь по умолчанию на {month.year} год.")
    days = list(
        CalendarDay.objects.filter(
            company=workspace.company,
            calendar=calendar,
            date__range=(start, end),
            is_working=True,
        )
        .order_by("date")
        .values_list("date", flat=True)
    )
    if not days:
        raise ValidationError(
            f"В {month:%m.%Y} нет рабочих дней внутри периода. Настройте календарь."
        )
    return days


def captured_profile(profile):
    if not profile:
        return []
    percentages = list(
        profile.items.order_by("workday_number").values_list("percentage", flat=True)
    )
    if not percentages or sum(percentages) <= 0 or any(p < 0 for p in percentages):
        raise ValidationError(
            f"Профиль «{profile}» должен содержать положительную сумму неотрицательных процентов."
        )
    return [str(value) for value in percentages]


def profile_weights(percentages, count):
    if not percentages:
        return [Decimal("1")] * count
    return [
        Decimal(percentages[min(i * len(percentages) // count, len(percentages) - 1)])
        for i in range(count)
    ]


def work_rows(workspace, spec, month, quantity, override_profile=None):
    quantity = Decimal(str(quantity))
    if quantity == 0:
        return []
    days = working_days(workspace, month)
    override = captured_profile(override_profile) if override_profile else None
    items = (
        spec["items"]
        if spec["kind"] == "COMPOSITE"
        else [{"id": None, "norm": "1", "weight": "100"}]
    )
    rows = []
    for item in items:
        percentages = (
            override
            if override is not None
            else spec.get("profiles", {}).get(str(item["id"]), [])
        )
        weights = profile_weights(percentages, len(days))
        quantities = allocate(
            quantity * Decimal(item["norm"]),
            weights,
            Decimal(".000001") if spec["kind"] == "COMPOSITE" else Decimal(".001"),
        )
        values = allocate(
            quantity * Decimal(spec["unit_price"]) * Decimal(item["weight"]) / 100,
            weights,
            Decimal(".01"),
        )
        rows += [
            {
                "date": day.isoformat(),
                "item_id": item["id"],
                "quantity": str(qty),
                "value": str(value),
                "origin": "PLAN",
            }
            for day, qty, value in zip(days, quantities, values)
        ]
    return rows


def specification(work):
    spec = work_specification(work)
    quantity_from_totals(spec, {})
    spec["profiles"] = (
        {str(item.pk): captured_profile(item.load_profile) for item in work.items.all()}
        if work.kind == "COMPOSITE"
        else {"None": captured_profile(work.load_profile)}
    )
    spec["versions"] = []
    return spec


def work_fact_rows(version, spec, before):
    rows = DailyFact.objects.filter(
        company=version.company, project_work_id=spec["id"], date__lt=before
    ).order_by("date", "pk")
    return [
        {
            "date": r.date.isoformat(),
            "item_id": r.work_item_id,
            "quantity": str(r.actual_quantity),
            "value": str(r.actual_value),
            "origin": "FACT",
            "fact_id": r.pk,
        }
        for r in rows
    ]


def finalize_work(spec, rows, workspace, context=None):
    spec = deepcopy(spec)
    rows = sorted(rows, key=lambda r: (r["date"], r["item_id"] or 0))
    context = context or []
    series = cumulative_series(
        spec,
        [
            (date.fromisoformat(r["date"]), r["item_id"], Decimal(r["quantity"]))
            for r in context + rows
        ],
    )
    spec["plans"] = rows
    spec["context_plans"] = context
    spec["daily"] = [
        {
            "date": day.isoformat(),
            "quantity": str(values["daily"]),
            "cumulative": str(values["cumulative"]),
        }
        for day, values in series.items()
        if workspace.start_date <= day <= workspace.end_date
    ]
    return spec


RESOURCE_CONFIG = {
    "labor": (
        "planned_hours",
        "actual_hours",
        LaborFact,
        ["brigade_id"],
        "planned_workers",
        "actual_workers",
    ),
    "equipment": (
        "planned_machine_hours",
        "machine_hours",
        EquipmentFact,
        ["equipment_type_id", "equipment_number"],
        "planned_count",
        "actual_count",
    ),
    "fuel": (
        "planned_liters",
        "actual_liters",
        FuelFact,
        ["fuel_type", "equipment_ref"],
        None,
        None,
    ),
}


def resource_identity(row, kind):
    return tuple(row.get(key) for key in RESOURCE_CONFIG[kind][3])


def resource_input(row):
    return {
        "profile": [],
        "month": row.month.isoformat(),
        "kind": row.kind,
        "brigade_id": row.brigade_id,
        "equipment_type_id": row.equipment_type_id,
        "equipment_number": row.equipment_number,
        "fuel_type": row.fuel_type,
        "equipment_ref": row.equipment_ref,
        "count": row.count,
        "hours": str(row.hours),
        "liters": str(row.liters),
        "balance": str(row.balance),
        "rate": str(row.rate),
        "label": (
            str(row.brigade)
            if row.kind == "labor"
            else (
                str(row.equipment_type)
                if row.kind == "equipment"
                else row.get_fuel_type_display()
            )
        ),
    }


def resource_rows(workspace, source, month, total=None, days=None, rate=None):
    kind = source["kind"]
    amount = (
        Decimal(source["liters"] if kind == "fuel" else source["hours"])
        if total is None
        else total
    )
    count = source["count"] if kind != "fuel" else 0
    if not amount and not count and not Decimal(str(source.get("balance") or 0)):
        return []
    start, end = month_bounds(workspace, month)
    days = [start + timedelta(days=i) for i in range((end - start).days + 1)]
    amounts = allocate(amount, [Decimal("1")] * len(days), Decimal(".01"))
    field = RESOURCE_CONFIG[kind][0]
    result = []
    for day, value in zip(days, amounts):
        row = {key: source[key] for key in RESOURCE_CONFIG[kind][3]}
        row.update(
            date=day.isoformat(), label=source["label"], comment="", origin="PLAN"
        )
        row[field] = str(value)
        if kind == "fuel":
            row["planned_balance"] = str(source.get("balance") or 0)
        row["price_per_liter" if kind == "fuel" else "hourly_rate"] = (
            source["rate"] if rate is None else rate
        )
        if kind != "fuel":
            row[RESOURCE_CONFIG[kind][4]] = count
        result.append(row)
    return result


def resource_facts(version, before):
    result = {kind: [] for kind in RESOURCE_CONFIG}
    for kind, (
        plan_field,
        actual_field,
        model,
        identity,
        plan_count,
        actual_count,
    ) in RESOURCE_CONFIG.items():
        for fact in model.objects.filter(
            company=version.company,
            construction_object=version.construction_object,
            date__gte=version.start_date,
            date__lt=before,
        ).order_by("date", "pk"):
            row = {key: getattr(fact, key) for key in identity}
            row.update(
                date=fact.date.isoformat(),
                label=(
                    str(fact.brigade)
                    if kind == "labor"
                    else (
                        str(fact.equipment_type)
                        if kind == "equipment"
                        else fact.get_fuel_type_display()
                    )
                ),
                comment=fact.comment,
                origin="FACT",
                fact_id=fact.pk,
            )
            row[plan_field] = str(getattr(fact, actual_field) or 0)
            if kind == "fuel":
                row["planned_balance"] = str(fact.actual_balance)
            row["price_per_liter" if kind == "fuel" else "hourly_rate"] = str(
                getattr(fact, "price_per_liter" if kind == "fuel" else "hourly_rate")
                or 0
            )
            if plan_count:
                row[plan_count] = getattr(fact, actual_count) or 0
            result[kind].append(row)
    return result


def check_inputs(version):
    if LegacyResourceRecord.objects.filter(
        company=version.company, resolved=False
    ).exists():
        raise ValidationError(
            "Сначала завершите перенос ресурсных записей без объекта."
        )
    for row in list(
        version.work_allocations.select_related(
            "version", "work__section", "load_profile"
        )
    ) + list(
        version.resource_allocations.select_related(
            "version", "brigade", "equipment_type"
        )
    ):
        row.full_clean()


def build_workspace_snapshot(version):
    workspace = version.workspace
    check_inputs(version)
    inputs = list(version.work_allocations.select_related("work", "load_profile"))
    resources = [
        resource_input(r)
        for r in version.resource_allocations.select_related(
            "brigade", "equipment_type"
        )
    ]
    if version.version_kind == "BASELINE":
        if not inputs and not resources:
            raise ValidationError("Добавьте месячные планы работ или ресурсов.")
        works = []
        for work_id in sorted({r.work_id for r in inputs}):
            work = next(r.work for r in inputs if r.work_id == work_id)
            spec = specification(work)
            rows = []
            for allocation in inputs:
                if allocation.work_id == work_id:
                    rows += work_rows(
                        workspace,
                        spec,
                        allocation.month,
                        allocation.quantity,
                        allocation.load_profile,
                    )
            works.append(finalize_work(spec, rows, workspace))
        resource_plans = {kind: [] for kind in RESOURCE_CONFIG}
        for source in resources:
            resource_plans[source["kind"]] += resource_rows(
                workspace, source, date.fromisoformat(source["month"])
            )
        warnings = []
    else:
        baseline = workspace.baseline_version
        if not baseline or not baseline.is_immutable:
            raise ValidationError("Сначала утвердите базовую версию периода.")
        works, resource_plans, warnings = build_forecast(
            version, inputs, resources, baseline.snapshot
        )
    return json_copy(
        {
            "schema": 2,
            "workspace_id": workspace.pk,
            "object_id": workspace.construction_object_id,
            "object_name": workspace.construction_object.name,
            "start": workspace.start_date.isoformat(),
            "end": workspace.end_date.isoformat(),
            "version_kind": version.version_kind,
            "planning_month": version.planning_month,
            "scenario": version.scenario,
            "baseline_id": workspace.baseline_version_id,
            "built_at": timezone.now(),
            "works": works,
            "resources": resource_plans,
            "warnings": warnings,
            "monthly_inputs": {
                "works": [
                    {
                        "work_id": r.work_id,
                        "month": r.month.isoformat(),
                        "quantity": str(r.quantity),
                        "load_profile_id": r.load_profile_id,
                        "profile": (
                            captured_profile(r.load_profile)
                            if r.load_profile_id
                            else None
                        ),
                    }
                    for r in inputs
                ],
                "resources": resources,
            },
        }
    )


def build_forecast(version, inputs, resources, base):
    workspace = version.workspace
    target = version.planning_month
    future = [
        m
        for m in months_between(workspace.start_date, workspace.end_date)
        if m > target
    ]
    works = []
    warnings = []
    base_inputs = base.get("monthly_inputs", {}).get("works", [])
    base_specs = {spec["id"]: spec for spec in base["works"]}
    for row in inputs:
        if row.work_id not in base_specs:
            new_spec = specification(row.work)
            new_spec.update(plans=[], daily=[])
            base_specs[row.work_id] = new_spec
    if any(r.month != target for r in inputs) and version.scenario != "REMAINING":
        raise ValidationError(
            "В сценарии сохранения базы редактируется только выбранный месяц."
        )
    for work_id, spec in base_specs.items():
        current = next(
            (r for r in inputs if r.work_id == work_id and r.month == target), None
        )
        if not current:
            raise ValidationError(
                f'Укажите план на выбранный месяц для работы «{spec["name"]}», включая нулевой объём.'
            )
        rows = work_rows(
            workspace, spec, target, current.quantity, current.load_profile
        )
        context = []
        if version.scenario == "BASELINE":
            rows += [
                deepcopy(r)
                for r in spec["plans"]
                if month_start(date.fromisoformat(r["date"])) != target
            ]
        else:
            facts = work_fact_rows(version, spec, target)
            past = [
                r
                for r in facts
                if date.fromisoformat(r["date"]) >= workspace.start_date
            ]
            context = [
                r for r in facts if date.fromisoformat(r["date"]) < workspace.start_date
            ]
            series = cumulative_series(
                spec,
                [
                    (
                        date.fromisoformat(r["date"]),
                        r["item_id"],
                        Decimal(r["quantity"]),
                    )
                    for r in facts
                ],
            )
            past_total = sum(
                v["daily"] for d, v in series.items() if d >= workspace.start_date
            )
            goal = sum(Decimal(r["quantity"]) for r in spec["daily"])
            rest = goal - past_total - current.quantity
            if rest < 0:
                warnings.append(
                    f'{spec["name"]}: превышение общего объёма {abs(rest)} {spec["unit"]}; будущий остаток равен нулю.'
                )
            rest = max(ZERO, rest)
            weights = [
                sum(
                    Decimal(r["quantity"])
                    for r in base_inputs
                    if r["work_id"] == work_id and r["month"] == m.isoformat()
                )
                for m in future
            ]
            manual = {
                r.month: r for r in inputs if r.work_id == work_id and r.month > target
            }
            if manual and sum(weights) > 0:
                raise ValidationError(
                    f'{spec["name"]}: будущие месяцы распределяются по базовым пропорциям; ручные строки удалите.'
                )
            if rest and not future:
                raise ValidationError(
                    f'{spec["name"]}: остаток {rest} {spec["unit"]} не помещается в период. Увеличьте план последнего месяца.'
                )
            if sum(weights) == 0 and rest:
                quantities = [
                    manual[m].quantity if m in manual else ZERO for m in future
                ]
                if sum(quantities) != rest:
                    raise ValidationError(
                        f'{spec["name"]}: базовые пропорции будущих месяцев нулевые. Распределите вручную остаток {rest} {spec["unit"]}.'
                    )
            else:
                if manual and any(r.quantity for r in manual.values()):
                    raise ValidationError(
                        f'{spec["name"]}: ручной объём задан при нулевом остатке.'
                    )
                quantities = allocate(rest, weights)
            for month, qty in zip(future, quantities):
                future_spec = deepcopy(spec)
                base_month = next(
                    (
                        r
                        for r in base_inputs
                        if r["work_id"] == work_id and r["month"] == month.isoformat()
                    ),
                    None,
                )
                if base_month and base_month.get("profile") is not None:
                    future_spec["profiles"] = (
                        {
                            str(item["id"]): base_month["profile"]
                            for item in spec["items"]
                        }
                        if spec["kind"] == "COMPOSITE"
                        else {"None": base_month["profile"]}
                    )
                rows += work_rows(
                    workspace,
                    future_spec,
                    month,
                    qty,
                    manual[month].load_profile if month in manual else None,
                )
            rows += past
        works.append(finalize_work(spec, rows, workspace, context))
    if version.scenario == "REMAINING":
        extra_works = (
            ProjectWork.objects.filter(
                company=version.company,
                section__construction_object=version.construction_object,
                daily_facts__date__gte=workspace.start_date,
                daily_facts__date__lt=target,
            )
            .exclude(pk__in=base_specs)
            .distinct()
        )
        for work in extra_works:
            spec = work_specification(work)
            spec["versions"] = []
            facts = work_fact_rows(version, spec, target)
            past = [
                r
                for r in facts
                if date.fromisoformat(r["date"]) >= workspace.start_date
            ]
            context = [
                r for r in facts if date.fromisoformat(r["date"]) < workspace.start_date
            ]
            works.append(finalize_work(spec, past, workspace, context))
            warnings.append(
                f"{work.name}: в базовом плане отсутствует; прошлый факт включён в новую версию."
            )
    resource_plans = forecast_resources(version, resources, base, future, warnings)
    return works, resource_plans, warnings


def forecast_resources(version, inputs, base, future, warnings):
    target = version.planning_month
    workspace = version.workspace
    if (
        any(date.fromisoformat(r["month"]) != target for r in inputs)
        and version.scenario != "REMAINING"
    ):
        raise ValidationError(
            "В сценарии сохранения базы ресурсы редактируются только в выбранном месяце."
        )
    result = (
        resource_facts(version, target)
        if version.scenario == "REMAINING"
        else {
            kind: [
                deepcopy(r)
                for r in base["resources"].get(kind, [])
                if month_start(date.fromisoformat(r["date"])) != target
            ]
            for kind in RESOURCE_CONFIG
        }
    )
    for source in inputs:
        if date.fromisoformat(source["month"]) == target:
            result[source["kind"]] += resource_rows(workspace, source, target)
    if version.scenario == "BASELINE":
        return result
    base_sources = base.get("monthly_inputs", {}).get("resources", [])
    for kind, config in RESOURCE_CONFIG.items():
        field = config[0]
        identities = (
            {resource_identity(r, kind) for r in base_sources if r["kind"] == kind}
            | {resource_identity(r, kind) for r in inputs if r["kind"] == kind}
            | {
                resource_identity(r, kind)
                for r in result[kind]
                if date.fromisoformat(r["date"]) < target
            }
        )
        for identity in identities:
            sources = [
                r
                for r in base_sources
                if r["kind"] == kind and resource_identity(r, kind) == identity
            ]
            current_sources = [
                r
                for r in inputs
                if r["kind"] == kind and resource_identity(r, kind) == identity
            ]
            past = [
                r
                for r in result[kind]
                if resource_identity(r, kind) == identity
                and date.fromisoformat(r["date"]) < target
            ]
            representative = (sources + current_sources + past)[0]
            current = next(
                (
                    r
                    for r in current_sources
                    if date.fromisoformat(r["month"]) == target
                ),
                None,
            )
            input_field = "liters" if kind == "fuel" else "hours"
            goal = sum(Decimal(r[input_field]) for r in sources)
            spent = sum(Decimal(r[field]) for r in past)
            chosen = Decimal(current[input_field]) if current else ZERO
            remaining = goal - spent - chosen
            if remaining < 0:
                warnings.append(
                    f'{representative["label"]}: превышение бюджета {abs(remaining)} {"л" if kind=="fuel" else "ч"}; будущий остаток равен нулю.'
                )
            remaining = max(ZERO, remaining)
            base_months = {date.fromisoformat(r["month"]): r for r in sources}
            weights = [
                Decimal(base_months[m][input_field]) if m in base_months else ZERO
                for m in future
            ]
            manual = {
                date.fromisoformat(r["month"]): r
                for r in current_sources
                if date.fromisoformat(r["month"]) > target
            }
            if manual and sum(weights):
                raise ValidationError(
                    f'{representative["label"]}: будущие ресурсы распределяются по базовым пропорциям; ручные строки удалите.'
                )
            if remaining and not future:
                raise ValidationError(
                    f'{representative["label"]}: остаток {remaining} не помещается в период. Увеличьте план последнего месяца.'
                )
            if not sum(weights) and remaining:
                quantities = [
                    Decimal(manual[m][input_field]) if m in manual else ZERO
                    for m in future
                ]
                if sum(quantities) != remaining:
                    raise ValidationError(
                        f'{representative["label"]}: будущие базовые пропорции нулевые. Распределите вручную остаток {remaining} {"л" if kind=="fuel" else "ч"}.'
                    )
            else:
                if manual and any(Decimal(r[input_field]) for r in manual.values()):
                    raise ValidationError(
                        "Ручной ресурсный объём задан при нулевом остатке."
                    )
                quantities = allocate(remaining, weights, Decimal(".01"))
            for month, amount in zip(future, quantities):
                source = manual.get(month) or base_months.get(month)
                if source:
                    result[kind] += resource_rows(workspace, source, month, amount)
    return result


class WorkspaceService:
    @staticmethod
    @transaction.atomic
    def create(user, obj, name, start, end):
        require_roles(user, PLAN_ROLES)
        if user.company_id != obj.company_id:
            raise PermissionDenied("Объект другой компании.")
        workspace = PlanningWorkspace(
            company=user.company,
            construction_object=obj,
            name=name,
            start_date=start,
            end_date=end,
        )
        workspace.full_clean()
        workspace.save()
        version = WorkspaceService.new_version(workspace, user, "BASELINE")
        workspace.baseline_version = version
        workspace.save(update_fields=["baseline_version"])
        return workspace

    @staticmethod
    def new_version(workspace, user, kind, month=None, scenario="", previous=None):
        require_roles(user, PLAN_ROLES)
        if workspace.company_id != user.company_id:
            raise PermissionDenied("План другой компании.")
        ConstructionObject.objects.select_for_update().get(
            pk=workspace.construction_object_id
        )
        number = (
            GlobalPlanVersion.objects.filter(
                construction_object=workspace.construction_object
            ).aggregate(n=Max("version_number"))["n"]
            or 0
        ) + 1
        version = GlobalPlanVersion(
            company=workspace.company,
            workspace=workspace,
            construction_object=workspace.construction_object,
            start_date=workspace.start_date,
            end_date=workspace.end_date,
            version_kind=kind,
            planning_month=month,
            scenario=scenario,
            version_number=number,
            title=workspace.name,
            created_by=user,
            previous_version=previous,
        )
        version.full_clean(exclude=["snapshot"])
        version.save()
        return version

    @staticmethod
    @transaction.atomic
    def forecast(workspace, user, month, scenario, previous=None):
        require_roles(user, PLAN_ROLES)
        workspace = PlanningWorkspace.objects.select_for_update().get(pk=workspace.pk)
        if workspace.company_id != user.company_id:
            raise PermissionDenied("План другой компании.")
        if (
            not workspace.baseline_version
            or not workspace.baseline_version.is_immutable
        ):
            raise ValidationError("Сначала утвердите базовый план.")
        if month.day != 1 or month not in months_between(
            workspace.start_date, workspace.end_date
        ):
            raise ValidationError("Выберите месяц внутри периода.")
        if scenario not in ["BASELINE", "REMAINING"]:
            raise ValidationError("Неизвестный сценарий.")
        if previous and (
            previous.workspace_id != workspace.pk
            or not previous.is_immutable
            or previous.version_kind != "FORECAST"
        ):
            raise ValidationError(
                "Исходная версия должна быть утверждённым месячным уточнением этого плана."
            )
        version = WorkspaceService.new_version(
            workspace, user, "FORECAST", month, scenario, previous
        )
        source = previous or workspace.baseline_version
        specs = {s["id"]: s for s in workspace.baseline_version.snapshot["works"]}
        if previous:
            specs.update({s["id"]: s for s in previous.snapshot.get("works", [])})
        for spec in specs.values():
            prior = source.work_allocations.filter(
                work_id=spec["id"], month=month
            ).first()
            WorkMonthAllocation.objects.create(
                company=workspace.company,
                version=version,
                work_id=spec["id"],
                month=month,
                quantity=prior.quantity if prior else 0,
                load_profile=prior.load_profile if prior else None,
            )
        for row in source.resource_allocations.filter(month=month):
            fields = {
                f.name: getattr(row, f.name)
                for f in ResourceMonthAllocation._meta.fields
                if f.name
                not in ["id", "created_at", "updated_at", "version", "company"]
            }
            ResourceMonthAllocation.objects.create(
                company=workspace.company, version=version, **fields
            )
        return version

    @staticmethod
    @transaction.atomic
    def refresh(version, user):
        require_roles(user, PLAN_ROLES)
        version = GlobalPlanVersion.objects.select_for_update().get(pk=version.pk)
        if version.company_id != user.company_id:
            raise PermissionDenied("Версия другой компании.")
        if version.status not in ["DRAFT", "REJECTED"]:
            raise ValidationError("Версия уже отправлена или утверждена.")
        version.snapshot = build_workspace_snapshot(version)
        version.save(update_fields=["snapshot"])
        return version


def current_workspace_version(company, obj, day):
    """The latest approved period version covering a date is the current plan."""
    return (
        GlobalPlanVersion.objects.filter(
            company=company,
            construction_object=obj,
            workspace__isnull=False,
            status__in=["APPROVED", "COMPLETED"],
            start_date__lte=day,
            end_date__gte=day,
        )
        .order_by("-approved_at", "-pk")
        .first()
    )


def virtual_resource_plan(version, kind, index, model):
    rows = version.snapshot.get("resources", {}).get(kind, [])
    if index < 0 or index >= len(rows):
        raise ValidationError("Строка плана не существует.")
    data = rows[index]
    fields = {field.attname for field in model._meta.fields} - {
        "id",
        "company_id",
        "construction_object_id",
        "created_at",
        "updated_at",
    }
    values = {name: value for name, value in data.items() if name in fields}
    values["date"] = date.fromisoformat(data["date"])
    row = model(
        company=version.company,
        construction_object=version.construction_object,
        **values,
    )
    row.pk = f"ws:{version.pk}:{index}"
    return row
