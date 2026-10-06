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
    model = Brigade
    template_name = 'resources/brigade_list.html'
    context_object_name = 'brigades'
    paginate_by = 20

    def get_queryset(self):
        return self.filter_queryset(Brigade.objects.filter(
            company=self.get_company()
        ).select_related('group', 'macro_group').order_by('code'))



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
        messages.success(self.request, 'Бригада создана!')
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
        messages.success(self.request, 'Бригада обновлена!')
        return super().form_valid(form)

    def get_success_url(self):
        return reverse_lazy('resources:brigade_list')


class BrigadeDeleteView(CompanyRequiredMixin, DeleteView):
    """Удаление бригады."""
    model = Brigade
    template_name = 'resources/brigade_confirm_delete.html'
    success_url = reverse_lazy('resources:brigade_list')

    def post(self, request, *args, **kwargs):
        """Переопределяем post, чтобы обойти проблемный mixin."""
        self.object = self.get_object()

        # Проверяем, есть ли члены бригады
        if self.object.members.exists():
            messages.error(
                request,
                f'Нельзя удалить бригаду "{self.object.name}": в ней {self.object.members.count()} человек. Сначала удалите членов бригады.'
            )
            return redirect('resources:brigade_list')

        name = self.object.name
        self.object.delete()
        messages.success(request, f'Бригада "{name}" удалена.')
        return redirect(self.get_success_url())

from .models import EquipmentType
from .forms import EquipmentTypeForm


class EquipmentTypeListView(CatalogFilterMixin, CompanyRequiredMixin, ListView):
    filter_form_class = EquipmentFilterForm
    model = EquipmentType
    template_name = 'resources/equipment_type_list.html'
    context_object_name = 'equipment_types'
    paginate_by = 20

    def get_queryset(self):
        return self.filter_queryset(EquipmentType.objects.filter(
            company=self.get_company()
        ).select_related('category').order_by('name', 'pk'))


class EquipmentTypeCreateView(CompanyRequiredMixin, CreateView):
    model = EquipmentType
    form_class = EquipmentTypeForm
    template_name = 'resources/equipment_type_form.html'

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        if not kwargs.get('instance'):
            kwargs['instance'] = EquipmentType(company=self.get_company())
        return kwargs

    def form_valid(self, form):
        form.instance.company = self.request.user.company
        messages.success(self.request, 'Вид техники создан!')
        return super().form_valid(form)

    def get_success_url(self):
        return reverse_lazy('resources:equipment_type_list')


class EquipmentTypeUpdateView(CompanyRequiredMixin, UpdateView):
    model = EquipmentType
    form_class = EquipmentTypeForm
    template_name = 'resources/equipment_type_form.html'

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        if not kwargs.get('instance'):
            kwargs['instance'] = EquipmentType(company=self.get_company())
        return kwargs

    def form_valid(self, form):
        messages.success(self.request, 'Вид техники обновлён!')
        return super().form_valid(form)

    def get_success_url(self):
        return reverse_lazy('resources:equipment_type_list')


class EquipmentTypeDeleteView(CompanyRequiredMixin, DeleteView):
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
