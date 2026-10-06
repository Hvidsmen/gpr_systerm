"""
Views для фактов по людям (бригадам).
"""
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.views.generic import ListView, CreateView, UpdateView, DeleteView, View
from django.urls import reverse_lazy
from django.http import JsonResponse
from django.views.decorators.http import require_POST
from django.views.decorators.csrf import csrf_protect

from core.mixins import CompanyRequiredMixin
from apps.production.models import LaborFact
from apps.production.forms import LaborFactForm
from apps.production.views.base import parse_fk_id, build_update_data
from apps.projects.models import Project
from apps.works.models import ProjectWork
from apps.resources.models import Brigade


# =============================================================================
# СПИСОК (МАТРИЦА)
# =============================================================================

class LaborFactListView(ListView):
    model = LaborFact
    template_name = 'production/labor_fact_list.html'
    context_object_name = 'labor_facts'
    paginate_by = None

    def get_queryset(self):
        qs = LaborFact.objects.filter(company=self.request.user.company) \
            .select_related('project', 'project_work', 'brigade')

        project_id = self.request.GET.get('project')
        if project_id:
            qs = qs.filter(project_id=project_id)

        work_id = self.request.GET.get('project_work')
        if work_id:
            qs = qs.filter(project_work_id=work_id)

        brigade_ids = self.request.GET.getlist('brigade')
        if brigade_ids:
            qs = qs.filter(brigade_id__in=brigade_ids)

        date_from = self.request.GET.get('date_from')
        date_to = self.request.GET.get('date_to')
        if date_from:
            qs = qs.filter(date__gte=date_from)
        if date_to:
            qs = qs.filter(date__lte=date_to)

        return qs.order_by('brigade__name', 'project_work__name', 'date')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        facts = context['labor_facts']

        rows = {}
        dates_set = set()
        for fact in facts:
            row_key = f"{fact.brigade_id}_{fact.project_work_id or 0}"
            if row_key not in rows:
                rows[row_key] = {
                    'brigade': fact.brigade,
                    'project_work': fact.project_work,
                    'project': fact.project,
                    'facts_by_date': {}
                }
            rows[row_key]['facts_by_date'][fact.date.isoformat()] = fact
            dates_set.add(fact.date)

        sorted_dates = sorted(dates_set)
        sorted_rows = sorted(
            rows.values(),
            key=lambda x: (x['brigade'].name, x['project_work'].name if x['project_work'] else '')
        )

        context.update({
            'matrix_rows': sorted_rows,
            'matrix_dates': sorted_dates,
            'date_from': self.request.GET.get('date_from', ''),
            'date_to': self.request.GET.get('date_to', ''),
            'selected_project': self.request.GET.get('project', ''),
            'selected_work': self.request.GET.get('project_work', ''),
            'selected_brigades': self.request.GET.getlist('brigade'),
            'projects': Project.objects.filter(company=self.request.user.company).order_by('name'),
            'works': ProjectWork.objects.filter(company=self.request.user.company).order_by('name'),
            'brigades': Brigade.objects.filter(company=self.request.user.company, is_active=True).order_by('name'),
        })
        return context


# =============================================================================
# CRUD
# =============================================================================

def _setup_labor_fact_form(form, company):
    """Настройка queryset'ов формы факта по людям."""
    form.fields['project'].queryset = Project.objects.filter(company=company)
    form.fields['project_work'].queryset = ProjectWork.objects.filter(company=company)
    form.fields['brigade'].queryset = Brigade.objects.filter(company=company, is_active=True)


class LaborFactCreateView(CompanyRequiredMixin, CreateView):
    model = LaborFact
    form_class = LaborFactForm
    template_name = 'production/resource_form.html'

    def get_form(self, form_class=None):
        form = super().get_form(form_class)
        _setup_labor_fact_form(form, self.request.user.company)
        return form

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        work_pk = self.kwargs.get('work_pk')
        if work_pk:
            context['work'] = get_object_or_404(ProjectWork, pk=work_pk)
        context['title'] = 'Ввод факта по людям'
        context['resource_type'] = 'labor'
        return context

    def form_valid(self, form):
        form.instance.company = self.request.user.company
        messages.success(self.request, 'Данные по бригаде сохранены!')
        return super().form_valid(form)

    def get_success_url(self):
        work_pk = self.kwargs.get('work_pk')
        return reverse_lazy('production:fact_daily') if work_pk else reverse_lazy('production:labor_fact_list')


class LaborFactUpdateView(UpdateView):
    model = LaborFact
    form_class = LaborFactForm
    template_name = 'production/resource_form.html'

    def get_form(self, form_class=None):
        form = super().get_form(form_class)
        _setup_labor_fact_form(form, self.request.user.company)
        return form

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['title'] = 'Редактирование факта по людям'
        context['resource_type'] = 'labor'
        return context

    def form_valid(self, form):
        messages.success(self.request, 'Данные по бригаде обновлены!')
        return super().form_valid(form)

    def get_success_url(self):
        return reverse_lazy('production:labor_fact_list')


class LaborFactDeleteView(DeleteView):
    model = LaborFact
    template_name = 'production/resource_confirm_delete.html'
    success_url = reverse_lazy('production:labor_fact_list')

    def delete(self, request, *args, **kwargs):
        fact = self.get_object()
        messages.success(request, f'Факт по бригаде "{fact.brigade}" за {fact.date} удалён')
        return super().delete(request, *args, **kwargs)


# =============================================================================
# INLINE UPDATE
# =============================================================================

@csrf_protect
@require_POST
def labor_fact_inline_update(request):
    import json
    try:
        data = json.loads(request.body)
        fact = LaborFact.objects.get(pk=data['fact_id'], company=request.user.company)

        field_map = {
            'actual_workers': lambda v: int(v) if v else 0,
            'actual_hours': lambda v: float(v) if v else None,
            'hourly_rate': lambda v: float(v) if v else None,
        }

        field = data.get('field')
        value = data.get('value')

        if field == 'comment':
            fact.comment = value
        elif field in field_map:
            setattr(fact, field, field_map[field](value))
        else:
            return JsonResponse({'success': False, 'message': f'Неизвестное поле: {field}'}, status=400)

        fact.save()
        return JsonResponse({'success': True})
    except LaborFact.DoesNotExist:
        return JsonResponse({'success': False, 'message': 'Не найдено'}, status=404)
    except Exception as e:
        return JsonResponse({'success': False, 'message': str(e)}, status=400)


# =============================================================================
# МАССОВЫЕ ОПЕРАЦИИ
# =============================================================================

def _labor_fact_qs(request, **filters):
    """Базовый queryset для фактов по людям с фильтрами."""
    qs = LaborFact.objects.filter(company=request.user.company)
    if filters.get('brigade_id'):
        qs = qs.filter(brigade_id=filters['brigade_id'])
    if filters.get('project_work_id') is not None:
        qs = qs.filter(project_work_id=filters['project_work_id'])
    if filters.get('date'):
        qs = qs.filter(date=filters['date'])
    if filters.get('date_from'):
        qs = qs.filter(date__gte=filters['date_from'])
    if filters.get('date_to'):
        qs = qs.filter(date__lte=filters['date_to'])
    return qs


_LABOR_FACT_FIELDS = [
    ('actual_workers', lambda v: int(v) if v else None),
    ('actual_hours', lambda v: float(v) if v else None),
    ('hourly_rate', lambda v: float(v) if v else None),
]


class LaborFactEditByDateView(View):
    template_name = 'production/labor_fact_batch_edit.html'

    def get(self, request, date_str):
        from apps.production.views.base import parse_date_safe
        target_date = parse_date_safe(date_str)
        if not target_date:
            messages.error(request, 'Некорректная дата')
            return redirect('production:labor_fact_list')

        brigade_id = request.GET.get('brigade_id')
        project_work_id = parse_fk_id(request.GET.get('project_work_id'))

        facts = _labor_fact_qs(
            request, brigade_id=brigade_id,
            project_work_id=project_work_id, date=target_date
        ).select_related('project', 'project_work', 'brigade')

        return render(request, self.template_name, {
            'target_date': target_date, 'facts': facts,
            'brigade_id': brigade_id, 'project_work_id': project_work_id or '',
        })

    def post(self, request, date_str):
        from apps.production.views.base import parse_date_safe
        target_date = parse_date_safe(date_str)
        if not target_date:
            messages.error(request, 'Некорректная дата')
            return redirect('production:labor_fact_list')

        brigade_id = request.POST.get('brigade_id')
        project_work_id = parse_fk_id(request.POST.get('project_work_id'))

        qs = _labor_fact_qs(
            request, brigade_id=brigade_id,
            project_work_id=project_work_id, date=target_date
        )
        update_data = build_update_data(request, _LABOR_FACT_FIELDS)

        if update_data:
            count = qs.update(**update_data)
            messages.success(request, f'Обновлено фактов: {count} на дату {target_date.strftime("%d.%m.%Y")}')
        else:
            messages.warning(request, 'Не указаны значения для обновления')
        return redirect('production:labor_fact_list')


class LaborFactEditRangeView(View):
    template_name = 'production/labor_fact_range_edit.html'

    def get(self, request):
        brigade_id = request.GET.get('brigade_id')
        if not brigade_id:
            messages.error(request, 'Не указана бригада')
            return redirect('production:labor_fact_list')

        project_work_id = parse_fk_id(request.GET.get('project_work_id'))
        date_from = request.GET.get('date_from')
        date_to = request.GET.get('date_to')

        facts = _labor_fact_qs(
            request, brigade_id=brigade_id,
            project_work_id=project_work_id,
            date_from=date_from, date_to=date_to
        ).select_related('project', 'project_work', 'brigade').order_by('date')

        return render(request, self.template_name, {
            'facts': facts, 'brigade_id': brigade_id,
            'project_work_id': project_work_id or '',
            'date_from': date_from or '', 'date_to': date_to or '',
            'facts_count': facts.count(),
        })

    def post(self, request):
        brigade_id = request.POST.get('brigade_id')
        if not brigade_id:
            messages.error(request, 'Не указана бригада')
            return redirect('production:labor_fact_list')

        project_work_id = parse_fk_id(request.POST.get('project_work_id'))
        date_from = request.POST.get('date_from')
        date_to = request.POST.get('date_to')

        qs = _labor_fact_qs(
            request, brigade_id=brigade_id,
            project_work_id=project_work_id,
            date_from=date_from, date_to=date_to
        )
        update_data = build_update_data(request, _LABOR_FACT_FIELDS)

        if update_data:
            count = qs.update(**update_data)
            messages.success(request, f'Обновлено фактов: {count}')
        else:
            messages.warning(request, 'Не указаны значения для обновления')
        return redirect('production:labor_fact_list')


class LaborFactDeleteByDateView(View):
    def post(self, request, date_str):
        from apps.production.views.base import parse_date_safe
        target_date = parse_date_safe(date_str)
        if not target_date:
            messages.error(request, 'Некорректная дата')
            return redirect('production:labor_fact_list')

        brigade_id = request.POST.get('brigade_id')
        project_work_id = parse_fk_id(request.POST.get('project_work_id'))

        qs = _labor_fact_qs(
            request, brigade_id=brigade_id,
            project_work_id=project_work_id, date=target_date
        )
        count = qs.count()
        qs.delete()
        messages.success(request, f'Удалено фактов: {count} на дату {target_date.strftime("%d.%m.%Y")}')
        return redirect('production:labor_fact_list')


class LaborFactDeleteByBrigadeView(View):
    def post(self, request, brigade_id):
        project_work_id = parse_fk_id(request.POST.get('project_work_id'))
        qs = _labor_fact_qs(request, brigade_id=brigade_id, project_work_id=project_work_id)
        count = qs.count()
        qs.delete()
        messages.success(request, f'Удалено всех фактов для бригады: {count} записей')
        return redirect('production:labor_fact_list')