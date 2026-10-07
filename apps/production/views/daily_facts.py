"""
Views для дневных фактов (объёмов работ).
"""

from django.contrib import messages
from core.permissions import scope_queryset
from django.views.generic import ListView, CreateView, UpdateView, DeleteView
from django.urls import reverse_lazy
from django.utils.http import urlencode
from django.core.paginator import Paginator
from ..fact_matrix import FactMatrixFilterForm, default_period, fact_rows, period_days

from core.mixins import CompanyRequiredMixin, CompanyScopedMixin
from apps.production.models import DailyFact
from apps.production.forms import DailyFactForm
from .resources import UserFormMixin


def fact_journal_url(fact):
    start, end = default_period(fact.date)
    return (
        str(reverse_lazy("production:fact_list"))
        + "?"
        + urlencode(
            {
                "start": start.isoformat(),
                "end": end.isoformat(),
                "construction_object": fact.project_work.section.construction_object_id,
            }
        )
    )


class FactListView(CompanyScopedMixin, ListView):
    model = DailyFact
    template_name = "production/fact_list.html"
    context_object_name = "facts"
    paginate_by = None

    def get_queryset(self):
        data = self.request.GET.copy()
        start, end = default_period()
        data.setdefault("start", start.isoformat())
        data.setdefault("end", end.isoformat())
        self.filter_form = FactMatrixFilterForm(data, user=self.request.user)
        queryset = scope_queryset(DailyFact.objects.all(), self.request.user)
        if not self.filter_form.is_valid():
            return queryset.none()
        values = self.filter_form.cleaned_data
        queryset = queryset.filter(date__range=(values["start"], values["end"]))
        if values["construction_object"]:
            queryset = queryset.filter(
                project_work__section__construction_object=values["construction_object"]
            )
        if values["work"]:
            queryset = queryset.filter(project_work=values["work"])
        return queryset.select_related(
            "project_work__section__construction_object__project",
            "work_item",
            "reported_by",
            "deviation_reason",
        ).order_by(
            "project_work__section__construction_object__name",
            "project_work__name",
            "project_work_id",
            "work_item__sequence",
            "work_item_id",
            "date",
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        days = (
            period_days(
                self.filter_form.cleaned_data["start"],
                self.filter_form.cleaned_data["end"],
            )
            if self.filter_form.is_valid()
            else []
        )
        rows = fact_rows(context["facts"], days)
        paginator = Paginator(rows, 20)
        page = paginator.get_page(self.request.GET.get("page"))
        params = self.filter_form.data.copy()
        params.pop("page", None)
        context.update(
            filter_form=self.filter_form,
            matrix_days=days,
            matrix_rows=page.object_list,
            page_obj=page,
            is_paginated=page.has_other_pages(),
            matrix_query=self.filter_form.data.urlencode(),
            filter_query=params.urlencode(),
        )
        return context


class FactCreateView(UserFormMixin, CompanyRequiredMixin, CreateView):
    model = DailyFact
    form_class = DailyFactForm
    template_name = "production/fact_form.html"
    success_url = reverse_lazy("production:fact_list")

    def get_success_url(self):
        return fact_journal_url(self.object)

    def form_valid(self, form):
        form.instance.reported_by = self.request.user
        messages.success(self.request, "Факт по объёму успешно введён!")
        return super().form_valid(form)


class FactUpdateView(UserFormMixin, CompanyScopedMixin, UpdateView):
    model = DailyFact
    form_class = DailyFactForm
    template_name = "production/fact_form.html"

    def get_success_url(self):
        return fact_journal_url(self.object)

    def form_valid(self, form):
        messages.success(self.request, "Факт обновлён!")
        return super().form_valid(form)


class FactDeleteView(CompanyScopedMixin, DeleteView):
    model = DailyFact
    template_name = "production/fact_confirm_delete.html"
    success_url = reverse_lazy("production:fact_list")

    def get_success_url(self):
        query = self.request.POST.get(
            "return_query", self.request.GET.get("return_query", "")
        )[:4096]
        return str(self.success_url) + ("?" + query if query else "")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["return_query"] = self.request.GET.get("return_query", "")[:4096]
        context["cancel_url"] = self.get_success_url()
        return context

    def form_valid(self, form):
        day = self.object.date
        response = super().form_valid(form)
        messages.success(self.request, f"Факт за {day:%d.%m.%Y} удалён.")
        return response
