from django.views import View
from core.permissions import scope_queryset
from django.shortcuts import render, redirect
from django.utils import timezone
from apps.works.models import ProjectWork
from apps.production.forms import DailyFactForm, FactInputFilterForm
from apps.production.models import DailyFact, DeviationReason
from apps.works.progress import WorkProgressService


class FactInputView(View):
    def get(self, request):
        return self.display(request)

    def display(self, request, form=None):
        filters = FactInputFilterForm(request.GET or None, user=request.user)
        works = scope_queryset(ProjectWork.objects.all(), request.user)
        if filters.is_bound:
            if filters.is_valid():
                for field, lookup in [
                    ("project", "section__construction_object__project"),
                    ("construction_object", "section__construction_object"),
                    ("section", "section"),
                ]:
                    if filters.cleaned_data.get(field):
                        works = works.filter(**{lookup: filters.cleaned_data[field]})
            else:
                works = works.none()
        data = [
            {"work": work, "completed": WorkProgressService.completed(work)}
            for work in works
        ]
        return render(
            request,
            "production/work_input.html",
            {
                "filter_form": filters,
                "form": form
                or DailyFactForm(
                    user=request.user, initial={"date": timezone.localdate()}
                ),
                "works_data": data,
                "works_with_plan": data,
                "deviation_reasons": DeviationReason.objects.filter(
                    company=request.user.company
                ),
            },
        )

    def post(self, request):
        form = DailyFactForm(request.POST, user=request.user)
        if form.is_valid():
            fact = form.save(commit=False)
            fact.reported_by = request.user
            fact.save()
            return redirect("production:fact_input")
        return self.display(request, form)


def fact_daily_view(request):
    return FactInputView.as_view()(request)
