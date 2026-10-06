from decimal import Decimal

from django.contrib import messages
from django.views.generic import (
    ListView,
    DetailView,
    CreateView,
    UpdateView,
    DeleteView,
    View,
)
from django.urls import reverse_lazy
from django.shortcuts import get_object_or_404, redirect, render
from core.mixins import CompanyRequiredMixin, CompanyScopedMixin

from .models import MonthlyPlan, PlanVersion, DailyPlan, LoadProfile
from .forms import MonthlyPlanForm
from .services import PlanGeneratorService
from apps.production.models import DailyFact

# =============================================================================
# МЕСЯЧНЫЕ ПЛАНЫ
# =============================================================================


class PlanListView(CompanyScopedMixin, ListView):
    model = MonthlyPlan
    template_name = "planning/plan_list.html"
    context_object_name = "plans"
    paginate_by = 20

    def get_queryset(self):
        qs = (
            MonthlyPlan.objects.filter(company=self.request.user.company)
            .select_related(
                "project_work",
                "project_work__section",
                "project_work__section__construction_object",
                "project_work__section__construction_object__project",
            )
            .order_by("-year", "-month")
        )

        # Фильтр: Проект
        project_id = self.request.GET.get("project")
        if project_id:
            qs = qs.filter(
                project_work__section__construction_object__project_id=project_id
            )

        # Фильтр: Объект
        object_id = self.request.GET.get("object")
        if object_id:
            qs = qs.filter(project_work__section__construction_object_id=object_id)

        # Фильтр: Раздел
        section_id = self.request.GET.get("section")
        if section_id:
            qs = qs.filter(project_work__section_id=section_id)

        # Фильтр: Работа
        work_id = self.request.GET.get("work")
        if work_id:
            qs = qs.filter(project_work_id=work_id)

        # Фильтр: Подработа
        item_id = self.request.GET.get("item")
        if item_id:
            qs = qs.filter(project_work__items__id=item_id)

        # Фильтр: Период (пересечение)
        # План пересекается с диапазоном, если:
        # plan.start_date <= period_end AND plan.end_date >= period_start
        date_from = self.request.GET.get("date_from")
        date_to = self.request.GET.get("date_to")
        if date_from:
            qs = qs.filter(end_date__gte=date_from)
        if date_to:
            qs = qs.filter(start_date__lte=date_to)

        return qs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        company = self.request.user.company

        # Списки для фильтров
        from apps.projects.models import Project, ConstructionObject, Section
        from apps.works.models import ProjectWork, ProjectWorkItem

        projects = Project.objects.filter(company=company).order_by("name")
        objects = ConstructionObject.objects.filter(company=company).order_by("name")
        sections = Section.objects.filter(company=company).order_by("name")
        works = ProjectWork.objects.filter(company=company).order_by("name")
        items = ProjectWorkItem.objects.filter(company=company).order_by("name")

        context.update(
            {
                "projects": projects,
                "objects": objects,
                "sections": sections,
                "works": works,
                "items": items,
                # Сохраняем выбранные значения
                "selected_project": self.request.GET.get("project", ""),
                "selected_object": self.request.GET.get("object", ""),
                "selected_section": self.request.GET.get("section", ""),
                "selected_work": self.request.GET.get("work", ""),
                "selected_item": self.request.GET.get("item", ""),
                "date_from": self.request.GET.get("date_from", ""),
                "date_to": self.request.GET.get("date_to", ""),
                "filters_count": sum(
                    1
                    for v in [
                        self.request.GET.get("project"),
                        self.request.GET.get("object"),
                        self.request.GET.get("section"),
                        self.request.GET.get("work"),
                        self.request.GET.get("item"),
                        self.request.GET.get("date_from"),
                        self.request.GET.get("date_to"),
                    ]
                    if v
                ),
            }
        )
        return context


class PlanDetailView(CompanyScopedMixin, DetailView):
    model = MonthlyPlan
    template_name = "planning/plan_detail.html"
    context_object_name = "plan"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["versions"] = self.object.versions.filter(
            company=self.request.user.company
        ).order_by("-version_number")
        return context


class MonthlyPlanCreateView(CompanyRequiredMixin, CreateView):
    model = MonthlyPlan
    form_class = MonthlyPlanForm
    template_name = "planning/monthly_plan_form.html"
    success_url = reverse_lazy("planning:plan_list")

    def form_valid(self, form):
        # 1. Сохраняем план
        self.object = form.save(commit=False)
        self.object.company = self.get_company()

        work = self.object.project_work
        self.object.planned_value = self.object.planned_quantity * work.unit_price
        self.object.save()

        # 2. ВСЕГДА создаём версию DRAFT (даже если генерация упадёт)
        version = PlanVersion.objects.create(
            monthly_plan=self.object,
            company=self.object.company,
            version_number=1,
            status="DRAFT",
            created_by=self.request.user,
        )

        # 3. Пытаемся сгенерировать дневные планы
        try:
            count = PlanGeneratorService.generate(version)
            messages.success(
                self.request,
                f"План создан. Версия v1 (DRAFT). Сгенерировано {count} дневных записей.",
            )
        except Exception as e:
            messages.warning(
                self.request,
                f"План создан (версия v1 DRAFT), но генерация дневных планов не удалась: {e}. "
                f"Вы можете сгенерировать план вручную на странице версии.",
            )

        return redirect(self.get_success_url())


class MonthlyPlanUpdateView(CompanyScopedMixin, UpdateView):
    """Редактирование месячного плана."""

    model = MonthlyPlan
    form_class = MonthlyPlanForm
    template_name = "planning/monthly_plan_form.html"
    success_url = reverse_lazy("planning:plan_list")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["is_edit"] = True
        return context

    def form_valid(self, form):
        messages.success(self.request, "План успешно обновлён!")
        return super().form_valid(form)


class MonthlyPlanDeleteView(CompanyScopedMixin, DeleteView):
    """Удаление месячного плана."""

    model = MonthlyPlan
    template_name = "planning/plan_confirm_delete.html"
    success_url = reverse_lazy("planning:plan_list")

    def delete(self, request, *args, **kwargs):
        plan = self.get_object()
        messages.success(
            request,
            f'План "{plan.project_work.name}" за {plan.month}/{plan.year} удалён',
        )
        return super().delete(request, *args, **kwargs)


# =============================================================================
# ВЕРСИИ ПЛАНОВ И WORKFLOW
# =============================================================================


class PlanVersionDetailView(CompanyScopedMixin, DetailView):
    model = PlanVersion
    template_name = "planning/version_detail.html"
    context_object_name = "version"

    def get_context_data(self, **kwargs):
        from apps.works.progress import WorkProgressService
        from .global_services import source_versions
        from datetime import date

        context = super().get_context_data(**kwargs)
        v = self.object
        work = v.monthly_plan.project_work
        history = [
            p
            for p in source_versions(
                work.section.construction_object, date.min, v.monthly_plan.start_date
            )
            if p.monthly_plan.project_work_id == work.pk
            and p.monthly_plan.end_date < v.monthly_plan.start_date
        ]
        planned = WorkProgressService.planned(work, history + [v])
        facts = WorkProgressService.facts(work, until=v.monthly_plan.end_date)
        start, end = v.monthly_plan.start_date, v.monthly_plan.end_date
        days = sorted(day for day in set(planned) | set(facts) if start <= day <= end)
        context["work"] = work
        context["days"] = [
            {
                "date": day,
                "plan": planned.get(day, {}).get("daily", 0),
                "fact": facts.get(day, {}).get("daily", 0),
            }
            for day in days
        ]
        context["daily_plans"] = v.daily_plans.select_related("work_item")
        context["total_planned_quantity"] = sum(
            (row["plan"] for row in context["days"]), Decimal("0")
        )
        context["total_fact"] = sum(
            (row["fact"] for row in context["days"]), Decimal("0")
        )
        return context


class PlanVersionsListView(CompanyScopedMixin, ListView):
    """Список всех версий плана."""

    model = PlanVersion
    template_name = "planning/plan_versions_list.html"
    context_object_name = "versions"

    def dispatch(self, request, *args, **kwargs):
        self.plan = get_object_or_404(
            MonthlyPlan, company=self.request.user.company, pk=kwargs["plan_pk"]
        )
        return super().dispatch(request, *args, **kwargs)

    def get_queryset(self):
        return PlanVersion.objects.filter(
            company=self.request.user.company, monthly_plan=self.plan
        ).order_by("-version_number")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["plan"] = self.plan
        return context


from django.http import JsonResponse
from django.views.decorators.http import require_POST
from django.views.decorators.csrf import csrf_protect


@csrf_protect
@require_POST
def daily_plan_inline_update(request):
    """Inline обновление дневного плана через AJAX."""
    try:
        import json

        data = json.loads(request.body)
        plan_id = data.get("plan_id")
        field = data.get("field")
        value = data.get("value")

        plan = DailyPlan.objects.get(company=request.user.company, pk=plan_id)

        # Проверяем, что версия не immutable
        if plan.plan_version.is_immutable or plan.plan_version.status == "SUBMITTED":
            return JsonResponse(
                {
                    "success": False,
                    "message": "Версия утверждена и не может быть изменена",
                },
                status=403,
            )

        if field == "planned_quantity":
            from decimal import Decimal, InvalidOperation

            try:
                qty = Decimal(str(value)) if value else Decimal("0")
            except (InvalidOperation, ValueError):
                return JsonResponse(
                    {"success": False, "message": "Некорректное число"}, status=400
                )

            plan.planned_quantity = qty

            # Пересчитываем стоимость
            unit_price = plan.project_work.unit_price
            if plan.work_item_id:
                unit_price *= (
                    plan.work_item.weight
                    / Decimal("100")
                    / plan.work_item.quantity_per_unit
                )
            if unit_price is None:
                unit_price = Decimal("0")
            plan.planned_value = qty * unit_price

        elif field == "planned_value":
            from decimal import Decimal, InvalidOperation

            try:
                plan.planned_value = Decimal(str(value)) if value else Decimal("0")
            except (InvalidOperation, ValueError):
                return JsonResponse(
                    {"success": False, "message": "Некорректное число"}, status=400
                )
        else:
            return JsonResponse(
                {"success": False, "message": f"Неизвестное поле: {field}"}, status=400
            )

        if plan.planned_quantity < 0 or plan.planned_value < 0:
            return JsonResponse(
                {"error": "Значения не могут быть отрицательными"}, status=400
            )
        plan.full_clean()
        plan.save()

        return JsonResponse(
            {
                "success": True,
                "planned_quantity": float(plan.planned_quantity),
                "planned_value": float(plan.planned_value),
            }
        )
    except DailyPlan.DoesNotExist:
        return JsonResponse({"success": False, "message": "Не найдено"}, status=404)
    except Exception as e:
        return JsonResponse({"success": False, "message": str(e)}, status=400)


class PlanMatrixView(View):
    def get(self, request):
        from apps.works.models import ProjectWork
        from apps.works.progress import WorkProgressService

        works = ProjectWork.objects.filter(company=request.user.company).select_related(
            "section__construction_object__project"
        )
        for key, lookup in [
            ("project", "section__construction_object__project_id"),
            ("object", "section__construction_object_id"),
            ("section", "section_id"),
        ]:
            if request.GET.get(key):
                works = works.filter(**{lookup: request.GET[key]})
        rows = []
        for work in works:
            plan = WorkProgressService.planned(work)
            fact = WorkProgressService.facts(work)
            rows.append(
                {
                    "work": work,
                    "days": [
                        {
                            "date": d,
                            "plan": plan.get(d, {}).get("daily", 0),
                            "fact": fact.get(d, {}).get("daily", 0),
                        }
                        for d in sorted(set(plan) | set(fact))
                    ],
                }
            )
        return render(request, "planning/plan_matrix.html", {"rows": rows})


# Keep URL imports compatible while separating the planning components.
from .profile_views import (
    LoadProfileCreateView,
    LoadProfileDeleteView,
    LoadProfileDetailView,
    LoadProfileItemCreateView,
    LoadProfileItemDeleteView,
    LoadProfileItemUpdateView,
    LoadProfileListView,
    LoadProfileUpdateView,
)
from .calendar_views import (
    CalendarAutoFillView,
    CalendarCreateView,
    CalendarDayBulkEditView,
    CalendarDayUpdateView,
    CalendarDeleteView,
    CalendarDetailView,
    CalendarListView,
    CalendarUpdateView,
)
from .workflow_views import (
    SetBaselineVersionView,
    VersionApproveView,
    VersionCompleteView,
    VersionCreateView,
    VersionGenerateView,
    VersionRegenerateView,
    VersionRejectView,
    VersionRevisionView,
    VersionSubmitView,
)
