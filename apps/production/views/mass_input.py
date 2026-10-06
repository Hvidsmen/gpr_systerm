"""
Массовый ввод фактов по людям на дату.
"""
from django.shortcuts import render, redirect
from django.contrib import messages
from django.views.generic import View

from apps.production.models import LaborFact, LaborPlan, EquipmentFact, EquipmentPlan, FuelFact, FuelPlan
from apps.production.forms import LaborFactDailyForm,FuelFactDailyForm,EquipmentFactDailyForm
from apps.production.views.base import parse_date_safe


class LaborFactDailyInputView(View):
    """Массовый ввод факта по людям на конкретную дату."""
    template_name = 'production/labor_fact_daily_input.html'

    def get(self, request):
        form = LaborFactDailyForm(user=request.user)
        return render(request, self.template_name, {
            'form': form, 'rows': [],
            'selected_date': None, 'selected_project': None, 'selected_work': None,
        })

    def post(self, request):
        form = LaborFactDailyForm(request.POST, user=request.user)

        if 'select_date' in request.POST:
            if form.is_valid():
                target_date = form.cleaned_data['date']
                project = form.cleaned_data.get('project')
                project_work = form.cleaned_data.get('project_work')

                plans = LaborPlan.objects.filter(
                    company=request.user.company, date=target_date
                ).select_related('project', 'project_work', 'brigade').order_by('brigade__name')

                if project:
                    plans = plans.filter(project=project)
                if project_work:
                    plans = plans.filter(project_work=project_work)

                rows = []
                for plan in plans:
                    fact = LaborFact.objects.filter(
                        company=request.user.company, date=target_date,
                        brigade=plan.brigade, project=plan.project,
                        project_work=plan.project_work,
                    ).first()
                    rows.append({
                        'plan': plan, 'fact': fact,
                        'brigade': plan.brigade, 'project': plan.project,
                        'project_work': plan.project_work,
                        'planned_workers': plan.planned_workers,
                        'actual_workers': fact.actual_workers if fact else '',
                        'actual_hours': fact.actual_hours if fact else '',
                        'hourly_rate': fact.hourly_rate if fact else (plan.hourly_rate or ''),
                        'comment': fact.comment if fact else '',
                    })

                return render(request, self.template_name, {
                    'form': form, 'rows': rows,
                    'selected_date': target_date,
                    'selected_project': project, 'selected_work': project_work,
                    'plans_count': len(rows),
                })

        elif 'save_facts' in request.POST:
            target_date = parse_date_safe(request.POST.get('date'))
            if not target_date:
                messages.error(request, 'Некорректная дата')
                return redirect('production:labor_fact_daily_input')

            plan_ids = request.POST.getlist('plan_id')
            saved_count = 0

            for plan_id in plan_ids:
                try:
                    plan = LaborPlan.objects.get(pk=plan_id, company=request.user.company)
                    actual_workers = request.POST.get(f'actual_workers_{plan_id}', '')
                    actual_hours = request.POST.get(f'actual_hours_{plan_id}', '')
                    hourly_rate = request.POST.get(f'hourly_rate_{plan_id}', '')
                    comment = request.POST.get(f'comment_{plan_id}', '')

                    if not actual_workers and not actual_hours:
                        continue

                    LaborFact.objects.update_or_create(
                        company=request.user.company, date=target_date,
                        brigade=plan.brigade, project=plan.project,
                        project_work=plan.project_work,
                        defaults={
                            'actual_workers': int(actual_workers) if actual_workers else 0,
                            'actual_hours': float(actual_hours) if actual_hours else None,
                            'hourly_rate': float(hourly_rate) if hourly_rate else None,
                            'comment': comment,
                        }
                    )
                    saved_count += 1
                except LaborPlan.DoesNotExist:
                    continue
                except Exception as e:
                    messages.error(request, f'Ошибка при сохранении плана #{plan_id}: {e}')

            messages.success(request, f'Сохранено фактов: {saved_count} на дату {target_date.strftime("%d.%m.%Y")}')
            return redirect('production:labor_fact_list')

        return render(request, self.template_name, {'form': form, 'rows': []})



class EquipmentFactDailyInputView(View):
    """Массовый ввод факта по технике на конкретную дату."""
    template_name = 'production/equipment_fact_daily_input.html'

    def get(self, request):
        form = EquipmentFactDailyForm(user=request.user)
        return render(request, self.template_name, {
            'form': form, 'rows': [],
            'selected_date': None, 'selected_project': None, 'selected_work': None,
        })

    def post(self, request):
        form = EquipmentFactDailyForm(request.POST, user=request.user)

        if 'select_date' in request.POST:
            if form.is_valid():
                target_date = form.cleaned_data['date']
                project = form.cleaned_data.get('project')
                project_work = form.cleaned_data.get('project_work')
                equipment_type = form.cleaned_data.get('equipment_type')

                plans = EquipmentPlan.objects.filter(
                    company=request.user.company, date=target_date
                ).select_related('project', 'project_work', 'equipment_type').order_by('equipment_type__name')

                if project:
                    plans = plans.filter(project=project)
                if project_work:
                    plans = plans.filter(project_work=project_work)
                if equipment_type:
                    plans = plans.filter(equipment_type=equipment_type)

                rows = []
                for plan in plans:
                    fact = EquipmentFact.objects.filter(
                        company=request.user.company, date=target_date,
                        equipment_type=plan.equipment_type,
                        equipment_number=plan.equipment_number,
                        project=plan.project, project_work=plan.project_work,
                    ).first()
                    rows.append({
                        'plan': plan, 'fact': fact,
                        'equipment_type': plan.equipment_type,
                        'equipment_number': plan.equipment_number,
                        'project': plan.project, 'project_work': plan.project_work,
                        'planned_count': plan.planned_count,
                        'actual_count': fact.actual_count if fact else '',
                        'machine_hours': fact.machine_hours if fact else '',
                        'hourly_rate': fact.hourly_rate if fact else (plan.hourly_rate or ''),
                        'comment': fact.comment if fact else '',
                    })

                return render(request, self.template_name, {
                    'form': form, 'rows': rows,
                    'selected_date': target_date,
                    'selected_project': project, 'selected_work': project_work,
                    'plans_count': len(rows),
                })

        elif 'save_facts' in request.POST:
            from django.utils.dateparse import parse_date
            target_date = parse_date(request.POST.get('date'))
            if not target_date:
                messages.error(request, 'Некорректная дата')
                return redirect('production:equipment_fact_daily_input')

            plan_ids = request.POST.getlist('plan_id')
            saved_count = 0

            for plan_id in plan_ids:
                try:
                    plan = EquipmentPlan.objects.get(pk=plan_id, company=request.user.company)
                    actual_count = request.POST.get(f'actual_count_{plan_id}', '')
                    machine_hours = request.POST.get(f'machine_hours_{plan_id}', '')
                    hourly_rate = request.POST.get(f'hourly_rate_{plan_id}', '')
                    comment = request.POST.get(f'comment_{plan_id}', '')

                    if not actual_count and not machine_hours:
                        continue

                    EquipmentFact.objects.update_or_create(
                        company=request.user.company, date=target_date,
                        equipment_type=plan.equipment_type,
                        equipment_number=plan.equipment_number,
                        project=plan.project, project_work=plan.project_work,
                        defaults={
                            'actual_count': int(actual_count) if actual_count else 0,
                            'machine_hours': float(machine_hours) if machine_hours else None,
                            'hourly_rate': float(hourly_rate) if hourly_rate else None,
                            'comment': comment,
                        }
                    )
                    saved_count += 1
                except EquipmentPlan.DoesNotExist:
                    continue
                except Exception as e:
                    messages.error(request, f'Ошибка при сохранении плана #{plan_id}: {e}')

            messages.success(request, f'Сохранено фактов: {saved_count} на дату {target_date.strftime("%d.%m.%Y")}')
            return redirect('production:equipment_fact_list')

        return render(request, self.template_name, {'form': form, 'rows': []})


class FuelFactDailyInputView(View):
    """Массовый ввод факта по ГСМ на конкретную дату."""
    template_name = 'production/fuel_fact_daily_input.html'

    def get(self, request):
        form = FuelFactDailyForm(user=request.user)
        return render(request, self.template_name, {
            'form': form, 'rows': [],
            'selected_date': None, 'selected_project': None, 'selected_work': None,
        })

    def post(self, request):
        form = FuelFactDailyForm(request.POST, user=request.user)

        if 'select_date' in request.POST:
            if form.is_valid():
                target_date = form.cleaned_data['date']
                project = form.cleaned_data.get('project')
                project_work = form.cleaned_data.get('project_work')
                fuel_type = form.cleaned_data.get('fuel_type')

                plans = FuelPlan.objects.filter(
                    company=request.user.company, date=target_date
                ).select_related('project', 'project_work').order_by('fuel_type', 'equipment_ref')

                if project:
                    plans = plans.filter(project=project)
                if project_work:
                    plans = plans.filter(project_work=project_work)
                if fuel_type:
                    plans = plans.filter(fuel_type=fuel_type)

                rows = []
                for plan in plans:
                    fact = FuelFact.objects.filter(
                        company=request.user.company, date=target_date,
                        fuel_type=plan.fuel_type,
                        equipment_ref=plan.equipment_ref,
                        project=plan.project, project_work=plan.project_work,
                    ).first()
                    rows.append({
                        'plan': plan, 'fact': fact,
                        'fuel_type': plan.fuel_type,
                        'fuel_type_display': plan.get_fuel_type_display(),
                        'equipment_ref': plan.equipment_ref,
                        'project': plan.project, 'project_work': plan.project_work,
                        'planned_liters': plan.planned_liters,
                        'actual_liters': fact.actual_liters if fact else '',
                        'price_per_liter': fact.price_per_liter if fact else (plan.price_per_liter or ''),
                        'comment': fact.comment if fact else '',
                    })

                return render(request, self.template_name, {
                    'form': form, 'rows': rows,
                    'selected_date': target_date,
                    'selected_project': project, 'selected_work': project_work,
                    'plans_count': len(rows),
                })

        elif 'save_facts' in request.POST:
            from django.utils.dateparse import parse_date
            target_date = parse_date(request.POST.get('date'))
            if not target_date:
                messages.error(request, 'Некорректная дата')
                return redirect('production:fuel_fact_daily_input')

            plan_ids = request.POST.getlist('plan_id')
            saved_count = 0

            for plan_id in plan_ids:
                try:
                    plan = FuelPlan.objects.get(pk=plan_id, company=request.user.company)
                    actual_liters = request.POST.get(f'actual_liters_{plan_id}', '')
                    price_per_liter = request.POST.get(f'price_per_liter_{plan_id}', '')
                    comment = request.POST.get(f'comment_{plan_id}', '')

                    if not actual_liters:
                        continue

                    FuelFact.objects.update_or_create(
                        company=request.user.company, date=target_date,
                        fuel_type=plan.fuel_type,
                        equipment_ref=plan.equipment_ref,
                        project=plan.project, project_work=plan.project_work,
                        defaults={
                            'actual_liters': float(actual_liters) if actual_liters else 0,
                            'price_per_liter': float(price_per_liter) if price_per_liter else None,
                            'comment': comment,
                        }
                    )
                    saved_count += 1
                except FuelPlan.DoesNotExist:
                    continue
                except Exception as e:
                    messages.error(request, f'Ошибка при сохранении плана #{plan_id}: {e}')

            messages.success(request, f'Сохранено фактов: {saved_count} на дату {target_date.strftime("%d.%m.%Y")}')
            return redirect('production:fuel_fact_list')

        return render(request, self.template_name, {'form': form, 'rows': []})