from .crew_views import EquipmentCrewEditorMixin
from .filters import CatalogFilterMixin, BrigadeFilterForm, EquipmentFilterForm
from django.views.generic import ListView, CreateView
from django.urls import reverse_lazy
from django.contrib import messages
from django.utils.translation import gettext_lazy as _

from core.mixins import CompanyRequiredMixin, CompanyScopedMixin

from .models import Employee, Brigade
from .forms import EmployeeForm, BrigadeForm
from django.shortcuts import get_object_or_404, redirect  # ← ДОБАВЛЕНО

class EmployeeListView(CompanyScopedMixin, ListView):
    model = Employee
    template_name = 'resources/employee_list.html'
    context_object_name = 'employees'
    paginate_by = 20


class EmployeeCreateView(CompanyRequiredMixin, CreateView):
    model = Employee
    form_class = EmployeeForm
    template_name = 'resources/employee_form.html'
    success_url = reverse_lazy('resources:employee_list')

    def form_valid(self, form):
        messages.success(self.request, 'Сотрудник успешно добавлен!')
        return super().form_valid(form)


class BrigadeListView(CatalogFilterMixin, CompanyRequiredMixin, ListView):
    filter_form_class = BrigadeFilterForm
    export_route = 'resources:brigade_export'
    model = Brigade
    template_name = 'resources/brigade_list.html'
    context_object_name = 'brigades'
    paginate_by = 20

    def get_queryset(self):
        return self.filter_queryset(Brigade.objects.filter(
            company=self.get_company(), merge_source__isnull=True
        ).select_related('group', 'macro_group').order_by('code'))

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        from apps.planning.meeting_import import key
        groups = {}
        for item in Brigade.objects.filter(company=self.get_company(), merge_source__isnull=True).order_by('pk'):
            groups.setdefault(key(item.name), []).append(item)
        context['brigade_duplicates'] = [items for items in groups.values() if len(items) > 1]
        return context



class BrigadeCreateView(CompanyRequiredMixin, CreateView):
    model = Brigade
    form_class = BrigadeForm
    template_name = 'resources/brigade_form.html'

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['instance'] = Brigade(company=self.get_company())
        return kwargs

    def form_valid(self, form):
        form.instance.company = self.request.user.company
        messages.success(self.request, 'Должность создана!')
        return super().form_valid(form)

    def get_success_url(self):
        return reverse_lazy('resources:brigade_list')


from django.views.generic import ListView, CreateView, UpdateView, DeleteView
from django.urls import reverse_lazy
from django.contrib import messages
from core.mixins import CompanyRequiredMixin
from .models import Brigade
from .forms import BrigadeForm







class BrigadeUpdateView(CompanyRequiredMixin, UpdateView):
    model = Brigade
    form_class = BrigadeForm
    template_name = 'resources/brigade_form.html'

    def form_valid(self, form):
        messages.success(self.request, 'Должность обновлена!')
        return super().form_valid(form)

    def get_success_url(self):
        return reverse_lazy('resources:brigade_list')


class BrigadeDeleteView(CompanyRequiredMixin, DeleteView):
    """Удаление должности."""
    model = Brigade
    template_name = 'resources/brigade_confirm_delete.html'
    success_url = reverse_lazy('resources:brigade_list')

    def post(self, request, *args, **kwargs):
        """Переопределяем post, чтобы обойти проблемный mixin."""
        self.object = self.get_object()

        # Проверяем, есть ли члены должности
        if self.object.members.exists():
            messages.error(
                request,
                f'Нельзя удалить должность "{self.object.name}": в ней {self.object.members.count()} человек. Сначала удалите назначенных сотрудников.'
            )
            return redirect('resources:brigade_list')

        name = self.object.name
        self.object.delete()
        messages.success(request, f'Должность "{name}" удалена.')
        return redirect(self.get_success_url())

from .models import EquipmentType
from .forms import EquipmentTypeForm


class EquipmentTypeListView(CatalogFilterMixin, CompanyRequiredMixin, ListView):
    filter_form_class = EquipmentFilterForm
    export_route = 'resources:equipment_type_export'
    model = EquipmentType
    template_name = 'resources/equipment_type_list.html'
    context_object_name = 'equipment_types'
    paginate_by = 20

    def get_queryset(self):
        return self.filter_queryset(EquipmentType.objects.filter(
            company=self.get_company(), merge_source__isnull=True
        ).select_related('category','parent').prefetch_related('crew_positions__position','model_variants').order_by(*(['category__name','category_id','name','pk'] if self.request.GET.get('view','groups') == 'groups' else ['name','pk'])))

    def get_paginate_by(self, queryset):
        return None if self.request.GET.get('view','groups') == 'groups' else 20

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['equipment_grouped'] = self.request.GET.get('view','groups') == 'groups'
        for mode in ['groups','table']:
            params = self.request.GET.copy()
            params.pop('page',None)
            params['view'] = mode
            context['equipment_'+mode+'_url'] = '?' + params.urlencode()
        from apps.planning.meeting_import import key
        groups = {}
        for item in EquipmentType.objects.filter(company=self.get_company(), merge_source__isnull=True).order_by('pk'):
            groups.setdefault(key(item.name), []).append(item)
        context['equipment_duplicates'] = [items for items in groups.values() if len(items) > 1]
        return context


class EquipmentTypeCreateView(EquipmentCrewEditorMixin, CompanyRequiredMixin, CreateView):
    model = EquipmentType
    form_class = EquipmentTypeForm
    template_name = 'resources/equipment_type_form.html'

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        if not kwargs.get('instance'):
            kwargs['instance'] = EquipmentType(company=self.get_company())
        return kwargs


    def get_success_url(self):
        return reverse_lazy('resources:equipment_type_list')


class EquipmentTypeUpdateView(EquipmentCrewEditorMixin, CompanyRequiredMixin, UpdateView):
    model = EquipmentType
    form_class = EquipmentTypeForm
    template_name = 'resources/equipment_type_form.html'

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        if not kwargs.get('instance'):
            kwargs['instance'] = EquipmentType(company=self.get_company())
        return kwargs


    def get_success_url(self):
        return reverse_lazy('resources:equipment_type_list')


class EquipmentTypeDeleteView(CompanyRequiredMixin, DeleteView):
    def form_valid(self,form):
        from django.db.models.deletion import ProtectedError
        try:return super().form_valid(form)
        except ProtectedError:
            messages.error(self.request,'Техника используется в планах или фактах. Сначала снимите связанные назначения.')
            return redirect('resources:equipment_type_list')

    model = EquipmentType
    template_name = 'resources/equipment_type_confirm_delete.html'
    success_url = reverse_lazy('resources:equipment_type_list')

    def delete(self, request, *args, **kwargs):
        et = self.get_object()
        messages.success(request, f'Вид техники "{et.name}" удалён')
        return super().delete(request, *args, **kwargs)

from .models import EquipmentCategory
from .forms import EquipmentCategoryForm
from django.http import JsonResponse
from django.db import IntegrityError, transaction
from django.views.decorators.http import require_POST
from core.permissions import require_roles, PLAN_ROLES


class EquipmentCategoryListView(CompanyScopedMixin, ListView):
    model = EquipmentCategory
    template_name = 'resources/equipment_category_list.html'
    context_object_name = 'categories'


@require_POST
def equipment_category_create(request):
    require_roles(request.user, PLAN_ROLES)
    form = EquipmentCategoryForm(request.POST, company=request.user.company)
    if form.is_valid():
        try:
            with transaction.atomic():
                category = form.save()
        except IntegrityError:
            return JsonResponse({'errors': {'name': ['Такая категория уже существует.']}}, status=400)
        return JsonResponse({'value': str(category.pk), 'label': category.name}, status=201)
    return JsonResponse({'errors': form.errors}, status=400)
