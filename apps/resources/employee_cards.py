"""Employee card: common employment fields and restricted HR/payroll data."""
from datetime import date
from decimal import Decimal

from django.core.exceptions import PermissionDenied
from django.contrib import messages
from django.urls import reverse
from django.views.generic import DetailView, UpdateView

from core.mixins import CompanyScopedMixin, CompanyRequiredMixin
from core.permissions import role_code
from .forms import EmployeeForm
from .models import Employee


def date_text(value):
    return date.fromisoformat(value).strftime('%d.%m.%Y') if value else '—'


class EmployeeDetail(CompanyScopedMixin, DetailView):
    model = Employee
    context_object_name = 'employee'
    template_name = 'resources/employee_detail.html'

    def get_queryset(self):
        return super().get_queryset().select_related('position')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        employee = self.object
        data = employee.zup_card
        context['employment_rows'] = [
            ('Табельный номер', employee.employee_number or '—'),
            ('Организация', employee.zup_organisation or '—'),
            ('Подразделение', employee.zup_department or '—'),
            ('Должность', employee.display_position or ('Не получена из ЗУП' if employee.zup_ref else '—')),
            ('Дата приёма', date_text(data.get('hire_date'))),
            ('Дата увольнения', date_text(data.get('dismissal_date'))),
            ('Количество ставок', data.get('fte', '—')),
            ('График работы', data.get('schedule') or '—'),
        ]
        context['position_since'] = date_text(data.get('position_since'))
        context['position_source'] = data.get('position_source', '')
        context['can_view_private'] = role_code(self.request.user) in {'ADMIN', 'HR_HEAD'}
        context['can_edit'] = not employee.zup_ref and role_code(self.request.user) in {'ADMIN', 'HR_HEAD', 'PLANNER'}
        if context['can_view_private']:
            context['personal_rows'] = [
                ('Полное имя', employee.display_name),
                ('Фамилия', employee.last_name or '—'), ('Имя', employee.first_name or '—'),
                ('Отчество', employee.middle_name or '—'),
                ('Дата рождения', date_text(data.get('birth_date'))),
                ('Место рождения', data.get('birth_place') or '—'),
                ('ИНН', data.get('inn') or '—'), ('СНИЛС', data.get('snils') or '—'),
            ]
            context['pay_rows'] = [
                (label, format(Decimal(data[key]), ',.2f').replace(',', ' ').replace('.', ',')+' ₽' if key in data else '—')
                for key, label in [('tariff_rate', 'Тарифная ставка'),
                                   ('combined_tariff_rate', 'Совокупная тарифная ставка'),
                                   ('payroll_fund', 'Плановый ФОТ')]
            ]
            context['pay_since'] = date_text(data.get('pay_since'))
        return context


class EmployeeUpdate(CompanyRequiredMixin, UpdateView):
    model = Employee
    form_class = EmployeeForm
    template_name = 'resources/employee_form.html'

    def get_object(self, queryset=None):
        employee = super().get_object(queryset)
        if employee.zup_ref:
            raise PermissionDenied('Эта карточка связана с ЗУП. Данные изменяются в источнике и обновляются загрузкой из ЗУП.')
        return employee

    def form_valid(self, form):
        messages.success(self.request, 'Карточка сотрудника сохранена.')
        return super().form_valid(form)

    def get_success_url(self):
        return reverse('resources:employee_detail', args=[self.object.pk])
