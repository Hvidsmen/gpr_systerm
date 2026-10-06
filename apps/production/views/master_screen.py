"""
Экран мастера — ежедневный ввод факта.
"""
from decimal import Decimal
from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect
from django.contrib import messages
from django.utils import timezone
from apps.planning.models import PlanVersion
from apps.production.models import (
    DailyFact, LaborFact, EquipmentFact, FuelFact,
    LaborPlan, EquipmentPlan, FuelPlan, DeviationReason
)


@login_required
def fact_daily_view(request):
    """Экран мастера: ввод факта на сегодня. Планы по ресурсам подтягиваются из LaborPlan/EquipmentPlan/FuelPlan."""
    today = timezone.now().date()

    active_versions = PlanVersion.objects.filter(
        company=request.user.company,
        status='APPROVED',
        monthly_plan__start_date__lte=today,
        monthly_plan__end_date__gte=today
    ).select_related('monthly_plan__project_work__section__construction_object__project')

    works_with_plan = []

    # Собираем уникальные проекты из активных версий
    projects = set()
    for version in active_versions:
        project = version.monthly_plan.project_work.section.construction_object.project
        projects.add(project)

    # Получаем ВСЕ планы ресурсов на сегодня по этим проектам
    all_labor_plans = LaborPlan.objects.filter(
        company=request.user.company,
        date=today, project__in=projects
    ).select_related('brigade', 'project').order_by('brigade__name')

    all_equipment_plans = EquipmentPlan.objects.filter(
        company=request.user.company,
        date=today, project__in=projects
    ).select_related('equipment_type', 'project').order_by('equipment_type__name')

    all_fuel_plans = FuelPlan.objects.filter(
        company=request.user.company,
        date=today, project__in=projects
    ).select_related('project').order_by('fuel_type', 'equipment_ref')

    # Группируем планы по проектам
    labor_by_project = {}
    for lp in all_labor_plans:
        proj_key = lp.project_id
        if proj_key not in labor_by_project:
            labor_by_project[proj_key] = []
        labor_by_project[proj_key].append(lp)

    equipment_by_project = {}
    for ep in all_equipment_plans:
        proj_key = ep.project_id
        if proj_key not in equipment_by_project:
            equipment_by_project[proj_key] = []
        equipment_by_project[proj_key].append(ep)

    fuel_by_project = {}
    for fp in all_fuel_plans:
        proj_key = fp.project_id
        if proj_key not in fuel_by_project:
            fuel_by_project[proj_key] = []
        fuel_by_project[proj_key].append(fp)

    for version in active_versions:
        daily_plans = version.daily_plans.filter(date=today).select_related('work_item')
        if not daily_plans.exists():
            continue

        work = version.monthly_plan.project_work
        project = work.section.construction_object.project

        # Объёмы работ (подработы)
        items_data = []
        for dp in daily_plans:
            fact = DailyFact.objects.filter(
                company=request.user.company,
                project_work=work, work_item=dp.work_item, date=today
            ).first()
            items_data.append({'item': dp.work_item, 'daily_plan': dp, 'fact': fact})

        # ===== ЛЮДИ: берём из LaborPlan по проекту =====
        labor_plans = labor_by_project.get(project.pk, [])
        labor_rows = []
        for plan in labor_plans:
            fact = LaborFact.objects.filter(
                company=request.user.company,
                project=project, date=today, brigade=plan.brigade
            ).first()
            labor_rows.append({
                'plan': plan,
                'fact': fact,
                'brigade': plan.brigade,
                'planned_workers': plan.planned_workers,
                'planned_hours': plan.planned_hours,
                'hourly_rate': plan.hourly_rate,
                'actual_workers': fact.actual_workers if fact else '',
                'actual_hours': fact.actual_hours if fact else '',
            })

        # ===== ТЕХНИКА: берём из EquipmentPlan по проекту =====
        equipment_plans = equipment_by_project.get(project.pk, [])
        equipment_rows = []
        for plan in equipment_plans:
            fact = EquipmentFact.objects.filter(
                company=request.user.company,
                project=project, date=today,
                equipment_type=plan.equipment_type,
                equipment_number=plan.equipment_number
            ).first()
            equipment_rows.append({
                'plan': plan,
                'fact': fact,
                'equipment_type': plan.equipment_type,
                'equipment_number': plan.equipment_number,
                'planned_count': plan.planned_count,
                'planned_machine_hours': plan.planned_machine_hours,
                'hourly_rate': plan.hourly_rate,
                'actual_count': fact.actual_count if fact else '',
                'machine_hours': fact.machine_hours if fact else '',
            })

        # ===== ГСМ: берём из FuelPlan по проекту =====
        fuel_plans = fuel_by_project.get(project.pk, [])
        fuel_rows = []
        for plan in fuel_plans:
            fact = FuelFact.objects.filter(
                company=request.user.company,
                project=project, date=today,
                fuel_type=plan.fuel_type,
                equipment_ref=plan.equipment_ref
            ).first()
            fuel_rows.append({
                'plan': plan,
                'fact': fact,
                'fuel_type': plan.fuel_type,
                'fuel_type_display': plan.get_fuel_type_display(),
                'equipment_ref': plan.equipment_ref,
                'planned_liters': plan.planned_liters,
                'price_per_liter': plan.price_per_liter,
                'actual_liters': fact.actual_liters if fact else '',
                'fact_price': fact.price_per_liter if fact else '',
            })

        works_with_plan.append({
            'work': work,
            'version': version,
            'project': project,
            'items': items_data,
            'labor_rows': labor_rows,
            'equipment_rows': equipment_rows,
            'fuel_rows': fuel_rows,
        })

    deviation_reasons = DeviationReason.objects.filter(company=request.user.company, is_active=True)

    if request.method == 'POST':
        saved_count = 0

        # 1. Сохраняем объёмы работ
        for work_data in works_with_plan:
            for item_data in work_data['items']:
                fact_value = request.POST.get(f'fact_{item_data["item"].pk}')
                reason_id = request.POST.get(f'reason_{item_data["item"].pk}')
                comment = request.POST.get(f'comment_{item_data["item"].pk}', '')
                if fact_value is not None and fact_value != '':
                    try:
                        qty = Decimal(fact_value)
                        reason = DeviationReason.objects.get(company=request.user.company, pk=reason_id) if reason_id else None
                        work = work_data['work']
                        actual_value = qty * work.unit_price
                        DailyFact.objects.update_or_create(
                            company=request.user.company,
                            project_work=work, work_item=item_data['item'], date=today,
                            defaults={
                                'actual_quantity': qty, 'actual_value': actual_value,
                                'reported_by': request.user, 'deviation_reason': reason,
                                'comment': comment, 'company': request.user.company,
                            }
                        )
                        saved_count += 1
                    except Exception as e:
                        messages.error(request, f'Ошибка для {item_data["item"].name}: {e}')

        # 2. Сохраняем факты по людям (по проекту)
        for work_data in works_with_plan:
            project = work_data['project']
            for row in work_data['labor_rows']:
                plan = row['plan']
                actual_workers = request.POST.get(f'labor_workers_{plan.pk}', '')
                actual_hours = request.POST.get(f'labor_hours_{plan.pk}', '')
                if actual_workers and actual_workers != '':
                    try:
                        LaborFact.objects.update_or_create(
                            project=project,
                            date=today,
                            brigade=plan.brigade,
                            company=request.user.company,
                            defaults={
                                'planned_workers': plan.planned_workers,
                                'planned_hours': plan.planned_hours,
                                'actual_workers': int(actual_workers),
                                'actual_hours': Decimal(actual_hours) if actual_hours else None,
                                'hourly_rate': plan.hourly_rate,
                            }
                        )
                        saved_count += 1
                    except Exception as e:
                        messages.error(request, f'Ошибка для бригады {plan.brigade}: {e}')

        # 3. Сохраняем факты по технике (по проекту)
        for work_data in works_with_plan:
            project = work_data['project']
            for row in work_data['equipment_rows']:
                plan = row['plan']
                actual_count = request.POST.get(f'equipment_count_{plan.pk}', '')
                machine_hours = request.POST.get(f'equipment_hours_{plan.pk}', '')
                if actual_count and actual_count != '':
                    try:
                        EquipmentFact.objects.update_or_create(
                            project=project,
                            date=today,
                            equipment_type=plan.equipment_type,
                            equipment_number=plan.equipment_number,
                            company=request.user.company,
                            defaults={
                                'planned_count': plan.planned_count,
                                'planned_machine_hours': plan.planned_machine_hours,
                                'actual_count': int(actual_count),
                                'machine_hours': Decimal(machine_hours) if machine_hours else None,
                                'hourly_rate': plan.hourly_rate,
                            }
                        )
                        saved_count += 1
                    except Exception as e:
                        messages.error(request, f'Ошибка для техники {plan.equipment_type}: {e}')

        # 4. Сохраняем факты по ГСМ (по проекту)
        for work_data in works_with_plan:
            project = work_data['project']
            for row in work_data['fuel_rows']:
                plan = row['plan']
                actual_liters = request.POST.get(f'fuel_liters_{plan.pk}', '')
                price = request.POST.get(f'fuel_price_{plan.pk}', '')
                if actual_liters and actual_liters != '':
                    try:
                        FuelFact.objects.update_or_create(
                            project=project,
                            date=today,
                            fuel_type=plan.fuel_type,
                            equipment_ref=plan.equipment_ref,
                            company=request.user.company,
                            defaults={
                                'actual_liters': Decimal(actual_liters),
                                'price_per_liter': Decimal(price) if price else plan.price_per_liter,
                            }
                        )
                        saved_count += 1
                    except Exception as e:
                        messages.error(request, f'Ошибка для ГСМ {plan.get_fuel_type_display()}: {e}')

        messages.success(request, f'Сохранено записей: {saved_count}')
        return redirect('production:fact_daily')

    return render(request, 'production/fact_daily.html', {
        'today': today,
        'works_with_plan': works_with_plan,
        'deviation_reasons': deviation_reasons,
    })