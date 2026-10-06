
"""
Views для планов по ГСМ.
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
from apps.production.models import FuelPlan
from apps.production.forms import FuelPlanForm, FuelPlanRangeForm
from apps.production.views.base import build_update_data, parse_date_safe
from apps.projects.models import Project
from apps.works.models import ProjectWork


class FuelPlanListView(ListView):
    model = FuelPlan
    template_name = 'production/fuel_plan_list.html'
    context_object_name = 'fuel_plans'
    paginate_by = None

    def get_queryset(self):
        qs = FuelPlan.objects.filter(company=self.request.user.company) \
            .select_related('project', 'project_work')

        project_id = self.request.GET.get('project')
        if project_id:
            qs = qs.filter(project_id=project_id)
        work_id = self.request.GET.get('project_work')
        if work_id:
            qs = qs.filter(project_work_id=work_id)
        fuel_types = self.request.GET.getlist('fuel_type')
        if fuel_types:
            qs = qs.filter(fuel_type__in=fuel_types)
        date_from = self.request.GET.get('date_from')
        date_to = self.request.GET.get('date_to')
        if date_from:
            qs = qs.filter(date__gte=date_from)
        if date_to:
            qs = qs.filter(date__lte=date_to)

        return qs.order_by('fuel_type', 'equipment_ref', 'date')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        plans = context['fuel_plans']

        rows, dates_set = {}, set()
        for plan in plans:
            row_key = f"{plan.fuel_type}_{plan.equipment_ref or ''}"
            if row_key not in rows:
                rows[row_key] = {
                    'fuel_type': plan.fuel_type,
                    'fuel_type_display': plan.get_fuel_type_display(),
                    'equipment_ref': plan.equipment_ref,
                    'project': plan.project, 'project_work': plan.project_work,
                    'plans_by_date': {}
                }
            rows[row_key]['plans_by_date'][plan.date.isoformat()] = plan
            dates_set.add(plan.date)

        sorted_dates = sorted(dates_set)
        sorted_rows = sorted(rows.values(), key=lambda x: (x['fuel_type_display'], x['equipment_ref'] or ''))

        context.update({
            'matrix_rows': sorted_rows, 'matrix_dates': sorted_dates,
            'date_from': self.request.GET.get('date_from', ''),
            'date_to': self.request.GET.get('date_to', ''),
            'selected_project': self.request.GET.get('project', ''),
            'selected_work': self.request.GET.get('project_work', ''),
            'selected_fuel_types': self.request.GET.getlist('fuel_type'),
            'projects': Project.objects.filter(company=self.request.user.company).order_by('name'),
            'works': ProjectWork.objects.filter(company=self.request.user.company).order_by('name'),
            'fuel_type_choices': FuelPlan.FUEL_TYPE_CHOICES,
        })
        return context


def _setup_fuel_plan_form(form, company):
    form.fields['project'].queryset = Project.objects.filter(company=company)
    form.fields['project_work'].queryset = ProjectWork.objects.filter(company=company)


class FuelPlanCreateView(CompanyRequiredMixin, CreateView):
    model = FuelPlan
    form_class = FuelPlanForm
    template_name = 'production/resource_plan_form.html'

    def get_form(self, form_class=None):
        form = super().get_form(form_class)
        _setup_fuel_plan_form(form, self.request.user.company)
        return form

    def form_valid(self, form):
        form.instance.company = self.request.user.company
        messages.success(self.request, 'План по ГСМ сохранён!')
        return super().form_valid(form)

    def get_success_url(self):
        return reverse_lazy('production:fuel_plan_list')


class FuelPlanRangeCreateView(CompanyRequiredMixin, View):
    template_name = 'production/fuel_plan_range_form.html'

    def get(self, request):
        return render(request, self.template_name, {'form': FuelPlanRangeForm(user=request.user)})

    def post(self, request):
        form = FuelPlanRangeForm(request.POST, user=request.user)
        if form.is_valid():
            project = form.cleaned_data.get('project')
            project_work = form.cleaned_data.get('project_work')
            fuel_type = form.cleaned_data['fuel_type']
            date_start = form.cleaned_data['date_start']
            date_end = form.cleaned_data['date_end']
            defaults = {
                'planned_liters': form.cleaned_data['planned_liters'],
                'price_per_liter': form.cleaned_data.get('price_per_liter'),
                'equipment_ref': form.cleaned_data.get('equipment_ref', ''),
                'comment': form.cleaned_data.get('comment', ''),
                'company': request.user.company,
            }
            current_date, count = date_start, 0
            while current_date <= date_end:
                FuelPlan.objects.update_or_create(
                    project=project, project_work=project_work,
                    date=current_date, fuel_type=fuel_type,
                    equipment_ref=defaults['equipment_ref'], defaults=defaults
                )
                current_date += timedelta(days=1)
                count += 1
            fuel_display = dict(FuelPlan.FUEL_TYPE_CHOICES).get(fuel_type, fuel_type)
            work_name = project_work.name if project_work else 'без привязки к работе'
            messages.success(request, f'Создано планов: {count} для "{fuel_display}" ({work_name}) с {date_start.strftime("%d.%m.%Y")} по {date_end.strftime("%d.%m.%Y")}')
            return redirect('production:fuel_plan_list')
        return render(request, self.template_name, {'form': form})


class FuelPlanUpdateView(UpdateView):
    model = FuelPlan
    form_class = FuelPlanForm
    template_name = 'production/resource_plan_form.html'

    def get_form(self, form_class=None):
        form = super().get_form(form_class)
        _setup_fuel_plan_form(form, self.request.user.company)
        return form

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['title'] = 'Редактирование плана по ГСМ'
        return context

    def form_valid(self, form):
        messages.success(self.request, 'План по ГСМ обновлён!')
        return super().form_valid(form)

    def get_success_url(self):
        return reverse_lazy('production:fuel_plan_list')


class FuelPlanDeleteView(DeleteView):
    model = FuelPlan
    template_name = 'production/resource_confirm_delete.html'
    success_url = reverse_lazy('production:fuel_plan_list')

    def delete(self, request, *args, **kwargs):
        plan = self.get_object()
        messages.success(request, f'План по ГСМ "{plan.get_fuel_type_display()}" за {plan.date} удалён')
        return super().delete(request, *args, **kwargs)


@csrf_protect
@require_POST
def fuel_plan_inline_update(request):
    import json
    try:
        data = json.loads(request.body)
        plan = FuelPlan.objects.get(pk=data['plan_id'], company=request.user.company)
        field_map = {
            'planned_liters': lambda v: float(v) if v else 0,
            'price_per_liter': lambda v: float(v) if v else None,
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
    except FuelPlan.DoesNotExist:
        return JsonResponse({'success': False, 'message': 'Не найдено'}, status=404)
    except Exception as e:
        return JsonResponse({'success': False, 'message': str(e)}, status=400)


def _fuel_plan_qs(request, **filters):
    qs = FuelPlan.objects.filter(company=request.user.company)
    if filters.get('fuel_type'):
        qs = qs.filter(fuel_type=filters['fuel_type'])
    if filters.get('equipment_ref') is not None:
        qs = qs.filter(equipment_ref=filters['equipment_ref'])
    if filters.get('date'):
        qs = qs.filter(date=filters['date'])
    if filters.get('date_from'):
        qs = qs.filter(date__gte=filters['date_from'])
    if filters.get('date_to'):
        qs = qs.filter(date__lte=filters['date_to'])
    return qs


_FUEL_PLAN_FIELDS = [
    ('planned_liters', lambda v: float(v) if v else None),
    ('price_per_liter', lambda v: float(v) if v else None),
]


class FuelPlanEditByDateView(View):
    template_name = 'production/fuel_plan_batch_edit.html'

    def get(self, request, date_str):
        target_date = parse_date_safe(date_str)
        if not target_date:
            messages.error(request, 'Некорректная дата')
            return redirect('production:fuel_plan_list')

        fuel_type = request.GET.get('fuel_type')
        eq_ref = request.GET.get('equipment_ref', '')
        plans = _fuel_plan_qs(
            request, fuel_type=fuel_type,
            equipment_ref=eq_ref if eq_ref else None, date=target_date
        ).select_related('project', 'project_work')

        return render(request, self.template_name, {
            'target_date': target_date, 'plans': plans,
            'fuel_type': fuel_type, 'equipment_ref': eq_ref,
        })

    def post(self, request, date_str):
        target_date = parse_date_safe(date_str)
        if not target_date:
            messages.error(request, 'Некорректная дата')
            return redirect('production:fuel_plan_list')

        fuel_type = request.POST.get('fuel_type')
        eq_ref = request.POST.get('equipment_ref', '')
        qs = _fuel_plan_qs(
            request, fuel_type=fuel_type,
            equipment_ref=eq_ref if eq_ref else None, date=target_date
        )
        update_data = build_update_data(request, _FUEL_PLAN_FIELDS)
        if update_data:
            count = qs.update(**update_data)
            messages.success(request, f'Обновлено планов: {count} на дату {target_date.strftime("%d.%m.%Y")}')
        else:
            messages.warning(request, 'Не указаны значения для обновления')
        return redirect('production:fuel_plan_list')


class FuelPlanEditRangeView(View):
    template_name = 'production/fuel_plan_range_edit.html'

    def get(self, request):
        fuel_type = request.GET.get('fuel_type')
        if not fuel_type:
            messages.error(request, 'Не указан вид ГСМ')
            return redirect('production:fuel_plan_list')

        eq_ref = request.GET.get('equipment_ref', '')
        date_from = request.GET.get('date_from')
        date_to = request.GET.get('date_to')

        plans = _fuel_plan_qs(
            request, fuel_type=fuel_type,
            equipment_ref=eq_ref if eq_ref else None,
            date_from=date_from, date_to=date_to
        ).select_related('project', 'project_work').order_by('date')

        return render(request, self.template_name, {
            'plans': plans, 'fuel_type': fuel_type,
            'equipment_ref': eq_ref,
            'date_from': date_from or '', 'date_to': date_to or '',
            'plans_count': plans.count(),
        })

    def post(self, request):
        fuel_type = request.POST.get('fuel_type')
        if not fuel_type:
            messages.error(request, 'Не указан вид ГСМ')
            return redirect('production:fuel_plan_list')

        eq_ref = request.POST.get('equipment_ref', '')
        date_from = request.POST.get('date_from')
        date_to = request.POST.get('date_to')

        qs = _fuel_plan_qs(
            request, fuel_type=fuel_type,
            equipment_ref=eq_ref if eq_ref else None,
            date_from=date_from, date_to=date_to
        )
        update_data = build_update_data(request, _FUEL_PLAN_FIELDS)
        if update_data:
            count = qs.update(**update_data)
            messages.success(request, f'Обновлено планов: {count}')
        else:
            messages.warning(request, 'Не указаны значения для обновления')
        return redirect('production:fuel_plan_list')


class FuelPlanDeleteByDateView(View):
    def post(self, request, date_str):
        target_date = parse_date_safe(date_str)
        if not target_date:
            messages.error(request, 'Некорректная дата')
            return redirect('production:fuel_plan_list')

        fuel_type = request.POST.get('fuel_type')
        eq_ref = request.POST.get('equipment_ref', '')
        qs = _fuel_plan_qs(
            request, fuel_type=fuel_type,
            equipment_ref=eq_ref if eq_ref else None, date=target_date
        )
        count = qs.count()
        qs.delete()
        messages.success(request, f'Удалено планов: {count} на дату {target_date.strftime("%d.%m.%Y")}')
        return redirect('production:fuel_plan_list')


class FuelPlanDeleteByTypeView(View):
    def post(self, request, fuel_type):
        eq_ref = request.POST.get('equipment_ref', '')
        qs = _fuel_plan_qs(
            request, fuel_type=fuel_type,
            equipment_ref=eq_ref if eq_ref else None
        )
        count = qs.count()
        qs.delete()
        messages.success(request, f'Удалено всех планов: {count} записей')
        return redirect('production:fuel_plan_list')