from django.shortcuts import get_object_or_404
from django.contrib import messages
from django.views.generic import ListView, DetailView, CreateView, UpdateView, DeleteView
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
    template_name = 'works/work_list.html'
    context_object_name = 'works'
    paginate_by = 20

    def get_queryset(self):
        return ProjectWork.objects.filter(company=self.request.user.company).annotate(
            items_count=Count('items'),
            total_quantity=Sum('items__planned_quantity', output_field=DecimalField())
        ).select_related('section', 'section__construction_object', 'section__construction_object__project').order_by('code', 'pk')


class WorkDetailView(CompanyScopedMixin, DetailView):
    model = ProjectWork
    template_name = 'works/work_tree.html'
    context_object_name = 'work'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        work = self.object

        items = work.items.select_related(
            'parent', 'load_profile'
        ).order_by('sequence')

        from apps.production.models import DailyFact
        from decimal import Decimal

        for item in items:
            item.children_count = work.items.filter(parent=item).count()

            fact_total = DailyFact.objects.filter(
                company=self.request.user.company,
                project_work=work, work_item=item
            ).aggregate(total=Sum('actual_quantity'))['total'] or Decimal('0')

            if item.planned_quantity and item.planned_quantity > 0:
                item.completion = int(
                    (fact_total / item.planned_quantity * 100)
                )
            else:
                item.completion = 0

        context['items'] = items
        context['total_weight'] = sum(i.weight for i in items)
        context['total_quantity'] = sum(
            i.planned_quantity for i in items if i.planned_quantity
        ) or 0
        context['total_value'] = context['total_quantity'] * work.unit_price

        total_completion = sum(
            float(i.weight) * float(i.completion) / 100 for i in items
        )
        context['work'].completion_percentage = int(total_completion)

        return context


class WorkCreateView(CompanyRequiredMixin, CreateView):
    """Создание НОВОЙ РАБОТЫ с автоматической генерацией подработ из шаблона."""
    model = ProjectWork
    form_class = ProjectWorkForm
    template_name = 'works/work_form.html'

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
                    f'Работа создана. Автоматически сформировано {len(items)} подработ из шаблона "{self.object.template.name}".'
                )
            else:
                messages.warning(
                    self.request,
                    'Работа создана, но шаблон не содержит элементов. Добавьте подработы вручную.'
                )
        else:
            messages.info(
                self.request,
                'Работа создана без шаблона. Добавьте подработы вручную.'
            )

        return response

    def get_success_url(self):
        return reverse_lazy('works:work_detail', kwargs={'pk': self.object.pk})


class WorkUpdateView(CompanyScopedMixin, UpdateView):
    model = ProjectWork
    form_class = ProjectWorkForm
    template_name = 'works/work_form.html'
    success_url = reverse_lazy('works:work_list')

    def form_valid(self, form):
        old_template = self.object.template
        response = super().form_valid(form)

        # Если изменился шаблон — перегенерируем подработы
        if form.cleaned_data.get('template') and form.cleaned_data['template'] != old_template:

            items = WorkItemGeneratorService.generate_from_template(self.object)
            if items:
                messages.success(
                    self.request,
                    f'Шаблон изменён. Сформировано {len(items)} новых подработ.'
                )

        return response


# ДОБАВЬТЕ ЭТОТ КЛАСС
class WorkDeleteView(CompanyScopedMixin, DeleteView):
    model = ProjectWork
    template_name = 'works/work_confirm_delete.html'
    success_url = reverse_lazy('works:work_list')

    def delete(self, request, *args, **kwargs):
        work = self.get_object()
        work_name = work.name
        messages.success(request, f'Работа "{work_name}" успешно удалена!')
        return super().delete(request, *args, **kwargs)


# apps/works/views.py — добавить
class WorkItemDeleteView(CompanyScopedMixin, DeleteView):
    model = ProjectWorkItem
    template_name = 'works/workitem_confirm_delete.html'

    def get_success_url(self):
        return reverse_lazy('works:work_detail', kwargs={'pk': self.object.project_work_id})

    def delete(self, request, *args, **kwargs):
        item = self.get_object()
        work_pk = item.project_work_id
        messages.success(request, f'Подработа "{item.name}" удалена')
        return super().delete(request, *args, **kwargs)


class WorkItemCreateView(CompanyRequiredMixin, CreateView):
    """Создание подработы."""
    model = ProjectWorkItem
    form_class = ProjectWorkItemForm
    template_name = 'works/work_item_form.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        work_pk = self.kwargs.get('work_pk')
        if work_pk:
            context['work'] = get_object_or_404(
                ProjectWork,
                pk=work_pk,
                company=self.request.user.company
            )
            # Передаём список существующих подработ для выбора родителя
            context['parent_choices'] = ProjectWorkItem.objects.filter(
                project_work_id=work_pk,
                company=self.request.user.company
            ).order_by('sequence')
        else:
            context['work'] = None
            context['parent_choices'] = []
        return context

    def form_valid(self, form):
        work_pk = self.kwargs.get('work_pk')
        if work_pk:
            # Заполняем обязательное поле project_work
            form.instance.project_work = get_object_or_404(
                ProjectWork, pk=work_pk, company=self.request.user.company
            )
            form.instance.company = self.request.user.company

            # parent НЕ заполняем автоматически — он берётся из формы
            # (если пользователь выбрал родительскую подработу)

            messages.success(self.request, 'Подработа успешно создана!')
            return super().form_valid(form)
        else:
            messages.error(self.request, 'Не указана работа')
            return redirect('works:work_list')

    def get_success_url(self):
        work_pk = self.kwargs.get('work_pk')
        if work_pk:
            return reverse_lazy('works:work_detail', kwargs={'pk': work_pk})
        return reverse_lazy('works:work_list')

from django.views.generic import  UpdateView


# Добавьте этот класс после WorkItemCreateView
class WorkItemUpdateView(CompanyScopedMixin, UpdateView):
    model = ProjectWorkItem
    form_class = ProjectWorkItemForm
    template_name = 'works/work_item_form.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['work'] = self.object.project_work
        context['is_edit'] = True  # Флаг для шаблона
        return context

    def form_valid(self, form):
        messages.success(self.request, f'Подработа "{self.object.name}" обновлена!')
        return super().form_valid(form)

    def get_success_url(self):
        return reverse_lazy('works:work_detail', kwargs={'pk': self.object.project_work_id})


# apps/works/views.py

class WorkSectionCreateView(CompanyRequiredMixin, CreateView):
    """Создание раздела работ."""
    model = Section
    form_class = WorkSectionForm
    template_name = 'works/section_form.html'

    def form_valid(self, form):
        form.instance.company = self.request.user.company
        messages.success(self.request, 'Раздел создан!')
        return super().form_valid(form)

    def get_success_url(self):
        return reverse_lazy('works:work_create')
