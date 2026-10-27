from core.catalogue_excel import CatalogueExcelMixin
import re

from django.db import connection
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
from django.db.models import Count, Sum, DecimalField, Exists, OuterRef, Q
from .list_filters import WorkListFilterForm
from .services import WorkItemGeneratorService
from apps.accounts.models import Company
from .models import ProjectWork, ProjectWorkItem
from ..projects.models import Section
from .forms import ProjectWorkForm, ProjectWorkItemForm, WorkSectionForm
from core.mixins import CompanyRequiredMixin, CompanyScopedMixin
from core.permissions import scope_queryset


class WorkListView(CatalogueExcelMixin, CompanyScopedMixin, ListView):
    model = ProjectWork
    template_name = "works/work_list.html"
    context_object_name = "works"
    paginate_by = 20

    def catalogue_view_mode(self):
        mode=self.request.GET.get('view',self.request.session.get('work_catalogue_view','tree'))
        return mode if mode in {'table','tree'} else 'tree'

    def get_paginate_by(self, queryset):
        return None if self.catalogue_view_mode()=='tree' else self.paginate_by

    def get_queryset(self):
        self.filter_form = WorkListFilterForm(self.request.GET, company=self.request.user.company, user=self.request.user)
        from core.permissions import scope_queryset
        queryset = scope_queryset(ProjectWork.objects.filter(merged_source__isnull=True), self.request.user)
        if self.filter_form.is_valid():
            values = self.filter_form.cleaned_data
            for field, path in [('project', 'section__construction_object__project'), ('construction_object', 'section__construction_object'), ('section', 'section')]:
                if values[field]:
                    queryset = queryset.filter(**{path: values[field]})
            if values['complex_object']:
                queryset=queryset.filter(complex_object=values['complex_object'])
            if values['package']:
                queryset=queryset.filter(pk=values['package'].pk)
            if values['selected_work']:
                kind, selected_id=values['selected_work'].split(':')
                queryset=queryset.filter(pk=selected_id) if kind=='w' else queryset.filter(pk__in=ProjectWorkItem.objects.filter(company=self.request.user.company,pk=selected_id).values('project_work_id'))
            if values.get('legacy_only'):
                matching_legacy=ProjectWorkItem.objects.filter(company=self.request.user.company,project_work_id=OuterRef('pk'),is_legacy_structure=True)
                queryset=queryset.filter(Q(is_legacy_structure=True)|Exists(matching_legacy))
            # SQLite LIKE does not fold Cyrillic case. Its regex implementation
            # does; escape the query so punctuation remains literal.
            lookup = 'iregex' if connection.vendor == 'sqlite' else 'icontains'
            if values['q']:
                value = re.escape(values['q']) if lookup == 'iregex' else values['q']
                matching = ProjectWorkItem.objects.filter(company=self.request.user.company,
                    project_work_id=OuterRef('pk'), **{'name__' + lookup: value})
                queryset = queryset.annotate(catalogue_child_match=Exists(matching)).filter(
                    Q(**{'name__' + lookup: value}) | Q(catalogue_child_match=True))
            if values['work']:
                value = re.escape(values['work']) if lookup == 'iregex' else values['work']
                queryset = queryset.filter(**{'name__' + lookup: value})
            if values['subwork']:
                value = re.escape(values['subwork']) if lookup == 'iregex' else values['subwork']
                matching_items = ProjectWorkItem.objects.filter(
                    company=self.request.user.company, project_work_id=OuterRef('pk'),
                    **{'name__' + lookup: value},
                )
                queryset = queryset.filter(Exists(matching_items))
        else:
            queryset = queryset.none()
        return queryset.annotate(items_count=Count('items')).select_related(
            'section', 'complex_object', 'section__construction_object', 'section__construction_object__project'
        ).prefetch_related('items').order_by('code', 'pk')

    def get_context_data(self, **kwargs):
        from apps.works.progress import WorkProgressService

        context = super().get_context_data(**kwargs)
        context['filter_form'] = self.filter_form
        mode=self.catalogue_view_mode()
        self.request.session['work_catalogue_view']=mode
        context['view_mode']=mode
        values = self.filter_form.cleaned_data if self.filter_form.is_valid() else {}
        query = values.get('q', '')
        context['search_query'] = query or values.get('subwork', '') or values.get('work', '')
        context['catalogue_reveal'] = bool(query or values.get('work') or values.get('subwork') or values.get('selected_work') or values.get('package'))
        extra = ['project', 'section', 'package', 'selected_work', 'work', 'subwork','legacy_only']
        context['advanced_filter_count'] = sum(bool(values.get(name)) for name in extra)
        context['advanced_filters_open'] = bool(context['advanced_filter_count'] or self.filter_form.errors)
        context['result_count'] = context['paginator'].count if context.get('paginator') else len(context['works'])
        context['bulk_contextual'] = True
        for work in context['works']:
            work.catalogue_match = bool(context['search_query'] and context['search_query'].casefold() in work.name.casefold())
            work.catalogue_items = list(work.items.all())
            for item in work.catalogue_items:
                item.catalogue_match = bool(context['search_query'] and context['search_query'].casefold() in item.name.casefold())
            work.catalogue_matching_items = [i for i in work.catalogue_items if i.catalogue_match]
        groups={}
        for work in context['works']:
            obj=work.section.construction_object
            group=groups.setdefault(obj.pk,{'object':obj,'sections':{}})
            section=group['sections'].setdefault(work.section_id,{'section':work.section,'branches':{}})
            branch=section['branches'].setdefault(work.complex_object_id,{'object':work.complex_object,'works':[]})
            branch['works'].append(work)
        context['tree_groups']=[{'object':g['object'],'sections':[{'section':s['section'],'branches':list(s['branches'].values())} for s in g['sections'].values()]} for g in groups.values()]
        for key in ['table','tree']:
            query=self.request.GET.copy();query['view']=key;query.pop('page',None)
            context[key+'_view_query']=query.urlencode()

        params = self.request.GET.copy()
        params.pop('page', None)
        context['filter_query'] = params.urlencode()
        for work in context["works"] if mode=='table' else []:
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
        context['merge_sources']=self.object.items.filter(merge_origin__isnull=False).select_related('merge_origin__source_work')
        context['merge_plans']=self.object.merge_plan_revisions.select_related('target_version','source_version')
        work = self.object
        from .prices import price_on
        context["current_price"] = price_on(work)
        context["price_history"] = work.price_history.select_related("created_by", "corrects").order_by("-effective_from", "-pk")
        planned = WorkProgressService.planned(work)
        fact = WorkProgressService.completed(work)
        total = (
            next(reversed(planned.values()))["cumulative"] if planned else Decimal("0")
        )
        items = list(work.items.select_related("load_profile", "parent").order_by('sequence', 'pk'))
        from .cost_shares import CostSharesForm
        from .structure import WorkStructureForm
        from .navigation import return_path
        shares_form = getattr(self, 'cost_shares_form', None)
        if shares_form is None:shares_form = CostSharesForm(items=items)
        structure_form = getattr(self, 'structure_form', None)
        if structure_form is None:structure_form = WorkStructureForm(items=items, company=self.request.user.company)
        for item in items:
            item.structure_unit = structure_form[f'unit_{item.pk}']
            item.structure_norm = structure_form[f'norm_{item.pk}']
            item.structure_profile = structure_form[f'profile_{item.pk}']
            item.structure_share = shares_form[f'share_{item.pk}'] if getattr(self, 'cost_shares_form', None) is not None else structure_form[f'share_{item.pk}']
            item.cost_share_input = shares_form[f'share_{item.pk}']
            item.children_count = item.children.count()
            qty = item.daily_facts.aggregate(total=Sum("actual_quantity"))["total"] or 0
            item.planned_quantity = total * item.quantity_per_unit
            item.completion = (
                int(qty / (total * item.quantity_per_unit) * 100) if total and item.quantity_per_unit else 0
            )
        work.completion_percentage = int(fact / total * 100) if total else 0
        context.update(
            items=items,
            total_quantity=total,
            total_value=total * work.unit_price,
            total_weight=sum(i.weight for i in items),
            completed_quantity=fact,
            cost_shares_form=shares_form,
            structure_form=structure_form,
            catalogue_return=return_path(self.request),
        )
        return context


class WorkLocationMixin:
    def get_form_kwargs(self):
        return {**super().get_form_kwargs(), 'user': self.request.user}

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['work_hierarchy'] = context['form'].hierarchy
        context['work'] = getattr(self, 'object', None)
        from .navigation import return_path
        context['catalogue_return'] = return_path(self.request)
        return context


class WorkCreateView(WorkLocationMixin, CompanyRequiredMixin, CreateView):
    """Создание НОВОЙ РАБОТЫ с автоматической генерацией работ в составе комплекса из шаблона."""

    model = ProjectWork
    form_class = ProjectWorkForm
    template_name = "works/work_form.html"

    def get_initial(self):
        initial = super().get_initial()
        for field in ['project', 'construction_object', 'section', 'complex_object']:
            value = self.request.GET.get(field)
            if value and value.isdigit():initial[field] = value
        kind = self.request.GET.get('kind')
        if kind in ProjectWork.Kind.values:
            initial['kind'] = kind
        return initial

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        kind = context['form']['kind'].value()
        context['creation_title'] = 'Новая комплексная работа' if kind == ProjectWork.Kind.COMPOSITE else 'Новая простая работа'
        return context

    def form_valid(self, form):
        form.instance.company = self.request.user.company
        form.instance._price_actor = self.request.user

        # Сохраняем работу
        response = super().form_valid(form)
        source = form.cleaned_data.get('price_source')
        if source:
            from .price_sources import source_note
            self.object.price_history.update(reason=source_note(source))

        # Автоматически создаём подработы из шаблона
        if self.object.template:

            items = WorkItemGeneratorService.generate_from_template(self.object)
            if items:
                messages.success(
                    self.request,
                    f'Работа создана. Автоматически сформировано {len(items)} работ в составе комплекса из шаблона "{self.object.template.name}".',
                )
            else:
                messages.warning(
                    self.request,
                    "Работа создана, но шаблон не содержит элементов. Добавьте работы в составе комплекса вручную.",
                )
        elif self.object.kind == "COMPOSITE":
            messages.info(
                self.request,
                "Комплекс работ создана. Добавьте работы в составе комплекса и нормативы.",
            )
        else:
            messages.success(
                self.request, "Самостоятельная работа создана. Можно вводить план и факт."
            )

        return response

    def get_success_url(self):
        url = str(reverse_lazy('works:work_detail', kwargs={'pk': self.object.pk}))
        if self.request.POST.get('return_to') or self.request.GET.get('return_to'):
            from .navigation import return_path
            from urllib.parse import urlencode
            url += '?' + urlencode({'return_to': return_path(self.request)})
        return url


class WorkUpdateView(WorkLocationMixin, CompanyScopedMixin, UpdateView):
    model = ProjectWork
    form_class = ProjectWorkForm
    template_name = "works/work_form.html"
    success_url = reverse_lazy("works:work_list")

    def get_success_url(self):
        from .navigation import return_path
        return return_path(self.request)

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
                    f"Шаблон изменён. Сформировано {len(items)} новых работ в составе комплекса.",
                )

        return response


# ДОБАВЬТЕ ЭТОТ КЛАСС
class WorkDeleteView(CompanyScopedMixin, DeleteView):
    def form_valid(self, form):
        from django.core.exceptions import PermissionDenied

        if self.object.resource_assignments.exists() or self.object.shared_resource_links.exists() or self.object.items.filter(resource_assignments__isnull=False).exists():
            raise PermissionDenied('Сначала снимите назначения ресурсов с этой работы в планах.')
        if hasattr(self.object,'merged_source') or self.object.items.filter(merge_origin__isnull=False).exists():
            raise PermissionDenied('Работа участвует в объединении и сохраняется для истории.')
        if (
            self.object.workmonthallocation_set.exists()
            or self.object.daily_facts.exists()
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

        if self.object.resource_assignments.exists() or self.object.shared_resource_links.exists():
            raise PermissionDenied('Сначала снимите назначения ресурсов с этой работы в планах.')
        if hasattr(self.object,'merge_origin'):
            raise PermissionDenied('Работа в составе комплекса сохраняет историю объединения и не может быть удалена.')
        work = self.object.project_work
        from apps.planning.models import GlobalPlanVersion

        if (
            GlobalPlanVersion.objects.filter(
                work_allocations__work=work,
                status__in=["SUBMITTED", "APPROVED", "COMPLETED"],
            ).exists()
            or work.daily_facts.exists()
            or work.monthly_plans.filter(versions__daily_plans__isnull=False).exists()
        ):
            raise PermissionDenied("Работы в составе комплекса с планом или фактом удалять нельзя.")
        return super().form_valid(form)

    model = ProjectWorkItem
    template_name = "works/workitem_confirm_delete.html"

    def get_success_url(self):
        from .navigation import return_path
        return return_path(self.request, str(reverse_lazy(
            "works:work_detail", kwargs={"pk": self.object.project_work_id})))

    def delete(self, request, *args, **kwargs):
        item = self.get_object()
        work_pk = item.project_work_id
        messages.success(request, f'Работа в составе комплекса "{item.name}" удалена')
        return super().delete(request, *args, **kwargs)


class WorkItemCreateView(CompanyRequiredMixin, CreateView):
    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['user']=self.request.user
        work = get_object_or_404(
            scope_queryset(ProjectWork.objects.all(),self.request.user), pk=self.kwargs["work_pk"]
        )
        kwargs["instance"] = ProjectWorkItem(
            project_work=work, company=self.request.user.company
        )
        return kwargs

    """Создание работы в составе комплекса."""
    model = ProjectWorkItem
    form_class = ProjectWorkItemForm
    template_name = "works/work_item_form.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        work_pk = self.kwargs.get("work_pk")
        if work_pk:
            context["work"] = get_object_or_404(
                scope_queryset(ProjectWork.objects.all(),self.request.user), pk=work_pk
            )
            # Передаём список существующих подработ для выбора родителя
            context["parent_choices"] = ProjectWorkItem.objects.filter(
                project_work_id=work_pk, company=self.request.user.company
            ).order_by("sequence")
        else:
            context["work"] = None
            context["parent_choices"] = []
        from .navigation import return_path
        context['catalogue_return'] = return_path(self.request,
            str(reverse_lazy('works:work_detail', kwargs={'pk': work_pk})))
        return context

    def form_valid(self, form):
        work_pk = self.kwargs.get("work_pk")
        if work_pk:
            # Заполняем обязательное поле project_work
            form.instance.project_work = get_object_or_404(
                scope_queryset(ProjectWork.objects.all(),self.request.user), pk=work_pk
            )
            form.instance.company = self.request.user.company

            # parent НЕ заполняем автоматически — он берётся из формы
            # (если пользователь выбрал родительскую подработу)

            messages.success(self.request, "Работа в составе комплекса успешно создана!")
            return super().form_valid(form)
        else:
            messages.error(self.request, "Не указана работа")
            return redirect("works:work_list")

    def get_success_url(self):
        work_pk = self.kwargs.get("work_pk")
        if work_pk:
            from .navigation import return_path
            return return_path(self.request, str(reverse_lazy("works:work_detail", kwargs={"pk": work_pk})))
        return reverse_lazy("works:work_list")


from django.views.generic import UpdateView


# Добавьте этот класс после WorkItemCreateView
class WorkItemUpdateView(CompanyScopedMixin, UpdateView):
    def get_form_kwargs(self):
        return {**super().get_form_kwargs(),'user':self.request.user}

    model = ProjectWorkItem
    form_class = ProjectWorkItemForm
    template_name = "works/work_item_form.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["work"] = self.object.project_work
        context["is_edit"] = True  # Флаг для шаблона
        from .navigation import return_path
        context['catalogue_return'] = return_path(self.request,
            str(reverse_lazy('works:work_detail', kwargs={'pk': self.object.project_work_id})))
        return context

    def form_valid(self, form):
        messages.success(self.request, f'Работа в составе комплекса "{self.object.name}" обновлена!')
        return super().form_valid(form)

    def get_success_url(self):
        from .navigation import return_path
        return return_path(self.request, str(reverse_lazy(
            "works:work_detail", kwargs={"pk": self.object.project_work_id})))


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
