"""
Views для фактов по технике.
"""
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.views.generic import ListView, CreateView, UpdateView, DeleteView, View
from django.urls import reverse_lazy
from django.http import JsonResponse
from django.views.decorators.http import require_POST
from django.views.decorators.csrf import csrf_protect

from core.mixins import CompanyRequiredMixin
from apps.production.models import EquipmentFact
from apps.production.forms import EquipmentFactForm
from apps.production.views.base import parse_fk_id, build_update_data
from apps.projects.models import Project
from apps.works.models import ProjectWork
from apps.resources.models import EquipmentType


class EquipmentFactListView(ListView):
    model = EquipmentFact
    template_name = 'production/equipment_fact_list.html'
    context_object_name = 'equipment_facts'
    paginate_by = None

    def get_queryset(self):
        qs = EquipmentFact.objects.filter(company=self.request.user.company) \
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
        facts = context['equipment_facts']

        rows, dates_set = {}, set()
        for fact in facts:
            row_key = f"{fact.equipment_type_id}_{fact.equipment_number or ''}"
            if row_key not in rows:
                rows[row_key] = {
                    'equipment_type': fact.equipment_type,
                    'equipment_number': fact.equipment_number,
                    'project': fact.project,
                    'project_work': fact.project_work,
                    'facts_by_date': {}
                }
            rows[row_key]['facts_by_date'][fact.date.isoformat()] = fact
            dates_set.add(fact.date)

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


def _setup_equipment_fact_form(form, company):
    form.fields['project'].queryset = Project.objects.filter(company=company)
    form.fields['project_work'].queryset = ProjectWork.objects.filter(company=company)
    form.fields['equipment_type'].queryset = EquipmentType.objects.filter(company=company, is_active=True)


class EquipmentFactCreateView(CompanyRequiredMixin, CreateView):
    model = EquipmentFact
    form_class = EquipmentFactForm
    template_name = 'production/resource_form.html'

    def get_form(self, form_class=None):
        form = super().get_form(form_class)
        _setup_equipment_fact_form(form, self.request.user.company)
        return form

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        work_pk = self.kwargs.get('work_pk')
        if work_pk:
            context['work'] = get_object_or_404(ProjectWork, pk=work_pk)
        context['title'] = 'Ввод факта по технике'
        context['resource_type'] = 'equipment'
        return context

    def form_valid(self, form):
        form.instance.company = self.request.user.company
        messages.success(self.request, 'Данные по технике сохранены!')
        return super().form_valid(form)

    def get_success_url(self):
        work_pk = self.kwargs.get('work_pk')
        return reverse_lazy('production:fact_daily') if work_pk else reverse_lazy('production:equipment_fact_list')


class EquipmentFactUpdateView(UpdateView):
    model = EquipmentFact
    form_class = EquipmentFactForm
    template_name = 'production/resource_form.html'

    def get_form(self, form_class=None):
        form = super().get_form(form_class)
        _setup_equipment_fact_form(form, self.request.user.company)
        return form

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['title'] = 'Редактирование факта по технике'
        context['resource_type'] = 'equipment'
        return context

    def form_valid(self, form):
        messages.success(self.request, 'Данные по технике обновлены!')
        return super().form_valid(form)

    def get_success_url(self):
        return reverse_lazy('production:equipment_fact_list')


class EquipmentFactDeleteView(DeleteView):
    model = EquipmentFact
    template_name = 'production/resource_confirm_delete.html'
    success_url = reverse_lazy('production:equipment_fact_list')

    def delete(self, request, *args, **kwargs):
        fact = self.get_object()
        messages.success(request, f'Факт по технике "{fact.equipment_type}" за {fact.date} удалён')
        return super().delete(request, *args, **kwargs)


@csrf_protect
@require_POST
def equipment_fact_inline_update(request):
    import json
    try:
        data = json.loads(request.body)
        fact = EquipmentFact.objects.get(pk=data['fact_id'], company=request.user.company)
        field_map = {
            'actual_count': lambda v: int(v) if v else 0,
            'machine_hours': lambda v: float(v) if v else None,
            'hourly_rate': lambda v: float(v) if v else None,
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
    except EquipmentFact.DoesNotExist:
        return JsonResponse({'success': False, 'message': 'Не найдено'}, status=404)
    except Exception as e:
        return JsonResponse({'success': False, 'message': str(e)}, status=400)


def _equipment_fact_qs(request, **filters):
    qs = EquipmentFact.objects.filter(company=request.user.company)
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


_EQUIPMENT_FACT_FIELDS = [
    ('actual_count', lambda v: int(v) if v else None),
    ('machine_hours', lambda v: float(v) if v else None),
    ('hourly_rate', lambda v: float(v) if v else None),
]


class EquipmentFactEditByDateView(View):
    template_name = 'production/equipment_fact_batch_edit.html'

    def get(self, request, date_str):
        from apps.production.views.base import parse_date_safe
        target_date = parse_date_safe(date_str)
        if not target_date:
            messages.error(request, 'Некорректная дата')
            return redirect('production:equipment_fact_list')

        et_id = request.GET.get('equipment_type_id')
        eq_num = request.GET.get('equipment_number', '')

        facts = _equipment_fact_qs(
            request, equipment_type_id=et_id,
            equipment_number=eq_num if eq_num else None, date=target_date
        ).select_related('project', 'project_work', 'equipment_type')

        return render(request, self.template_name, {
            'target_date': target_date, 'facts': facts,
            'equipment_type_id': et_id, 'equipment_number': eq_num,
        })

    def post(self, request, date_str):
        from apps.production.views.base import parse_date_safe
        target_date = parse_date_safe(date_str)
        if not target_date:
            messages.error(request, 'Некорректная дата')
            return redirect('production:equipment_fact_list')

        et_id = request.POST.get('equipment_type_id')
        eq_num = request.POST.get('equipment_number', '')
        qs = _equipment_fact_qs(
            request, equipment_type_id=et_id,
            equipment_number=eq_num if eq_num else None, date=target_date
        )
        update_data = build_update_data(request, _EQUIPMENT_FACT_FIELDS)
        if update_data:
            count = qs.update(**update_data)
            messages.success(request, f'Обновлено фактов: {count} на дату {target_date.strftime("%d.%m.%Y")}')
        else:
            messages.warning(request, 'Не указаны значения для обновления')
        return redirect('production:equipment_fact_list')


class EquipmentFactEditRangeView(View):
    template_name = 'production/equipment_fact_range_edit.html'

    def get(self, request):
        et_id = request.GET.get('equipment_type_id')
        if not et_id:
            messages.error(request, 'Не указан вид техники')
            return redirect('production:equipment_fact_list')

        eq_num = request.GET.get('equipment_number', '')
        date_from = request.GET.get('date_from')
        date_to = request.GET.get('date_to')

        facts = _equipment_fact_qs(
            request, equipment_type_id=et_id,
            equipment_number=eq_num if eq_num else None,
            date_from=date_from, date_to=date_to
        ).select_related('project', 'project_work', 'equipment_type').order_by('date')

        return render(request, self.template_name, {
            'facts': facts, 'equipment_type_id': et_id,
            'equipment_number': eq_num,
            'date_from': date_from or '', 'date_to': date_to or '',
            'facts_count': facts.count(),
        })

    def post(self, request):
        et_id = request.POST.get('equipment_type_id')
        if not et_id:
            messages.error(request, 'Не указан вид техники')
            return redirect('production:equipment_fact_list')

        eq_num = request.POST.get('equipment_number', '')
        date_from = request.POST.get('date_from')
        date_to = request.POST.get('date_to')

        qs = _equipment_fact_qs(
            request, equipment_type_id=et_id,
            equipment_number=eq_num if eq_num else None,
            date_from=date_from, date_to=date_to
        )
        update_data = build_update_data(request, _EQUIPMENT_FACT_FIELDS)
        if update_data:
            count = qs.update(**update_data)
            messages.success(request, f'Обновлено фактов: {count}')
        else:
            messages.warning(request, 'Не указаны значения для обновления')
        return redirect('production:equipment_fact_list')


class EquipmentFactDeleteByDateView(View):
    def post(self, request, date_str):
        from apps.production.views.base import parse_date_safe
        target_date = parse_date_safe(date_str)
        if not target_date:
            messages.error(request, 'Некорректная дата')
            return redirect('production:equipment_fact_list')

        et_id = request.POST.get('equipment_type_id')
        eq_num = request.POST.get('equipment_number', '')
        qs = _equipment_fact_qs(
            request, equipment_type_id=et_id,
            equipment_number=eq_num if eq_num else None, date=target_date
        )
        count = qs.count()
        qs.delete()
        messages.success(request, f'Удалено фактов: {count} на дату {target_date.strftime("%d.%m.%Y")}')
        return redirect('production:equipment_fact_list')


class EquipmentFactDeleteByTypeView(View):
    def post(self, request, equipment_type_id):
        eq_num = request.POST.get('equipment_number', '')
        qs = _equipment_fact_qs(
            request, equipment_type_id=equipment_type_id,
            equipment_number=eq_num if eq_num else None
        )
        count = qs.count()
        qs.delete()
        messages.success(request, f'Удалено всех фактов: {count} записей')
        return redirect('production:equipment_fact_list')