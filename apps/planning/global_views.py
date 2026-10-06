from django import forms
from django.core.exceptions import ValidationError
from django.contrib import messages
from django.shortcuts import get_object_or_404, render, redirect
from django.views import View
from django.views.generic import ListView, DetailView
from core.mixins import CompanyScopedMixin
from apps.production.forms import CompanyFormMixin
from apps.projects.models import ConstructionObject
from .models import GlobalPlanVersion, PlanVersion
from .global_services import GlobalPlanService, comparison


class GlobalForm(CompanyFormMixin, forms.Form):
    construction_object = forms.ModelChoiceField(
        queryset=ConstructionObject.objects.all(), label="Строительный объект"
    )
    start_date = forms.DateField(
        label="Начало периода",
        widget=forms.DateInput(format="%Y-%m-%d", attrs={"type": "date"}),
    )
    end_date = forms.DateField(
        label="Конец периода",
        widget=forms.DateInput(format="%Y-%m-%d", attrs={"type": "date"}),
    )
    title = forms.CharField(label="Название", required=False, max_length=255)
    source_versions = forms.ModelMultipleChoiceField(
        queryset=PlanVersion.objects.filter(status__in=["APPROVED", "COMPLETED"]),
        required=False,
        label="Утверждённые версии работ",
        help_text="Пустой выбор включает последние утверждённые версии выбранного объекта в заданном периоде.",
    )

    def clean(self):
        values = super().clean()
        if (
            values.get("start_date")
            and values.get("end_date")
            and values["start_date"] > values["end_date"]
        ):
            self.add_error("end_date", "Конец периода раньше начала.")
        return values


class GlobalList(CompanyScopedMixin, ListView):
    model = GlobalPlanVersion
    template_name = "planning/global_list.html"
    paginate_by = 50


class GlobalCreate(View):
    def get(self, request):
        return render(
            request,
            "planning/global_form.html",
            {"form": GlobalForm(user=request.user)},
        )

    def post(self, request):
        form = GlobalForm(request.POST, user=request.user)
        if form.is_valid():
            try:
                data = form.cleaned_data
                version = GlobalPlanService.create(
                    request.user,
                    data["construction_object"],
                    data["start_date"],
                    data["end_date"],
                    data["title"],
                    list(data["source_versions"]),
                )
                return redirect("planning:global_detail", pk=version.pk)
            except ValidationError as exc:
                form.add_error(None, "; ".join(exc.messages))
        return render(request, "planning/global_form.html", {"form": form})


class GlobalDetail(CompanyScopedMixin, DetailView):
    model = GlobalPlanVersion
    template_name = "planning/global_detail.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx.update(comparison(self.object))
        if self.object.workspace_id:
            from .workspace_views import monthly_summary, resource_monthly_summary
            from .workspace_services import months_between

            ctx["period_summary"] = monthly_summary(self.object)
            ctx["resource_period_summary"] = resource_monthly_summary(self.object)
            ctx["period_months"] = months_between(
                self.object.start_date, self.object.end_date
            )
        return ctx


class GlobalAction(View):
    def post(self, request, pk, action):
        version = get_object_or_404(
            GlobalPlanVersion, pk=pk, company=request.user.company
        )
        try:
            if action == "revision":
                version = GlobalPlanService.revision(version, request.user)
            else:
                version = GlobalPlanService.transition(
                    version, request.user, action, request.POST.get("comment", "")
                )
        except ValidationError as exc:
            messages.error(request, "; ".join(exc.messages))
        if (
            action == "revision"
            and version.workspace_id
            and version.status in ["DRAFT", "REJECTED"]
        ):
            return redirect("planning:workspace_edit", pk=version.pk)
        return redirect("planning:global_detail", pk=version.pk)
