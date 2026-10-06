from django.shortcuts import get_object_or_404
from django.contrib import messages
from django.views.generic import (
    ListView,
    DetailView,
    CreateView,
    UpdateView,
    DeleteView,
)
from django.urls import reverse_lazy
from django.utils.translation import gettext_lazy as _
from django.db.models import Count, Sum, DecimalField
from .services import WorkItemGeneratorService
from apps.accounts.models import Company
from .models import ProjectWork, ProjectWorkItem
from ..projects.models import Section
from .forms import ProjectWorkForm, ProjectWorkItemForm, WorkSectionForm
from core.mixins import CompanyRequiredMixin, CompanyScopedMixin


class WorkListView(CompanyScopedMixin, ListView):
    model = ProjectWork
    template_name = "works/work_list.html"
    context_object_name = "works"
    paginate_by = 20

    def get_queryset(self):
        return (
            ProjectWork.objects.filter(company=self.request.user.company)
            .annotate(items_count=Count("items"))
            .select_related(
                "section",
                "section__construction_object",
                "section__construction_object__project",
            )
            .order_by("code", "pk")
        )

    def get_context_data(self, **kwargs):
        from apps.works.progress import WorkProgressService

        context = super().get_context_data(**kwargs)
        for work in context["works"]:
            planned = WorkProgressService.planned(work)
            work.total_quantity = (
                next(reversed(planned.values()))["cumulative"] if planned else 0
            )
        return context


class WorkDetailView(CompanyScopedMixin, DetailView):
    model = ProjectWork
    template_name = "works/work_tree.html"
    context_object_name = "work"

    def get_context_data(self, **kwargs):
        from apps.works.progress import WorkProgressService
        from decimal import Decimal

        context = super().get_context_data(**kwargs)
        work = self.object
        planned = WorkProgressService.planned(work)
        fact = WorkProgressService.completed(work)
        total = (
            next(reversed(planned.values()))["cumulative"] if planned else Decimal("0")
        )
        items = list(work.items.select_related("load_profile", "parent"))
        for item in items:
            item.children_count = item.children.count()
            qty = item.daily_facts.aggregate(total=Sum("actual_quantity"))["total"] or 0
            item.planned_quantity = total * item.quantity_per_unit
            item.completion = (
                int(qty / (total * item.quantity_per_unit) * 100) if total else 0
            )
        work.completion_percentage = int(fact / total * 100) if total else 0
        context.update(
            items=items,
            total_quantity=total,
            total_value=total * work.unit_price,
            total_weight=sum(i.weight for i in items),
            completed_quantity=fact,
        )
        return context


class WorkCreateView(CompanyRequiredMixin, CreateView):
    """Создание НОВОЙ РАБОТЫ с автоматической генерацией подработ из шаблона."""

    model = ProjectWork
    form_class = ProjectWorkForm
    template_name = "works/work_form.html"

    def form_valid(self, form):
        form.instance.company = self.request.user.company

        # Сохраняем работу
        response = super().form_valid(form)

        # Автоматически создаём подработы из шаблона
        if self.object.template:

            items = WorkItemGeneratorService.generate_from_template(self.object)
            if items:
                messages.success(
                    self.request,
                    f'Работа создана. Автоматически сформировано {len(items)} подработ из шаблона "{self.object.template.name}".',
                )
            else:
                messages.warning(
                    self.request,
                    "Работа создана, но шаблон не содержит элементов. Добавьте подработы вручную.",
                )
        elif self.object.kind == "COMPOSITE":
            messages.info(
                self.request,
                "Составная работа создана. Добавьте подработы и нормативы.",
            )
        else:
            messages.success(
                self.request, "Простая работа создана. Можно вводить план и факт."
            )

        return response

    def get_success_url(self):
        return reverse_lazy("works:work_detail", kwargs={"pk": self.object.pk})


class WorkUpdateView(CompanyScopedMixin, UpdateView):
    model = ProjectWork
    form_class = ProjectWorkForm
    template_name = "works/work_form.html"
    success_url = reverse_lazy("works:work_list")

    def form_valid(self, form):
        old_template = ProjectWork.objects.get(pk=self.object.pk).template
        response = super().form_valid(form)

        # Если изменился шаблон — перегенерируем подработы
        if (
            form.cleaned_data.get("template")
            and form.cleaned_data["template"] != old_template
        ):

            items = WorkItemGeneratorService.generate_from_template(self.object)
            if items:
                messages.success(
                    self.request,
                    f"Шаблон изменён. Сформировано {len(items)} новых подработ.",
                )

        return response


# ДОБАВЬТЕ ЭТОТ КЛАСС
class WorkDeleteView(CompanyScopedMixin, DeleteView):
    def form_valid(self, form):
        from django.core.exceptions import PermissionDenied

        if (
            self.object.daily_facts.exists()
            or self.object.monthly_plans.filter(
                versions__status__in=["APPROVED", "COMPLETED"]
            ).exists()
        ):
            raise PermissionDenied(
                "Работу с фактом или утверждённым планом удалять нельзя."
            )
        return super().form_valid(form)

    model = ProjectWork
    template_name = "works/work_confirm_delete.html"
    success_url = reverse_lazy("works:work_list")

    def delete(self, request, *args, **kwargs):
        work = self.get_object()
        work_name = work.name
        messages.success(request, f'Работа "{work_name}" успешно удалена!')
        return super().delete(request, *args, **kwargs)


# apps/works/views.py — добавить
class WorkItemDeleteView(CompanyScopedMixin, DeleteView):
    def form_valid(self, form):
        from django.core.exceptions import PermissionDenied

        work = self.object.project_work
        if (
            work.daily_facts.exists()
            or work.monthly_plans.filter(versions__daily_plans__isnull=False).exists()
        ):
            raise PermissionDenied("Подработы с планом или фактом удалять нельзя.")
        return super().form_valid(form)

    model = ProjectWorkItem
    template_name = "works/workitem_confirm_delete.html"

    def get_success_url(self):
        return reverse_lazy(
            "works:work_detail", kwargs={"pk": self.object.project_work_id}
        )

    def delete(self, request, *args, **kwargs):
        item = self.get_object()
        work_pk = item.project_work_id
        messages.success(request, f'Подработа "{item.name}" удалена')
        return super().delete(request, *args, **kwargs)


class WorkItemCreateView(CompanyRequiredMixin, CreateView):
    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        work = get_object_or_404(
            ProjectWork, pk=self.kwargs["work_pk"], company=self.request.user.company
        )
        kwargs["instance"] = ProjectWorkItem(
            project_work=work, company=self.request.user.company
        )
        return kwargs

    """Создание подработы."""
    model = ProjectWorkItem
    form_class = ProjectWorkItemForm
    template_name = "works/work_item_form.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        work_pk = self.kwargs.get("work_pk")
        if work_pk:
            context["work"] = get_object_or_404(
                ProjectWork, pk=work_pk, company=self.request.user.company
            )
            # Передаём список существующих подработ для выбора родителя
            context["parent_choices"] = ProjectWorkItem.objects.filter(
                project_work_id=work_pk, company=self.request.user.company
            ).order_by("sequence")
        else:
            context["work"] = None
            context["parent_choices"] = []
        return context

    def form_valid(self, form):
        work_pk = self.kwargs.get("work_pk")
        if work_pk:
            # Заполняем обязательное поле project_work
            form.instance.project_work = get_object_or_404(
                ProjectWork, pk=work_pk, company=self.request.user.company
            )
            form.instance.company = self.request.user.company

            # parent НЕ заполняем автоматически — он берётся из формы
            # (если пользователь выбрал родительскую подработу)

            messages.success(self.request, "Подработа успешно создана!")
            return super().form_valid(form)
        else:
            messages.error(self.request, "Не указана работа")
            return redirect("works:work_list")

    def get_success_url(self):
        work_pk = self.kwargs.get("work_pk")
        if work_pk:
            return reverse_lazy("works:work_detail", kwargs={"pk": work_pk})
        return reverse_lazy("works:work_list")


from django.views.generic import UpdateView


# Добавьте этот класс после WorkItemCreateView
class WorkItemUpdateView(CompanyScopedMixin, UpdateView):
    model = ProjectWorkItem
    form_class = ProjectWorkItemForm
    template_name = "works/work_item_form.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["work"] = self.object.project_work
        context["is_edit"] = True  # Флаг для шаблона
        return context

    def form_valid(self, form):
        messages.success(self.request, f'Подработа "{self.object.name}" обновлена!')
        return super().form_valid(form)

    def get_success_url(self):
        return reverse_lazy(
            "works:work_detail", kwargs={"pk": self.object.project_work_id}
        )


# apps/works/views.py


class WorkSectionCreateView(CompanyRequiredMixin, CreateView):
    """Создание раздела работ."""

    model = Section
    form_class = WorkSectionForm
    template_name = "works/section_form.html"

    def form_valid(self, form):
        form.instance.company = self.request.user.company
        messages.success(self.request, "Раздел создан!")
        return super().form_valid(form)

    def get_success_url(self):
        return reverse_lazy("works:work_create")
