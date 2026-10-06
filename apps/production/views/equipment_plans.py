"""
Views для планов по технике.
"""
from django.shortcuts import render, redirect
from django.contrib import messages
from django.views.generic import ListView, CreateView, UpdateView, DeleteView, View
from django.urls import reverse_lazy
from django.http import JsonResponse
from django.views.decorators.http import require_POST
from django.views.decorators.csrf import csrf_protect
from datetime import timedelta

from core.mixins import CompanyRequiredMixin
from apps.production.models import EquipmentPlan
from apps.production.forms import EquipmentPlanForm, EquipmentPlanRangeForm
from apps.production.views.base import build_update_data, parse_date_safe
from apps.projects.models import Project
from apps.works.models import ProjectWork
from apps.resources.models import EquipmentType


class EquipmentPlanListView(ListView):
    model = EquipmentPlan
    template_name = 'production/equipment_plan_list.html'
    context_object_name = 'equipment_plans'
    paginate_by = None

    def get_queryset(self):
        qs = EquipmentPlan.objects.filter(company=self.request.user.company) \
            .select_related('project', 'project_work', 'equipment_type')

        project_id = self.request.GET.get('project')
        if project_id:
            qs = qs.filter(project_id=project_id)
        work_id = self.request.GET.get('project_work')
        if work_id:
            qs = qs.filter(project_work_id=work_id)
        et_ids = self.request.GET.getlist('equipment_type')
        if et_ids:
            qs = qs.filter(equipment_type_id__in=et_ids)
        date_from = self.request.GET.get('date_from')
        date_to = self.request.GET.get('date_to')
        if date_from:
            qs = qs.filter(date__gte=date_from)
        if date_to:
            qs = qs.filter(date__lte=date_to)

        return qs.order_by('equipment_type__name', 'equipment_number', 'date')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        plans = context['equipment_plans']

        rows, dates_set = {}, set()
        for plan in plans:
            row_key = f"{plan.equipment_type_id}_{plan.equipment_number or ''}"
            if row_key not in rows:
                rows[row_key] = {
                    'equipment_type': plan.equipment_type,
                    'equipment_number': plan.equipment_number,
                    'project': plan.project, 'project_work': plan.project_work,
                    'plans_by_date': {}
                }
            rows[row_key]['plans_by_date'][plan.date.isoformat()] = plan
            dates_set.add(plan.date)

        sorted_dates = sorted(dates_set)
        sorted_rows = sorted(rows.values(), key=lambda x: (x['equipment_type'].name, x['equipment_number'] or ''))

        context.update({
            'matrix_rows': sorted_rows, 'matrix_dates': sorted_dates,
            'date_from': self.request.GET.get('date_from', ''),
            'date_to': self.request.GET.get('date_to', ''),
            'selected_project': self.request.GET.get('project', ''),
            'selected_work': self.request.GET.get('project_work', ''),
            'selected_equipment_types': self.request.GET.getlist('equipment_type'),
            'projects': Project.objects.filter(company=self.request.user.company).order_by('name'),
            'works': ProjectWork.objects.filter(company=self.request.user.company).order_by('name'),
            'equipment_types': EquipmentType.objects.filter(company=self.request.user.company, is_active=True).order_by('name'),
        })
        return context


def _setup_equipment_plan_form(form, company):
    form.fields['project'].queryset = Project.objects.filter(company=company)
    form.fields['project_work'].queryset = ProjectWork.objects.filter(company=company)
    form.fields['equipment_type'].queryset = EquipmentType.objects.filter(company=company, is_active=True)


class EquipmentPlanCreateView(CompanyRequiredMixin, CreateView):
    model = EquipmentPlan
    form_class = EquipmentPlanForm
    template_name = 'production/resource_plan_form.html'

    def get_form(self, form_class=None):
        form = super().get_form(form_class)
        _setup_equipment_plan_form(form, self.request.user.company)
        return form

    def form_valid(self, form):
        form.instance.company = self.request.user.company
        messages.success(self.request, 'План по технике сохранён!')
        return super().form_valid(form)

    def get_success_url(self):
        return reverse_lazy('production:equipment_plan_list')


class EquipmentPlanRangeCreateView(CompanyRequiredMixin, View):
    template_name = 'production/equipment_plan_range_form.html'

    def get(self, request):
        return render(request, self.template_name, {'form': EquipmentPlanRangeForm(user=request.user)})

    def post(self, request):
        form = EquipmentPlanRangeForm(request.POST, user=request.user)
        if form.is_valid():
            project = form.cleaned_data.get('project')
            project_work = form.cleaned_data.get('project_work')
            equipment_type = form.cleaned_data['equipment_type']
            equipment_number = form.cleaned_data.get('equipment_number', '')
            date_start = form.cleaned_data['date_start']
            date_end = form.cleaned_data['date_end']
            defaults = {
                'planned_count': form.cleaned_data['planned_count'],
                'planned_machine_hours': form.cleaned_data.get('planned_machine_hours'),
                'hourly_rate': form.cleaned_data.get('hourly_rate'),
                'comment': form.cleaned_data.get('comment', ''),
                'company': request.user.company,
            }
            current_date, count = date_start, 0
            while current_date <= date_end:
                EquipmentPlan.objects.update_or_create(
                    project=project, project_work=project_work,
                    date=current_date, equipment_type=equipment_type,
                    equipment_number=equipment_number, defaults=defaults
                )
                current_date += timedelta(days=1)
                count += 1
            work_name = project_work.name if project_work else 'без привязки к работе'
            messages.success(request, f'Создано планов: {count} для "{equipment_type.name}" ({work_name}) с {date_start.strftime("%d.%m.%Y")} по {date_end.strftime("%d.%m.%Y")}')
            return redirect('production:equipment_plan_list')
        return render(request, self.template_name, {'form': form})


class EquipmentPlanUpdateView(UpdateView):
    model = EquipmentPlan
    form_class = EquipmentPlanForm
    template_name = 'production/resource_plan_form.html'

    def get_form(self, form_class=None):
        form = super().get_form(form_class)
        _setup_equipment_plan_form(form, self.request.user.company)
        return form

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['title'] = 'Редактирование плана по технике'
        return context

    def form_valid(self, form):
        messages.success(self.request, 'План по технике обновлён!')
        return super().form_valid(form)

    def get_success_url(self):
        return reverse_lazy('production:equipment_plan_list')


class EquipmentPlanDeleteView(DeleteView):
    model = EquipmentPlan
    template_name = 'production/resource_confirm_delete.html'
    success_url = reverse_lazy('production:equipment_plan_list')

    def delete(self, request, *args, **kwargs):
        plan = self.get_object()
        messages.success(request, f'План по технике "{plan.equipment_type}" за {plan.date} удалён')
        return super().delete(request, *args, **kwargs)


@csrf_protect
@require_POST
def equipment_plan_inline_update(request):
    import json
    try:
        data = json.loads(request.body)
        plan = EquipmentPlan.objects.get(pk=data['plan_id'], company=request.user.company)
        field_map = {
            'planned_count': lambda v: int(v) if v else 0,
            'planned_machine_hours': lambda v: float(v) if v else None,
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
    except EquipmentPlan.DoesNotExist:
        return JsonResponse({'success': False, 'message': 'Не найдено'}, status=404)
    except Exception as e:
        return JsonResponse({'success': False, 'message': str(e)}, status=400)


def _equipment_plan_qs(request, **filters):
    qs = EquipmentPlan.objects.filter(company=request.user.company)
    if filters.get('equipment_type_id'):
        qs = qs.filter(equipment_type_id=filters['equipment_type_id'])
    if filters.get('equipment_number') is not None:
        qs = qs.filter(equipment_number=filters['equipment_number'])
    if filters.get('date'):
        qs = qs.filter(date=filters['date'])
    if filters.get('date_from'):
        qs = qs.filter(date__gte=filters['date_from'])
    if filters.get('date_to'):
        qs = qs.filter(date__lte=filters['date_to'])
    return qs


_EQUIPMENT_PLAN_FIELDS = [
    ('planned_count', lambda v: int(v) if v else None),
    ('planned_machine_hours', lambda v: float(v) if v else None),
    ('hourly_rate', lambda v: float(v) if v else None),
]


class EquipmentPlanEditByDateView(View):
    template_name = 'production/equipment_plan_batch_edit.html'

    def get(self, request, date_str):
        target_date = parse_date_safe(date_str)
        if not target_date:
            messages.error(request, 'Некорректная дата')
            return redirect('production:equipment_plan_list')

        et_id = request.GET.get('equipment_type_id')
        eq_num = request.GET.get('equipment_number', '')
        plans = _equipment_plan_qs(
            request, equipment_type_id=et_id,
            equipment_number=eq_num if eq_num else None, date=target_date
        ).select_related('project', 'project_work', 'equipment_type')

        return render(request, self.template_name, {
            'target_date': target_date, 'plans': plans,
            'equipment_type_id': et_id, 'equipment_number': eq_num,
        })

    def post(self, request, date_str):
        target_date = parse_date_safe(date_str)
        if not target_date:
            messages.error(request, 'Некорректная дата')
            return redirect('production:equipment_plan_list')

        et_id = request.POST.get('equipment_type_id')
        eq_num = request.POST.get('equipment_number', '')
        qs = _equipment_plan_qs(
            request, equipment_type_id=et_id,
            equipment_number=eq_num if eq_num else None, date=target_date
        )
        update_data = build_update_data(request, _EQUIPMENT_PLAN_FIELDS)
        if update_data:
            count = qs.update(**update_data)
            messages.success(request, f'Обновлено планов: {count} на дату {target_date.strftime("%d.%m.%Y")}')
        else:
            messages.warning(request, 'Не указаны значения для обновления')
        return redirect('production:equipment_plan_list')


class EquipmentPlanEditRangeView(View):
    template_name = 'production/equipment_plan_range_edit.html'

    def get(self, request):
        et_id = request.GET.get('equipment_type_id')
        if not et_id:
            messages.error(request, 'Не указан вид техники')
            return redirect('production:equipment_plan_list')

        eq_num = request.GET.get('equipment_number', '')
        date_from = request.GET.get('date_from')
        date_to = request.GET.get('date_to')

        plans = _equipment_plan_qs(
            request, equipment_type_id=et_id,
            equipment_number=eq_num if eq_num else None,
            date_from=date_from, date_to=date_to
        ).select_related('project', 'project_work', 'equipment_type').order_by('date')

        return render(request, self.template_name, {
            'plans': plans, 'equipment_type_id': et_id,
            'equipment_number': eq_num,
            'date_from': date_from or '', 'date_to': date_to or '',
            'plans_count': plans.count(),
        })

    def post(self, request):
        et_id = request.POST.get('equipment_type_id')
        if not et_id:
            messages.error(request, 'Не указан вид техники')
            return redirect('production:equipment_plan_list')

        eq_num = request.POST.get('equipment_number', '')
        date_from = request.POST.get('date_from')
        date_to = request.POST.get('date_to')

        qs = _equipment_plan_qs(
            request, equipment_type_id=et_id,
            equipment_number=eq_num if eq_num else None,
            date_from=date_from, date_to=date_to
        )
        update_data = build_update_data(request, _EQUIPMENT_PLAN_FIELDS)
        if update_data:
            count = qs.update(**update_data)
            messages.success(request, f'Обновлено планов: {count}')
        else:
            messages.warning(request, 'Не указаны значения для обновления')
        return redirect('production:equipment_plan_list')


class EquipmentPlanDeleteByDateView(View):
    def post(self, request, date_str):
        target_date = parse_date_safe(date_str)
        if not target_date:
            messages.error(request, 'Некорректная дата')
            return redirect('production:equipment_plan_list')

        et_id = request.POST.get('equipment_type_id')
        eq_num = request.POST.get('equipment_number', '')
        qs = _equipment_plan_qs(
            request, equipment_type_id=et_id,
            equipment_number=eq_num if eq_num else None, date=target_date
        )
        count = qs.count()
        qs.delete()
        messages.success(request, f'Удалено планов: {count} на дату {target_date.strftime("%d.%m.%Y")}')
        return redirect('production:equipment_plan_list')


class EquipmentPlanDeleteByTypeView(View):
    def post(self, request, equipment_type_id):
        eq_num = request.POST.get('equipment_number', '')
        qs = _equipment_plan_qs(
            request, equipment_type_id=equipment_type_id,
            equipment_number=eq_num if eq_num else None
        )
        count = qs.count()
        qs.delete()
        messages.success(request, f'Удалено всех планов: {count} записей')
        return redirect('production:equipment_plan_list')