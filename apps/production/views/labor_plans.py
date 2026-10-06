"""
Views для планов по людям.
"""
from django.shortcuts import render, redirect
from django.contrib import messages
from django.views.generic import ListView, CreateView, UpdateView, DeleteView, View
from django.urls import reverse_lazy
from django.http import JsonResponse
from django.views.decorators.http import require_POST
from django.views.decorators.csrf import csrf_protect
from datetime import timedelta

from core.mixins import CompanyRequiredMixin, CompanyScopedMixin
from apps.production.models import LaborPlan
from apps.production.forms import LaborPlanForm, LaborPlanRangeForm
from apps.production.views.base import parse_fk_id, build_update_data, parse_date_safe
from apps.projects.models import Project
from apps.works.models import ProjectWork
from apps.resources.models import Brigade


class LaborPlanListView(CompanyScopedMixin, ListView):
    model = LaborPlan
    template_name = 'production/labor_plan_list.html'
    context_object_name = 'labor_plans'
    paginate_by = None

    def get_queryset(self):
        qs = LaborPlan.objects.filter(company=self.request.user.company) \
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
        plans = context['labor_plans']

        rows, dates_set = {}, set()
        for plan in plans:
            row_key = f"{plan.brigade_id}_{plan.project_work_id or 0}"
            if row_key not in rows:
                rows[row_key] = {
                    'brigade': plan.brigade, 'project_work': plan.project_work,
                    'project': plan.project, 'plans_by_date': {}
                }
            rows[row_key]['plans_by_date'][plan.date.isoformat()] = plan
            dates_set.add(plan.date)

        sorted_dates = sorted(dates_set)
        sorted_rows = sorted(
            rows.values(),
            key=lambda x: (x['brigade'].name, x['project_work'].name if x['project_work'] else '')
        )

        context.update({
            'matrix_rows': sorted_rows, 'matrix_dates': sorted_dates,
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


def _setup_labor_plan_form(form, company):
    form.fields['project'].queryset = Project.objects.filter(company=company)
    form.fields['project_work'].queryset = ProjectWork.objects.filter(company=company)
    form.fields['brigade'].queryset = Brigade.objects.filter(company=company, is_active=True)


class LaborPlanCreateView(CompanyRequiredMixin, CreateView):
    model = LaborPlan
    form_class = LaborPlanForm
    template_name = 'production/resource_plan_form.html'

    def get_form(self, form_class=None):
        form = super().get_form(form_class)
        _setup_labor_plan_form(form, self.request.user.company)
        return form

    def form_valid(self, form):
        form.instance.company = self.request.user.company
        messages.success(self.request, 'План по людям сохранён!')
        return super().form_valid(form)

    def get_success_url(self):
        return reverse_lazy('production:labor_plan_list')


class LaborPlanRangeCreateView(CompanyRequiredMixin, View):
    template_name = 'production/labor_plan_range_form.html'

    def get(self, request):
        return render(request, self.template_name, {'form': LaborPlanRangeForm(user=request.user)})

    def post(self, request):
        form = LaborPlanRangeForm(request.POST, user=request.user)
        if form.is_valid():
            project = form.cleaned_data.get('project')
            project_work = form.cleaned_data.get('project_work')
            brigade = form.cleaned_data['brigade']
            date_start = form.cleaned_data['date_start']
            date_end = form.cleaned_data['date_end']
            defaults = {
                'planned_workers': form.cleaned_data['planned_workers'],
                'planned_hours': form.cleaned_data.get('planned_hours'),
                'hourly_rate': form.cleaned_data.get('hourly_rate'),
                'comment': form.cleaned_data.get('comment', ''),
                'company': request.user.company,
            }
            current_date, count = date_start, 0
            while current_date <= date_end:
                LaborPlan.objects.update_or_create(
                    company=self.request.user.company,
                    project=project, project_work=project_work,
                    date=current_date, brigade=brigade, defaults=defaults
                )
                current_date += timedelta(days=1)
                count += 1
            work_name = project_work.name if project_work else 'без привязки к работе'
            messages.success(request, f'Создано планов: {count} для "{work_name}" (с {date_start.strftime("%d.%m.%Y")} по {date_end.strftime("%d.%m.%Y")})')
            return redirect('production:labor_plan_list')
        return render(request, self.template_name, {'form': form})


class LaborPlanUpdateView(CompanyScopedMixin, UpdateView):
    model = LaborPlan
    form_class = LaborPlanForm
    template_name = 'production/resource_plan_form.html'

    def get_form(self, form_class=None):
        form = super().get_form(form_class)
        _setup_labor_plan_form(form, self.request.user.company)
        return form

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['title'] = 'Редактирование плана по людям'
        return context

    def form_valid(self, form):
        messages.success(self.request, 'План по людям обновлён!')
        return super().form_valid(form)

    def get_success_url(self):
        return reverse_lazy('production:labor_plan_list')


class LaborPlanDeleteView(CompanyScopedMixin, DeleteView):
    model = LaborPlan
    template_name = 'production/resource_confirm_delete.html'
    success_url = reverse_lazy('production:labor_plan_list')

    def delete(self, request, *args, **kwargs):
        plan = self.get_object()
        messages.success(request, f'План по людям за {plan.date} удалён')
        return super().delete(request, *args, **kwargs)


@csrf_protect
@require_POST
def labor_plan_inline_update(request):
    import json
    try:
        data = json.loads(request.body)
        plan = LaborPlan.objects.get(pk=data['plan_id'], company=request.user.company)
        field_map = {
            'planned_workers': lambda v: int(v) if v else 0,
            'planned_hours': lambda v: float(v) if v else None,
            'hourly_rate': lambda v: float(v) if v else None,
        }
        field, value = data.get('field'), data.get('value')
        if field == 'comment':
            plan.comment = value
        elif field in field_map:
            setattr(plan, field, field_map[field](value))
        else:
            return JsonResponse({'success': False, 'message': f'Неизвестное поле: {field}'}, status=400)
        plan.save()
        return JsonResponse({'success': True})
    except LaborPlan.DoesNotExist:
        return JsonResponse({'success': False, 'message': 'Не найдено'}, status=404)
    except Exception as e:
        return JsonResponse({'success': False, 'message': str(e)}, status=400)


def _labor_plan_qs(request, **filters):
    qs = LaborPlan.objects.filter(company=request.user.company)
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


_LABOR_PLAN_FIELDS = [
    ('planned_workers', lambda v: int(v) if v else None),
    ('planned_hours', lambda v: float(v) if v else None),
    ('hourly_rate', lambda v: float(v) if v else None),
]


class LaborPlanEditByDateView(View):
    template_name = 'production/labor_plan_batch_edit.html'

    def get(self, request, date_str):
        target_date = parse_date_safe(date_str)
        if not target_date:
            messages.error(request, 'Некорректная дата')
            return redirect('production:labor_plan_list')

        brigade_id = request.GET.get('brigade_id')
        project_work_id = parse_fk_id(request.GET.get('project_work_id'))
        plans = _labor_plan_qs(
            request, brigade_id=brigade_id,
            project_work_id=project_work_id, date=target_date
        ).select_related('project', 'project_work', 'brigade')

        return render(request, self.template_name, {
            'target_date': target_date, 'plans': plans,
            'brigade_id': brigade_id, 'project_work_id': project_work_id or '',
        })

    def post(self, request, date_str):
        target_date = parse_date_safe(date_str)
        if not target_date:
            messages.error(request, 'Некорректная дата')
            return redirect('production:labor_plan_list')

        brigade_id = request.POST.get('brigade_id')
        project_work_id = parse_fk_id(request.POST.get('project_work_id'))
        qs = _labor_plan_qs(
            request, brigade_id=brigade_id,
            project_work_id=project_work_id, date=target_date
        )
        update_data = build_update_data(request, _LABOR_PLAN_FIELDS)
        if update_data:
            count = qs.update(**update_data)
            messages.success(request, f'Обновлено планов: {count} на дату {target_date.strftime("%d.%m.%Y")}')
        else:
            messages.warning(request, 'Не указаны значения для обновления')
        return redirect('production:labor_plan_list')


class LaborPlanEditRangeView(View):
    template_name = 'production/labor_plan_range_edit.html'

    def get(self, request):
        brigade_id = request.GET.get('brigade_id')
        if not brigade_id:
            messages.error(request, 'Не указана бригада')
            return redirect('production:labor_plan_list')

        project_work_id = parse_fk_id(request.GET.get('project_work_id'))
        date_from = request.GET.get('date_from')
        date_to = request.GET.get('date_to')

        plans = _labor_plan_qs(
            request, brigade_id=brigade_id,
            project_work_id=project_work_id,
            date_from=date_from, date_to=date_to
        ).select_related('project', 'project_work', 'brigade').order_by('date')

        return render(request, self.template_name, {
            'plans': plans, 'brigade_id': brigade_id,
            'project_work_id': project_work_id or '',
            'date_from': date_from or '', 'date_to': date_to or '',
            'plans_count': plans.count(),
        })

    def post(self, request):
        brigade_id = request.POST.get('brigade_id')
        if not brigade_id:
            messages.error(request, 'Не указана бригада')
            return redirect('production:labor_plan_list')

        project_work_id = parse_fk_id(request.POST.get('project_work_id'))
        date_from = request.POST.get('date_from')
        date_to = request.POST.get('date_to')

        qs = _labor_plan_qs(
            request, brigade_id=brigade_id,
            project_work_id=project_work_id,
            date_from=date_from, date_to=date_to
        )
        update_data = build_update_data(request, _LABOR_PLAN_FIELDS)
        if update_data:
            count = qs.update(**update_data)
            messages.success(request, f'Обновлено планов: {count}')
        else:
            messages.warning(request, 'Не указаны значения для обновления')
        return redirect('production:labor_plan_list')


class LaborPlanDeleteByDateView(View):
    def post(self, request, date_str):
        target_date = parse_date_safe(date_str)
        if not target_date:
            messages.error(request, 'Некорректная дата')
            return redirect('production:labor_plan_list')

        brigade_id = request.POST.get('brigade_id')
        project_work_id = parse_fk_id(request.POST.get('project_work_id'))
        qs = _labor_plan_qs(
            request, brigade_id=brigade_id,
            project_work_id=project_work_id, date=target_date
        )
        count = qs.count()
        qs.delete()
        messages.success(request, f'Удалено планов: {count} на дату {target_date.strftime("%d.%m.%Y")}')
        return redirect('production:labor_plan_list')


class LaborPlanDeleteByBrigadeView(View):
    def post(self, request, brigade_id):
        project_work_id = parse_fk_id(request.POST.get('project_work_id'))
        qs = _labor_plan_qs(request, brigade_id=brigade_id, project_work_id=project_work_id)
        count = qs.count()
        qs.delete()
        messages.success(request, f'Удалено всех планов для бригады: {count} записей')
        return redirect('production:labor_plan_list')