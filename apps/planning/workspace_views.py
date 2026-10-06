from datetime import date
from decimal import Decimal
from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.http import Http404
from django.shortcuts import get_object_or_404, render, redirect
from django.urls import reverse
from django.views import View
from django.views.generic import ListView
from core.mixins import CompanyScopedMixin
from apps.works.forms import ProjectWorkForm
from apps.works.models import ProjectWork
from apps.works.services import WorkItemGeneratorService
from .models import PlanningWorkspace, GlobalPlanVersion, WorkMonthAllocation, ResourceMonthAllocation
from .workspace_forms import (
    WorkspaceForm,
    ForecastForm,
    WorkAllocationSet,
    ResourceAllocationSet,
    AddWorkForm,
    AddPeriodResourceForm,
    RESOURCE_IDENTITIES,
)
from .workspace_services import WorkspaceService, months_between, month_start
from .global_services import comparison


def workspace_for(request, pk):
    return get_object_or_404(
        PlanningWorkspace.objects.select_related(
            "baseline_version", "construction_object"
        ),
        pk=pk,
        company=request.user.company,
    )


def version_for(request, pk):
    return get_object_or_404(
        GlobalPlanVersion.objects.select_related(
            "workspace__baseline_version", "workspace__construction_object"
        ),
        pk=pk,
        company=request.user.company,
        workspace__isnull=False,
    )


def monthly_summary(version):
    if not version.snapshot:
        return []
    months = months_between(version.start_date, version.end_date)
    result = []
    for spec in version.snapshot.get("works", []):
        values = {
            m.isoformat(): sum(
                Decimal(r["quantity"])
                for r in spec["daily"]
                if r["date"][:7] == m.isoformat()[:7]
            )
            for m in months
        }
        baseline = (
            version.workspace.baseline_version.snapshot
            if version.workspace.baseline_version
            else {}
        )
        base_spec = next(
            (s for s in baseline.get("works", []) if s["id"] == spec["id"]), None
        )
        base_total = (
            sum(Decimal(r["quantity"]) for r in base_spec["daily"])
            if base_spec
            else (Decimal("0") if version.version_kind == "FORECAST" else None)
        )
        total = sum(values.values())
        result.append(
            {
                "name": spec["name"],
                "unit": spec["unit"],
                "cells": [
                    {"month": m, "quantity": values[m.isoformat()]} for m in months
                ],
                "total": total,
                "base_total": base_total,
                "deviation": total - base_total if base_total is not None else None,
            }
        )
    return result


def resource_monthly_summary(version):
    from .workspace_services import RESOURCE_CONFIG, resource_identity

    rows = []
    months = months_between(version.start_date, version.end_date)
    baseline = (
        version.workspace.baseline_version.snapshot
        if version.workspace.baseline_version
        else {}
    )
    for kind, records in version.snapshot.get("resources", {}).items():
        config = RESOURCE_CONFIG[kind]
        identities = {resource_identity(r, kind) for r in records}
        for identity in sorted(identities, key=str):
            selected = [r for r in records if resource_identity(r, kind) == identity]
            cells = []
            for month in months:
                monthly = [
                    r for r in selected if r["date"][:7] == month.isoformat()[:7]
                ]
                cells.append(
                    {
                        "quantity": sum(Decimal(r[config[0]]) for r in monthly),
                        "count_days": (
                            sum(r.get(config[4], 0) for r in monthly)
                            if config[4]
                            else None
                        ),
                    }
                )
            total = sum(c["quantity"] for c in cells)
            base_total = sum(
                Decimal(r[config[0]])
                for r in baseline.get("resources", {}).get(kind, [])
                if resource_identity(r, kind) == identity
            )
            rows.append(
                {
                    "label": (
                        selected[0]["label"]
                        + (" " + str(identity[1] or "") if kind != "labor" else "")
                    ).strip(),
                    "kind": kind,
                    "identity": identity,
                    "cells": cells,
                    "unit": (
                        "л"
                        if kind == "fuel"
                        else "чел·ч" if kind == "labor" else "маш·ч"
                    ),
                    "count_unit": "чел·дни" if kind == "labor" else "маш·дни",
                    "total": total,
                    "base_total": base_total,
                    "deviation": total - base_total,
                }
            )
    return rows


class WorkspaceList(CompanyScopedMixin, ListView):
    model = PlanningWorkspace
    template_name = "planning/workspace_list.html"
    paginate_by = 50


class WorkspaceCreate(View):
    def get(self, request):
        return render(
            request,
            "planning/workspace_create.html",
            {"form": WorkspaceForm(user=request.user)},
        )

    def post(self, request):
        form = WorkspaceForm(request.POST, user=request.user)
        if form.is_valid():
            data = form.cleaned_data
            try:
                workspace = WorkspaceService.create(
                    request.user,
                    data["construction_object"],
                    data["name"],
                    data["start_date"],
                    data["end_date"],
                )
                return redirect(
                    "planning:workspace_edit", pk=workspace.baseline_version_id
                )
            except ValidationError as exc:
                form.add_error(None, "; ".join(exc.messages))
        return render(request, "planning/workspace_create.html", {"form": form})


class WorkspaceDetail(View):
    def get(self, request, pk):
        workspace = workspace_for(request, pk)
        return render(
            request,
            "planning/workspace_detail.html",
            {
                "workspace": workspace,
                "versions": workspace.versions.order_by("-version_number"),
                "forecast_form": ForecastForm(workspace=workspace),
                "months": months_between(workspace.start_date, workspace.end_date),
            },
        )


class ForecastCreate(View):
    def post(self, request, pk):
        workspace = workspace_for(request, pk)
        form = ForecastForm(request.POST, workspace=workspace)
        if form.is_valid():
            try:
                version = WorkspaceService.forecast(
                    workspace,
                    request.user,
                    date.fromisoformat(form.cleaned_data["month"]),
                    form.cleaned_data["scenario"],
                )
                return redirect("planning:workspace_edit", pk=version.pk)
            except ValidationError as exc:
                form.add_error(None, "; ".join(exc.messages))
        return render(
            request,
            "planning/workspace_detail.html",
            {
                "workspace": workspace,
                "versions": workspace.versions.order_by("-version_number"),
                "forecast_form": form,
                "months": months_between(workspace.start_date, workspace.end_date),
            },
        )


class WorkspaceEdit(View):
    def setup_version(self, request, pk):
        version = version_for(request, pk)
        if version.status not in ["DRAFT", "REJECTED"]:
            raise PermissionDenied(
                "Отправленную или утверждённую версию редактировать нельзя."
            )
        months = months_between(version.start_date, version.end_date)
        selected = (
            request.GET.get("month")
            or (version.planning_month or months[0]).isoformat()
        )
        try:
            selected = date.fromisoformat(selected)
        except ValueError:
            raise Http404("Неизвестный месяц")
        if (
            selected not in months
            or version.planning_month
            and (
                selected < version.planning_month
                or version.scenario == "BASELINE"
                and selected != version.planning_month
            )
        ):
            raise Http404("Этот месяц формируется автоматически")
        return version, selected, months

    def forms(self, request, version, selected, bound=False):
        kwargs = {
            "data": request.POST if bound else None,
            "form_kwargs": {
                "user": request.user,
                "version": version,
                "month": selected,
            },
        }
        works = WorkAllocationSet(
            prefix="works",
            queryset=version.work_allocations.filter(month=selected),
            **kwargs,
        )
        resources = ResourceAllocationSet(
            prefix="resources",
            queryset=version.resource_allocations.filter(month=selected),
            **kwargs,
        )
        return works, resources

    def context(self, request, version, selected, months, works, resources):
        from .selection_filters import planning_filters
        filter_data, filter_specs = planning_filters(version)
        return {
            "version": version,
            "workspace": version.workspace,
            "selected_month": selected,
            "months": months,
            "work_forms": works,
            "planning_filter_data": filter_data,
            "work_filters": filter_specs['works'],
            "composite_items": {
                str(work.pk): [
                    {"name": item.name, "unit": item.unit, "norm": str(item.quantity_per_unit)}
                    for item in work.items.all() if item.company_id == version.company_id
                ]
                for work in ProjectWork.objects.filter(
                    company=version.company, section__construction_object=version.construction_object,
                    kind='COMPOSITE',
                ).prefetch_related('items')
            },
            "resource_forms": resources,
            "resource_sections": [
                {
                    "kind": kind, "title": title, "filters": filter_specs[kind],
                    "forms": [form for form in resources if form['kind'].value() == kind],
                    "period_form": AddPeriodResourceForm(
                        kind=kind, user=request.user, version=version, month=months[0], prefix='period-'+kind,
                    ),
                }
                for kind, title in ResourceMonthAllocation.Kind.choices
            ],
            "add_work_form": AddWorkForm(
                user=request.user, workspace=version.workspace
            ),
            "summary": monthly_summary(version),
            "future_manual": bool(
                version.planning_month and selected > version.planning_month
            ),
        }

    def get(self, request, pk):
        version, selected, months = self.setup_version(request, pk)
        works, resources = self.forms(request, version, selected)
        return render(
            request,
            "planning/workspace_edit.html",
            self.context(request, version, selected, months, works, resources),
        )

    def post(self, request, pk):
        version, selected, months = self.setup_version(request, pk)
        works, resources = self.forms(request, version, selected, True)
        if works.is_valid() and resources.is_valid():
            try:
                with transaction.atomic():
                    locked = GlobalPlanVersion.objects.select_for_update().get(
                        pk=version.pk
                    )
                    if locked.status not in ["DRAFT", "REJECTED"]:
                        raise ValidationError("Версия уже отправлена на согласование.")
                    works.save()
                    resources.save()
                    # Clear a stale preview atomically with changed monthly inputs.
                    locked.snapshot = {}
                    locked.save(update_fields=["snapshot"])
                messages.success(request, "Месячные данные сохранены.")
                if request.POST.get("action") == "preview":
                    try:
                        WorkspaceService.refresh(version, request.user)
                        return redirect("planning:global_detail", pk=version.pk)
                    except ValidationError as exc:
                        messages.error(request, "; ".join(exc.messages))
                return redirect(
                    reverse("planning:workspace_edit", args=[version.pk])
                    + "?month="
                    + selected.isoformat()
                )
            except ValidationError as exc:
                messages.error(request, "; ".join(exc.messages))
        return render(
            request,
            "planning/workspace_edit.html",
            self.context(request, version, selected, months, works, resources),
        )


class AddWorkspaceWork(View):
    def post(self, request, pk):
        version = version_for(request, pk)
        if version.version_kind != "BASELINE" or version.status not in [
            "DRAFT",
            "REJECTED",
        ]:
            raise PermissionDenied("Работы добавляются в черновик базы.")
        form = AddWorkForm(request.POST, user=request.user, workspace=version.workspace)
        if form.is_valid():
            with transaction.atomic():
                locked = GlobalPlanVersion.objects.select_for_update().get(
                    pk=version.pk
                )
                if locked.status not in ["DRAFT", "REJECTED"]:
                    raise PermissionDenied("Версия уже отправлена.")
                for month in months_between(version.start_date, version.end_date):
                    WorkMonthAllocation.objects.get_or_create(
                        company=version.company,
                        version=version,
                        work=form.cleaned_data["work"],
                        month=month,
                    )
                locked.snapshot = {}
                locked.save(update_fields=["snapshot"])
            messages.success(
                request,
                "Работа добавлена во все месяцы. Введите объёмы на вкладках месяцев.",
            )
        else:
            messages.error(request, "Выберите работу этого строительного объекта.")
        return redirect("planning:workspace_edit", pk=version.pk)


class WorkspaceWorkCreate(View):
    def form(self, request, workspace, bound=False):
        form = ProjectWorkForm(
            request.POST if bound else None,
            instance=ProjectWork(company=request.user.company),
            user=request.user, fixed_object=workspace.construction_object,
        )
        form.fields["section"].queryset = form.fields["section"].queryset.filter(
            company=request.user.company,
            construction_object=workspace.construction_object,
        )
        for name in ["template", "load_profile"]:
            form.fields[name].queryset = form.fields[name].queryset.filter(
                company=request.user.company
            )
        return form

    def get(self, request, pk):
        workspace = workspace_for(request, pk)
        if workspace.baseline_version.status not in ["DRAFT", "REJECTED"]:
            raise PermissionDenied("Базовый план уже зафиксирован.")
        return render(
            request,
            "planning/workspace_work_create.html",
            {"workspace": workspace, "form": self.form(request, workspace)},
        )

    def post(self, request, pk):
        workspace = workspace_for(request, pk)
        if workspace.baseline_version.status not in ["DRAFT", "REJECTED"]:
            raise PermissionDenied("Базовый план уже зафиксирован.")
        form = self.form(request, workspace, True)
        if form.is_valid():
            try:
                with transaction.atomic():
                    version = GlobalPlanVersion.objects.select_for_update().get(
                        pk=workspace.baseline_version_id
                    )
                    if version.status not in ["DRAFT", "REJECTED"]:
                        raise PermissionDenied("Базовый план уже отправлен.")
                    work = form.save()
                    if work.template_id:
                        WorkItemGeneratorService.generate_from_template(work)
                    for month in months_between(
                        workspace.start_date, workspace.end_date
                    ):
                        WorkMonthAllocation.objects.create(
                            company=workspace.company,
                            version=version,
                            work=work,
                            month=month,
                        )
                    version.snapshot = {}
                    version.save(update_fields=["snapshot"])
                if work.kind == "COMPOSITE" and not work.items.exists():
                    messages.info(
                        request,
                        "Добавьте подработы и нормативы, затем вернитесь в планирование.",
                    )
                    return redirect("works:work_detail", pk=work.pk)
                return redirect(
                    "planning:workspace_edit", pk=workspace.baseline_version_id
                )
            except ValidationError as exc:
                form.add_error(None, "; ".join(exc.messages))
        return render(
            request,
            "planning/workspace_work_create.html",
            {"workspace": workspace, "form": form},
        )


class WorkspaceExport(View):
    def get(self, request, pk):
        import csv
        import io
        from django.http import HttpResponse

        version = version_for(request, pk)
        if not version.snapshot:
            messages.error(request, "Сначала сформируйте план на весь период.")
            return redirect("planning:global_detail", pk=pk)
        stream = io.StringIO()
        writer = csv.writer(stream, delimiter=";")
        writer.writerow(
            [
                "Версия",
                version.version_number,
                "Статус",
                version.get_status_display(),
                "Период",
                str(version.start_date),
                str(version.end_date),
            ]
        )
        writer.writerow(
            [
                "Тип",
                "Работа / ресурс",
                "Единица",
                "Месяц",
                "Объём",
                "Численность в человеко-днях / машино-днях",
            ]
        )

        def label(text):
            text = str(text)
            return (
                "'" + text
                if text.startswith(("=", "+", "-", "@", "\t", "\r"))
                else text
            )

        def number(value):
            return str(value).replace(".", ",")

        for row in monthly_summary(version):
            for cell in row["cells"]:
                writer.writerow(
                    [
                        "Работа",
                        label(row["name"]),
                        label(row["unit"]),
                        cell["month"].strftime("%m.%Y"),
                        number(cell["quantity"]),
                        "",
                    ]
                )
        months = months_between(version.start_date, version.end_date)
        for row in resource_monthly_summary(version):
            for month, cell in zip(months, row["cells"]):
                writer.writerow(
                    [
                        {"labor": "Люди", "equipment": "Техника", "fuel": "ГСМ"}[
                            row["kind"]
                        ],
                        label(row["label"]),
                        row["unit"],
                        month.strftime("%m.%Y"),
                        number(cell["quantity"]),
                        (
                            number(cell["count_days"])
                            if cell["count_days"] is not None
                            else ""
                        ),
                    ]
                )
        response = HttpResponse(
            "\ufeff" + stream.getvalue(), content_type="text/csv; charset=utf-8"
        )
        response["Content-Disposition"] = (
            f'attachment; filename="period-plan-v{version.version_number}.csv"'
        )
        return response


class AddWorkspaceResource(View):
    def post(self, request, pk, kind):
        version = version_for(request, pk)
        if kind not in RESOURCE_IDENTITIES:
            raise Http404('Неизвестный вид ресурса')
        if version.version_kind != 'BASELINE' or version.status not in ['DRAFT', 'REJECTED']:
            raise PermissionDenied('Ресурсы на весь период добавляются в черновик базы.')
        months = months_between(version.start_date, version.end_date)
        form = AddPeriodResourceForm(request.POST, kind=kind, user=request.user,
            version=version, month=months[0], prefix='period-'+kind)
        if form.is_valid():
            with transaction.atomic():
                locked = GlobalPlanVersion.objects.select_for_update().get(pk=version.pk)
                if locked.status not in ['DRAFT', 'REJECTED']:
                    raise PermissionDenied('Версия уже отправлена на согласование.')
                identity = {name: form.cleaned_data[name] for name in RESOURCE_IDENTITIES[kind]}
                if kind == 'fuel':
                    identity['equipment_ref'] = ''
                for month in months:
                    ResourceMonthAllocation.objects.get_or_create(
                        company=version.company, version=locked, month=month, kind=kind, **identity)
                locked.snapshot = {}
                locked.save(update_fields=['snapshot'])
            messages.success(request, 'Ресурс добавлен во все месяцы. Введите количества и объёмы на вкладках месяцев.')
            return redirect('planning:workspace_edit', pk=version.pk)
        editor = WorkspaceEdit()
        works, resources = editor.forms(request, version, months[0])
        context = editor.context(request, version, months[0], months, works, resources)
        for section in context['resource_sections']:
            if section['kind'] == kind:
                section['period_form'] = form
        return render(request, 'planning/workspace_edit.html', context, status=400)
