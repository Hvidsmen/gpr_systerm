"""
Views для фактов по ГСМ.
"""
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.views.generic import ListView, CreateView, UpdateView, DeleteView, View
from django.urls import reverse_lazy
from django.http import JsonResponse
from django.views.decorators.http import require_POST
from django.views.decorators.csrf import csrf_protect

from core.mixins import CompanyRequiredMixin
from apps.production.models import FuelFact
from apps.production.forms import FuelFactForm
from apps.production.views.base import build_update_data
from apps.projects.models import Project
from apps.works.models import ProjectWork


class FuelFactListView(ListView):
    model = FuelFact
    template_name = 'production/fuel_fact_list.html'
    context_object_name = 'fuel_facts'
    paginate_by = None

    def get_queryset(self):
        qs = FuelFact.objects.filter(company=self.request.user.company) \
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
        facts = context['fuel_facts']

        rows, dates_set = {}, set()
        for fact in facts:
            row_key = f"{fact.fuel_type}_{fact.equipment_ref or ''}"
            if row_key not in rows:
                rows[row_key] = {
                    'fuel_type': fact.fuel_type,
                    'fuel_type_display': fact.get_fuel_type_display(),
                    'equipment_ref': fact.equipment_ref,
                    'project': fact.project,
                    'project_work': fact.project_work,
                    'facts_by_date': {}
                }
            rows[row_key]['facts_by_date'][fact.date.isoformat()] = fact
            dates_set.add(fact.date)

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
            'fuel_type_choices': FuelFact.FUEL_TYPE_CHOICES,
        })
        return context


def _setup_fuel_fact_form(form, company):
    form.fields['project'].queryset = Project.objects.filter(company=company)
    form.fields['project_work'].queryset = ProjectWork.objects.filter(company=company)


class FuelFactCreateView(CompanyRequiredMixin, CreateView):
    model = FuelFact
    form_class = FuelFactForm
    template_name = 'production/resource_form.html'

    def get_form(self, form_class=None):
        form = super().get_form(form_class)
        _setup_fuel_fact_form(form, self.request.user.company)
        return form

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        work_pk = self.kwargs.get('work_pk')
        if work_pk:
            context['work'] = get_object_or_404(ProjectWork, pk=work_pk)
        context['title'] = 'Ввод факта по ГСМ'
        context['resource_type'] = 'fuel'
        return context

    def form_valid(self, form):
        form.instance.company = self.request.user.company
        messages.success(self.request, 'Данные по ГСМ сохранены!')
        return super().form_valid(form)

    def get_success_url(self):
        work_pk = self.kwargs.get('work_pk')
        return reverse_lazy('production:fact_daily') if work_pk else reverse_lazy('production:fuel_fact_list')


class FuelFactUpdateView(UpdateView):
    model = FuelFact
    form_class = FuelFactForm
    template_name = 'production/resource_form.html'

    def get_form(self, form_class=None):
        form = super().get_form(form_class)
        _setup_fuel_fact_form(form, self.request.user.company)
        return form

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['title'] = 'Редактирование факта по ГСМ'
        context['resource_type'] = 'fuel'
        return context

    def form_valid(self, form):
        messages.success(self.request, 'Данные по ГСМ обновлены!')
        return super().form_valid(form)

    def get_success_url(self):
        return reverse_lazy('production:fuel_fact_list')


class FuelFactDeleteView(DeleteView):
    model = FuelFact
    template_name = 'production/resource_confirm_delete.html'
    success_url = reverse_lazy('production:fuel_fact_list')

    def delete(self, request, *args, **kwargs):
        fact = self.get_object()
        messages.success(request, f'Факт по ГСМ "{fact.get_fuel_type_display()}" за {fact.date} удалён')
        return super().delete(request, *args, **kwargs)


@csrf_protect
@require_POST
def fuel_fact_inline_update(request):
    import json
    try:
        data = json.loads(request.body)
        fact = FuelFact.objects.get(pk=data['fact_id'], company=request.user.company)
        field_map = {
            'actual_liters': lambda v: float(v) if v else 0,
            'price_per_liter': lambda v: float(v) if v else None,
        }
        field, value = data.get('field'), data.get('value')
        if field == 'comment':
            fact.comment = value
        elif field in field_map:
            setattr(fact, field, field_map[field](value))
        else:
            return JsonResponse({'success': False, 'message': f'Неизвестное поле: {field}'}, status=400)
        fact.save()
        return JsonResponse({'success': True})
    except FuelFact.DoesNotExist:
        return JsonResponse({'success': False, 'message': 'Не найдено'}, status=404)
    except Exception as e:
        return JsonResponse({'success': False, 'message': str(e)}, status=400)


def _fuel_fact_qs(request, **filters):
    qs = FuelFact.objects.filter(company=request.user.company)
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


_FUEL_FACT_FIELDS = [
    ('actual_liters', lambda v: float(v) if v else None),
    ('price_per_liter', lambda v: float(v) if v else None),
]


class FuelFactEditByDateView(View):
    template_name = 'production/fuel_fact_batch_edit.html'

    def get(self, request, date_str):
        from apps.production.views.base import parse_date_safe
        target_date = parse_date_safe(date_str)
        if not target_date:
            messages.error(request, 'Некорректная дата')
            return redirect('production:fuel_fact_list')

        fuel_type = request.GET.get('fuel_type')
        eq_ref = request.GET.get('equipment_ref', '')
        facts = _fuel_fact_qs(
            request, fuel_type=fuel_type,
            equipment_ref=eq_ref if eq_ref else None, date=target_date
        ).select_related('project', 'project_work')

        return render(request, self.template_name, {
            'target_date': target_date, 'facts': facts,
            'fuel_type': fuel_type, 'equipment_ref': eq_ref,
        })

    def post(self, request, date_str):
        from apps.production.views.base import parse_date_safe
        target_date = parse_date_safe(date_str)
        if not target_date:
            messages.error(request, 'Некорректная дата')
            return redirect('production:fuel_fact_list')

        fuel_type = request.POST.get('fuel_type')
        eq_ref = request.POST.get('equipment_ref', '')
        qs = _fuel_fact_qs(
            request, fuel_type=fuel_type,
            equipment_ref=eq_ref if eq_ref else None, date=target_date
        )
        update_data = build_update_data(request, _FUEL_FACT_FIELDS)
        if update_data:
            count = qs.update(**update_data)
            messages.success(request, f'Обновлено фактов: {count} на дату {target_date.strftime("%d.%m.%Y")}')
        else:
            messages.warning(request, 'Не указаны значения для обновления')
        return redirect('production:fuel_fact_list')


class FuelFactEditRangeView(View):
    template_name = 'production/fuel_fact_range_edit.html'

    def get(self, request):
        fuel_type = request.GET.get('fuel_type')
        if not fuel_type:
            messages.error(request, 'Не указан вид ГСМ')
            return redirect('production:fuel_fact_list')

        eq_ref = request.GET.get('equipment_ref', '')
        date_from = request.GET.get('date_from')
        date_to = request.GET.get('date_to')

        facts = _fuel_fact_qs(
            request, fuel_type=fuel_type,
            equipment_ref=eq_ref if eq_ref else None,
            date_from=date_from, date_to=date_to
        ).select_related('project', 'project_work').order_by('date')

        return render(request, self.template_name, {
            'facts': facts, 'fuel_type': fuel_type,
            'equipment_ref': eq_ref,
            'date_from': date_from or '', 'date_to': date_to or '',
            'facts_count': facts.count(),
        })

    def post(self, request):
        fuel_type = request.POST.get('fuel_type')
        if not fuel_type:
            messages.error(request, 'Не указан вид ГСМ')
            return redirect('production:fuel_fact_list')

        eq_ref = request.POST.get('equipment_ref', '')
        date_from = request.POST.get('date_from')
        date_to = request.POST.get('date_to')

        qs = _fuel_fact_qs(
            request, fuel_type=fuel_type,
            equipment_ref=eq_ref if eq_ref else None,
            date_from=date_from, date_to=date_to
        )
        update_data = build_update_data(request, _FUEL_FACT_FIELDS)
        if update_data:
            count = qs.update(**update_data)
            messages.success(request, f'Обновлено фактов: {count}')
        else:
            messages.warning(request, 'Не указаны значения для обновления')
        return redirect('production:fuel_fact_list')


class FuelFactDeleteByDateView(View):
    def post(self, request, date_str):
        from apps.production.views.base import parse_date_safe
        target_date = parse_date_safe(date_str)
        if not target_date:
            messages.error(request, 'Некорректная дата')
            return redirect('production:fuel_fact_list')

        fuel_type = request.POST.get('fuel_type')
        eq_ref = request.POST.get('equipment_ref', '')
        qs = _fuel_fact_qs(
            request, fuel_type=fuel_type,
            equipment_ref=eq_ref if eq_ref else None, date=target_date
        )
        count = qs.count()
        qs.delete()
        messages.success(request, f'Удалено фактов: {count} на дату {target_date.strftime("%d.%m.%Y")}')
        return redirect('production:fuel_fact_list')


class FuelFactDeleteByTypeView(View):
    def post(self, request, fuel_type):
        eq_ref = request.POST.get('equipment_ref', '')
        qs = _fuel_fact_qs(
            request, fuel_type=fuel_type,
            equipment_ref=eq_ref if eq_ref else None
        )
        count = qs.count()
        qs.delete()
        messages.success(request, f'Удалено всех фактов: {count} записей')
        return redirect('production:fuel_fact_list')