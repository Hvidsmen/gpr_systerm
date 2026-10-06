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


class BrigadeListView(CompanyRequiredMixin, ListView):
    model = Brigade
    template_name = 'resources/brigade_list.html'
    context_object_name = 'brigades'
    paginate_by = 20

    def get_queryset(self):
        # УБРАЛИ .select_related('foreman')
        return Brigade.objects.filter(
            company=self.request.user.company
        ).order_by('code')



class BrigadeCreateView(CompanyRequiredMixin, CreateView):
    model = Brigade
    form_class = BrigadeForm
    template_name = 'resources/brigade_form.html'

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


class EquipmentTypeListView(CompanyRequiredMixin, ListView):
    model = EquipmentType
    template_name = 'resources/equipment_type_list.html'
    context_object_name = 'equipment_types'
    paginate_by = 20

    def get_queryset(self):
        return EquipmentType.objects.filter(
            company=self.request.user.company
        ).order_by('name')


class EquipmentTypeCreateView(CompanyRequiredMixin, CreateView):
    model = EquipmentType
    form_class = EquipmentTypeForm
    template_name = 'resources/equipment_type_form.html'

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