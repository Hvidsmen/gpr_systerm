"""Employee card: common employment fields and restricted personal data."""
from datetime import date

from django.core.exceptions import PermissionDenied
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.urls import reverse
from django.views.decorators.http import require_GET
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
        return super().get_queryset().select_related('position__planning_mapping__brigade')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        employee = self.object
        data = employee.zup_card
        context['employment_rows'] = [
            ('Табельный номер', employee.employee_number or '—'),
            ('Организация', employee.zup_organisation or '—'),
            ('Подразделение', employee.display_department or '—'),
            ('Должность (штатная позиция)' if employee.position_from_staffing else 'Должность', employee.display_position or ('Не получена из ЗУП' if employee.zup_ref else '—')),
            ('Дата приёма', date_text(data.get('hire_date'))),
            ('Дата увольнения', date_text(data.get('dismissal_date'))),
            ('Количество ставок', data.get('fte', '—')),
            ('График работы', data.get('schedule') or '—'),
        ]
        mapping=getattr(employee.position,'planning_mapping',None) if employee.position_id else None
        if mapping and mapping.company_id==employee.company_id and mapping.brigade.company_id==employee.company_id:
            from urllib.parse import urlencode
            from .models import BrigadeMerge
            merged=BrigadeMerge.objects.filter(company_id=employee.company_id,source_id=mapping.brigade_id).select_related('target').first()
            context['catalogue_job']=merged.target if merged else mapping.brigade
            context['catalogue_job_url']=reverse('resources:brigade_list')+'?'+urlencode({'q':context['catalogue_job'].name})
        context['position_since'] = date_text(data.get('staffing_since' if employee.position_from_staffing else 'position_since'))
        context['position_source'] = data.get('staffing_source' if employee.position_from_staffing else 'position_source', '')
        context['position_reason'] = {
            'ambiguous': 'В источнике найдено несколько вариантов назначения. Автоматический выбор должности не выполнен.',
            'no_staffing_assignment': 'Не найдена однозначная штатная позиция с наименованием для этого сотрудника.',
            'no_position_link': 'В штатной позиции не заполнена ссылка на отдельную должность.',
            'missing_job_record': 'Ссылка на должность есть, но соответствующая запись справочника не найдена.',
            'empty_job_name': 'Запись должности найдена, но её наименование пустое.',
        }.get(data.get('position_state'), '')
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


@login_required
@require_GET
def employee_position_report(request, pk):
    from core.permissions import require_roles
    from django.shortcuts import get_object_or_404
    from django.http import Http404
    from apps.accounts.one_c_report import _download_response
    require_roles(request.user, {'ADMIN'})
    employee = get_object_or_404(Employee, pk=pk, company=request.user.company)
    trace = employee.zup_card.get('position_diagnostics')
    if not employee.zup_ref or not trace:
        raise Http404('Обновите сотрудников из ЗУП для получения проверки должности.')
    payload = {
        'format': 'gpr-zup-employee-position-diagnostics', 'version': 1,
        'employee_id': employee.pk, 'employee_number': employee.employee_number,
        'employee_ref': employee.zup_ref,
        'synced_at': employee.zup_synced_at.isoformat() if employee.zup_synced_at else None,
        'state': employee.zup_card.get('position_state'),
        'has_job_name': bool(employee.zup_position_name),
        'has_staffing_name': bool(employee.zup_card.get('staffing_position')),
        'probes': trace.get('probes', {}), 'references': trace.get('references', {}),
    }
    return _download_response(payload, f'zup-employee-position-{employee.pk}', zipped=True)
