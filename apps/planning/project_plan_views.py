from django import forms
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db import transaction
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.views import View
from django.views.generic import ListView
from core.mixins import CompanyScopedMixin
from core.permissions import PLAN_ROLES, require_roles
from apps.projects.models import Project
from .models import ProjectPlanVersion, GlobalPlanVersion, PlanningWorkspace
from .project_plan_services import ProjectPlanService


class ProjectPlanForm(forms.ModelForm):
    class Meta:
        model = ProjectPlanVersion
        fields = ["project", "title", "start_date", "end_date"]
        widgets = {
            name: forms.DateInput(format="%Y-%m-%d", attrs={"type": "date"})
            for name in ["start_date", "end_date"]
        }

    def __init__(self, *args, user, **kwargs):
        super().__init__(*args, **kwargs)
        self.instance.company = user.company
        self.fields["project"].queryset = Project.objects.filter(company=user.company)
        if self.instance.pk:
            self.fields["project"].disabled = True
        for field in self.fields.values():
            field.widget.attrs["class"] = (
                "form-select"
                if isinstance(field, forms.ModelChoiceField)
                else "form-control"
            )


class MemberForm(forms.Form):
    version = forms.ModelChoiceField(
        queryset=GlobalPlanVersion.objects.none(),
        label="Согласованная версия плана объекта",
    )
    replace = forms.BooleanField(
        required=False, label="Заменить версию объекта, если он уже включён"
    )

    def __init__(self, *args, parent, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["version"].queryset = GlobalPlanVersion.objects.filter(
            company=parent.company,
            construction_object__project=parent.project,
            status__in=["APPROVED", "COMPLETED"],
            start_date__lte=parent.start_date,
            end_date__gte=parent.end_date,
        ).select_related("construction_object")
        self.fields["version"].widget.attrs["class"] = "form-select"


class AssignmentForm(forms.Form):
    consolidated_version = forms.ModelChoiceField(
        queryset=ProjectPlanVersion.objects.none(), label="Сводная версия проекта"
    )
    version = forms.ModelChoiceField(
        queryset=GlobalPlanVersion.objects.none(), label="Согласованный план объекта"
    )
    replace = forms.BooleanField(
        required=False, label="Заменить версию объекта в составе"
    )

    def __init__(self, *args, user, workspace=None, version=None, **kwargs):
        super().__init__(*args, **kwargs)
        if version:
            versions = GlobalPlanVersion.objects.filter(
                pk=version.pk,
                company=user.company,
                status__in=["APPROVED", "COMPLETED"],
            )
            obj = version.construction_object
            start, end = version.start_date, version.end_date
            self.fields["version"].initial = version.pk
            self.fields["version"].widget = forms.HiddenInput()
        else:
            versions = workspace.versions.filter(
                company=user.company, status__in=["APPROVED", "COMPLETED"]
            )
            obj = workspace.construction_object
            start, end = workspace.start_date, workspace.end_date
        self.fields["version"].queryset = versions
        self.fields["consolidated_version"].queryset = (
            ProjectPlanVersion.objects.filter(
                company=user.company,
                project=obj.project,
                status="DRAFT",
                start_date__gte=start,
                end_date__lte=end,
            ).select_related("project")
        )
        for name in ["version", "consolidated_version"]:
            self.fields[name].widget.attrs["class"] = "form-select"


def parent_for(request, pk):
    return get_object_or_404(
        ProjectPlanVersion.objects.select_related("project"),
        pk=pk,
        company=request.user.company,
    )


class ProjectPlanList(CompanyScopedMixin, ListView):
    model = ProjectPlanVersion
    template_name = "planning/project_plan_list.html"
    paginate_by = 50

    def get_queryset(self):
        return (
            super().get_queryset().select_related("project").prefetch_related("members")
        )


class ProjectPlanCreate(View):
    def get(self, request):
        return render(
            request,
            "planning/project_plan_form.html",
            {"form": ProjectPlanForm(user=request.user)},
        )

    def post(self, request):
        form = ProjectPlanForm(request.POST, user=request.user)
        if form.is_valid():
            try:
                row = ProjectPlanService.create(
                    request.user,
                    form.cleaned_data["project"],
                    form.cleaned_data["title"],
                    form.cleaned_data["start_date"],
                    form.cleaned_data["end_date"],
                )
                return redirect("planning:project_plan_detail", pk=row.pk)
            except ValidationError as exc:
                form.add_error(None, "; ".join(exc.messages))
        return render(request, "planning/project_plan_form.html", {"form": form})


class ProjectPlanUpdate(View):
    def get(self, request, pk):
        row = parent_for(request, pk)
        if row.status != "DRAFT":
            messages.error(request, "Зафиксированную сводную версию менять нельзя.")
            return redirect("planning:project_plan_detail", pk=pk)
        return render(
            request,
            "planning/project_plan_form.html",
            {"form": ProjectPlanForm(user=request.user, instance=row), "object": row},
        )

    def post(self, request, pk):
        require_roles(request.user, PLAN_ROLES)
        with transaction.atomic():
            row = get_object_or_404(
                ProjectPlanVersion.objects.select_for_update(),
                pk=pk,
                company=request.user.company,
            )
            if row.status != "DRAFT":
                messages.error(request, "Зафиксированную сводную версию менять нельзя.")
                return redirect("planning:project_plan_detail", pk=pk)
            form = ProjectPlanForm(request.POST, user=request.user, instance=row)
            if form.is_valid():
                try:
                    form.save()
                    return redirect("planning:project_plan_detail", pk=pk)
                except ValidationError as exc:
                    form.add_error(None, "; ".join(exc.messages))
        return render(
            request, "planning/project_plan_form.html", {"form": form, "object": row}
        )


class ProjectPlanDetail(View):
    def get(self, request, pk):
        row = parent_for(request, pk)
        members = list(
            row.members.select_related("version", "construction_object", "review")
        )
        for member in members:
            member.revoked = member.version.status not in ["APPROVED", "COMPLETED"]
            member.changed_review = bool(
                member.review_id
                and member.review.number != member.version.approval_round
            )
        return render(
            request,
            "planning/project_plan_detail.html",
            {"object": row, "members": members, "form": MemberForm(parent=row)},
        )

    def post(self, request, pk):
        require_roles(request.user, PLAN_ROLES)
        row = parent_for(request, pk)
        form = MemberForm(request.POST, parent=row)
        try:
            action = request.POST.get("action", "add")
            if action == "fix":
                ProjectPlanService.fix(request.user, row)
                messages.success(
                    request, "Состав и согласованные снимки планов зафиксированы."
                )
            elif action == "reopen":
                ProjectPlanService.reopen(request.user, row)
                messages.success(request, "Сводный план возвращён в черновик. Измените состав и зафиксируйте его снова.")
            elif action == "revision":
                result = ProjectPlanService.revision(request.user, row)
                missing = row.members.count() - result.members.count()
                if missing:
                    messages.warning(
                        request,
                        f"{missing} объектов не перенесено: исходные планы возвращены на доработку. Добавьте их после согласования.",
                    )
                return redirect("planning:project_plan_detail", pk=result.pk)
            elif action == "remove":
                try:
                    member_id = int(request.POST.get("member", ""))
                except (ValueError, TypeError):
                    raise Http404("Неизвестная строка состава.")
                member = get_object_or_404(row.members, pk=member_id)
                ProjectPlanService.remove(request.user, row, member.pk)
            elif action == "add" and form.is_valid():
                ProjectPlanService.assign(
                    request.user,
                    row,
                    form.cleaned_data["version"],
                    form.cleaned_data["replace"],
                )
                messages.success(request, "Версия плана объекта включена в состав.")
            else:
                messages.error(
                    request,
                    "; ".join(
                        str(error)
                        for errors in form.errors.values()
                        for error in errors
                    )
                    or "Неизвестное действие.",
                )
        except ValidationError as exc:
            messages.error(request, "; ".join(exc.messages))
        return redirect("planning:project_plan_detail", pk=pk)


class AssignProjectPlan(View):
    def post(self, request, pk, workspace_mode=False):
        require_roles(request.user, PLAN_ROLES)
        if workspace_mode:
            workspace = get_object_or_404(
                PlanningWorkspace, pk=pk, company=request.user.company
            )
            version = None
        else:
            version = get_object_or_404(
                GlobalPlanVersion, pk=pk, company=request.user.company
            )
            workspace = None
        form = AssignmentForm(
            request.POST, user=request.user, workspace=workspace, version=version
        )
        if form.is_valid():
            try:
                ProjectPlanService.assign(
                    request.user,
                    form.cleaned_data["consolidated_version"],
                    form.cleaned_data["version"],
                    form.cleaned_data["replace"],
                )
                messages.success(
                    request, "План объекта включён в сводную версию проекта."
                )
                return redirect(
                    "planning:project_plan_detail",
                    pk=form.cleaned_data["consolidated_version"].pk,
                )
            except ValidationError as exc:
                messages.error(request, "; ".join(exc.messages))
        else:
            messages.error(
                request,
                "; ".join(
                    str(error) for errors in form.errors.values() for error in errors
                ),
            )
        return redirect(
            "planning:workspace_detail" if workspace_mode else "planning:global_detail",
            pk=pk,
        )
