from datetime import date, timedelta
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
from core.permissions import role_code
from apps.works.forms import ProjectWorkForm
from apps.works.models import ProjectWork
from apps.works.services import WorkItemGeneratorService
from .approval_workflow import baseline_snapshot
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
            "baseline_version", "construction_object", "continued_from_review"
        ).defer("baseline_version__snapshot", "continued_from_review__snapshot"),
        pk=pk,
        company=request.user.company,
    )


def version_for(request, pk):
    return get_object_or_404(
        GlobalPlanVersion.objects.select_related(
            "workspace__baseline_version", "workspace__construction_object", "workspace__continued_from_review"
        ).defer("snapshot", "workspace__baseline_version__snapshot", "workspace__continued_from_review__snapshot"),
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
            baseline_snapshot(version)
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
        baseline_snapshot(version)
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

    def get_queryset(self):
        query=super().get_queryset().select_related('construction_object','baseline_version','baseline_version__preparation').defer('baseline_version__snapshot')
        term=self.request.GET.get('q','').strip()[:200]
        if term:
            from django.db.models import Q
            query=query.filter(Q(name__icontains=term)|Q(construction_object__name__icontains=term))
        from django.db.models import Prefetch
        return query.prefetch_related(Prefetch('versions',queryset=GlobalPlanVersion.objects.defer('snapshot').select_related('preparation').order_by('-version_number'),to_attr='ui_versions')).order_by('-updated_at','-pk')

    def get_context_data(self,**kwargs):
        ctx=super().get_context_data(**kwargs)
        from .gpr_ui import list_stage
        for workspace in ctx['object_list']:
            workspace.ui_version=workspace.ui_versions[0] if workspace.ui_versions else workspace.baseline_version
            workspace.ui_stage=list_stage(workspace.ui_version)
        ctx['search_query']=self.request.GET.get('q','').strip()[:200]
        return ctx


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
        from .project_plan_views import AssignmentForm
        return render(
            request,
            "planning/workspace_detail.html",
            {
                "workspace": workspace,
                "project_assignment_form": AssignmentForm(user=request.user, workspace=workspace),
                "assignment_workspace": workspace,
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
        if version.workspace.is_archived:raise PermissionDenied("ГПР находится в архиве.")
        if version.status not in ["DRAFT", "REJECTED"]:
            raise PermissionDenied(
                "Отправленную или утверждённую версию редактировать нельзя."
            )
        months = months_between(version.workspace.continuation_start or version.start_date, version.end_date)
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
        from core.permissions import role_code
        if role_code(request.user)=='MANAGER':
            for form in works:
                for field in form.fields.values():field.disabled=True
            from .preparation import require_resource_input
            try:require_resource_input(request.user,version)
            except (PermissionDenied,ValidationError):
                for form in resources:
                    for field in form.fields.values():field.disabled=True
        if role_code(request.user)=='PLANNER' and version.workspace.parent_gpr_id:
            for form in resources:
                for field in form.fields.values():field.disabled=True
        return works, resources

    def context(self, request, version, selected, months, works, resources):
        from .selection_filters import planning_filters
        from apps.works.prices import rate_on
        from apps.works.models import WorkPrice
        from django.db.models import Prefetch
        priced_works = ProjectWork.objects.filter(
            company=version.company,
            section__construction_object=version.construction_object,
        ).prefetch_related(Prefetch("price_history", queryset=WorkPrice.objects.order_by("effective_from", "pk")))
        work_prices = {
            str(work.pk): str(rate_on(work.price_history.all(), selected, work.unit_price))
            for work in priced_works
        }
        filter_data, filter_specs = planning_filters(version)
        from .work_selection_views import BranchForm
        from .resource_editor import context as resource_context
        resource_data=resource_context(request.user,version,selected)
        from .approval_workflow import panel
        from .gpr_ui import path_context
        approval=panel(version,request.user)
        allowed_months=[m for m in months if not version.planning_month or m>=version.planning_month and (version.scenario in {'REMAINING','ENTERED'} or m==version.planning_month)]
        from .plan_workbench import month_matrix
        from .daily_schedule_table_forms import AddEntryForm
        visible=month_matrix(version,selected,works,resources)
        can_work=role_code(request.user) in {'ADMIN','PLANNER'}
        if not can_work:
            for group in visible['sections'][0]['groups']:
                for entry in group['entries']:entry.can_edit=False;entry.profile=False
        return {
            'plan_sections':visible['sections'],'plan_is_daily':False,'plan_kind_label':'ГПР','plan_month':selected,'days':[],
            'plan_years':sorted({m.year for m in allowed_months}),'plan_month_options':[{'month':m,'url':reverse('planning:workspace_edit',args=[version.pk])+'?month='+m.isoformat()} for m in allowed_months],
            'plan_can_add_work':can_work,'plan_can_add_resources':resource_data['can_prepare_resources'],'table_editable':can_work or resource_data['can_prepare_resources'],
            'plan_add_form':AddEntryForm(schedule=version) if resource_data['can_prepare_resources'] else None,
            'plan_add_url':reverse('planning:workspace_edit',args=[version.pk])+'?month='+selected.isoformat(),
            **resource_data, **approval, **path_context(version,{**resource_data,**approval},request.user),
            'object':version,'gpr_single_page':True,'editor_has_errors':request.method=='POST',
            'editor_months':allowed_months,'editor_years':sorted({m.year for m in allowed_months}),
            "branch_form": BranchForm(version=version,initial={"months":[selected.isoformat()]}),
            "version": version,
            "workspace": version.workspace,
            "selected_month": selected,
            "remaining_work_issue": request.session.get(f'workspace_remainder_issue_{version.pk}'),
            "work_prices": work_prices,
            "months": months,
            "work_forms": works,
            "work_carryover_notes": {str(r.work_id): {'is_forecast':r.is_forecast,'as_of':str(r.forecast_as_of or ''),'quantity':str(r.quantity),'unit':r.work.unit,'items':r.item_quantities,'added':r.added,'previous_month':(selected.replace(day=1)-timedelta(days=1)).strftime('%m.%Y')} for r in version.work_carryovers.filter(month=selected).select_related('work')},
            "work_item_quantities": {form.prefix: form.instance.item_quantities for form in works if form.instance.item_quantities},
            "planning_filter_data": filter_data,
            "work_filters": filter_specs['works'],
            "composite_fractional": dict(ProjectWork.objects.filter(company=version.company, section__construction_object=version.construction_object, kind="COMPOSITE").values_list("pk", "allow_fractional")),
            "composite_items": {
                str(work.pk): [
                    {"id": item.pk, "name": item.name, "unit": item.unit, "norm": str(item.quantity_per_unit)}
                    for item in work.items.all() if item.company_id == version.company_id
                ]
                for work in ProjectWork.objects.filter(
                    company=version.company, section__construction_object=version.construction_object,
                    kind='COMPOSITE',
                ).prefetch_related('items')
            },
            "resource_forms": resources,
            "resources_read_only": role_code(request.user)=="PLANNER" and bool(version.workspace.parent_gpr_id),
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
        from core.permissions import scope_queryset
        version=get_object_or_404(scope_queryset(GlobalPlanVersion.objects.filter(workspace__isnull=False),request.user),pk=pk)
        if version.status not in {'DRAFT','REJECTED'} or role_code(request.user) not in {'ADMIN','PLANNER','MANAGER'}:
            from .global_views import GlobalDetail
            request.gpr_single_page=True
            return GlobalDetail.as_view()(request,pk=pk)
        version, selected, months = self.setup_version(request, pk)
        works, resources = self.forms(request, version, selected)
        return render(
            request,
            "planning/workspace_edit.html",
            self.context(request, version, selected, months, works, resources),
        )

    def post(self, request, pk):
        from core.permissions import require_roles
        require_roles(request.user,{'ADMIN','PLANNER','MANAGER'})
        version, selected, months = self.setup_version(request, pk)
        if role_code(request.user)=='MANAGER':
            from .preparation import require_resource_input
            require_resource_input(request.user,version)
            if request.POST.get('action') not in {None,'save','add_table_resource'}:raise PermissionDenied('Руководитель редактирует и подтверждает ресурсы. Работы составляет планировщик.')
        from core.permissions import require_planning_object
        require_planning_object(request.user,version.construction_object_id)
        if request.POST.get('action')=='add_table_resource':
            from .daily_schedule_table_forms import AddEntryForm
            from .plan_workbench import add_month_resource
            form=AddEntryForm(request.POST,schedule=version)
            if form.is_valid():
                try:
                    created=add_month_resource(request.user,version,selected,form.cleaned_data)
                    messages.success(request,'Ресурс добавлен. Заполните месячные значения и сохраните план.' if created else 'Этот ресурс уже есть в месяце. Измените его значения в таблице.')
                    return redirect(reverse('planning:workspace_edit',args=[version.pk])+'?month='+selected.isoformat())
                except ValidationError as exc:form.add_error(None,exc)
            works,resources=self.forms(request,version,selected)
            ctx=self.context(request,version,selected,months,works,resources);ctx['plan_add_form']=form
            return render(request,'planning/workspace_edit.html',ctx,status=400)
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
                    crew_edits={f.instance.pk:f.cleaned_data['count'] for f in resources if f.instance.pk and not f.cleaned_data.get('DELETE') and f.cleaned_data.get('kind')=='labor' and 'count' in f.changed_data}
                    crew_removed=set(locked.resource_allocations.filter(pk__in=[f.instance.pk for f in resources if f.cleaned_data.get('DELETE')],kind='labor').values_list('brigade_id',flat=True))
                    resources.save()
                    from .table_resource_crew import sync_monthly
                    sync_monthly(request.user,locked,selected,edited_counts=crew_edits,removed_brigades=crew_removed,removal_actions=request.POST.get('crew_removal_actions'))
                    removed = None
                    if request.POST.get('action') == 'remove_zero_works':
                        from .zero_works import remove_zero_works
                        removed = remove_zero_works(locked, selected)
                    # Clear a stale preview atomically with changed monthly inputs.
                    locked.snapshot = {}
                    locked.save(update_fields=["snapshot"])
                messages.success(request, f"Месячные данные сохранены. Удалено нулевых работ: {removed}." if removed is not None else "Месячные данные сохранены.")
                if request.POST.get("action") == "preview":
                    try:
                        WorkspaceService.refresh(version, request.user)
                        request.session.pop(f'workspace_remainder_issue_{version.pk}',None)
                        return redirect("planning:global_detail", pk=version.pk)
                    except ValidationError as exc:
                        if getattr(exc, 'code', None) == 'manual_work_remainder':
                            request.session[f'workspace_remainder_issue_{version.pk}'] = exc.params
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
                for month in months_between(version.workspace.continuation_start or version.start_date, version.end_date):
                    row,created = WorkMonthAllocation.objects.get_or_create(
                        company=version.company,
                        version=version,
                        work=form.cleaned_data["work"],
                        month=month,
                    )
                    if not created:
                        from .work_selection import extend_allocation
                        extend_allocation(row,[])
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
                        "Добавьте работы в составе комплекса и нормативы, затем вернитесь в планирование.",
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
                        {"labor": "Должность", "equipment": "Техника", "fuel": "ГСМ"}[
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
        from .preparation import require_resource_input
        require_resource_input(request.user,version)
        if kind not in RESOURCE_IDENTITIES:
            raise Http404('Неизвестный вид ресурса')
        if version.version_kind != 'BASELINE' or version.status not in ['DRAFT', 'REJECTED']:
            raise PermissionDenied('Ресурсы на весь период добавляются в черновик базы.')
        months = months_between(version.workspace.continuation_start or version.start_date, version.end_date)
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


class AddRemainingWork(View):
    def post(self,request,pk):
        from core.permissions import require_roles, PLAN_ROLES
        from .meeting_import import validate_target_period
        from calendar import monthrange
        require_roles(request.user,PLAN_ROLES)
        version=version_for(request,pk)
        issue=request.session.get(f'workspace_remainder_issue_{version.pk}')
        if not issue:
            raise Http404('Сначала сформируйте план, чтобы определить остаток.')
        raw=request.POST.get('month','')
        if raw not in issue['months']:
            raise Http404('Выберите месяц распределения.')
        month=date.fromisoformat(raw)
        with transaction.atomic():
            version=GlobalPlanVersion.objects.select_for_update().defer('snapshot').get(pk=version.pk,company=request.user.company)
            validate_target_period(version,month,month.replace(day=monthrange(month.year,month.month)[1]))
            work=get_object_or_404(ProjectWork,pk=issue['work_id'],company=request.user.company,section__construction_object_id=version.construction_object_id)
            row,created=WorkMonthAllocation.objects.get_or_create(company=request.user.company,version=version,month=month,work=work,defaults={'quantity':0})
            version.snapshot={};version.save(update_fields=['snapshot'])
        messages.info(request,f'Работа «{work.name}» '+('добавлена. Укажите объем для этого месяца и сохраните.' if created else 'уже есть в этом месяце. Проверьте и сохраните объем.'))
        return redirect(reverse('planning:workspace_edit',args=[version.pk])+'?month='+raw+'&focus_work='+str(work.pk))
