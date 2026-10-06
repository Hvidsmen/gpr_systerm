from django.contrib.auth.decorators import login_required
from django.shortcuts import render
from django.utils import timezone
from datetime import timedelta
from django.db.models import Sum, Count, Q, F
from decimal import Decimal

from apps.projects.models import Project
from apps.works.models import ProjectWork
from apps.planning.models import MonthlyPlan, PlanVersion, DailyPlan
from apps.production.models import DailyFact


@login_required
def dashboard_view(request):
    today = timezone.now().date()

    # KPI
    projects_count = Project.objects.filter(company=request.user.company, status='ACTIVE').count()
    approved_plans_count = PlanVersion.objects.filter(company=request.user.company, status='APPROVED').count()
    in_progress_count = ProjectWork.objects.filter(company=request.user.company, status='IN_PROGRESS').count()

    # Статусы работ
    status_counts = dict(
        ProjectWork.objects.filter(company=request.user.company).values_list('status').annotate(
            count=Count('id')
        ).values_list('status', 'count')
    )

    # Последние 7 дней: план/факт
    last_7_days = []
    for i in range(6, -1, -1):
        day = today - timedelta(days=i)
        plan = DailyPlan.objects.filter(company=request.user.company, date=day).aggregate(
            total=Sum('planned_quantity')
        )['total'] or Decimal('0')
        fact = DailyFact.objects.filter(company=request.user.company, date=day).aggregate(
            total=Sum('actual_quantity')
        )['total'] or Decimal('0')
        last_7_days.append({
            'date': day,
            'plan': plan,
            'fact': fact,
        })

    # Работы с отставанием
    behind_schedule = []
    for work in ProjectWork.objects.filter(company=request.user.company, status__in=['PLANNED', 'IN_PROGRESS']):
        plans = DailyPlan.objects.filter(
            company=request.user.company,
            work_item__project_work=work,
            date__lte=today
        ).aggregate(total=Sum('planned_quantity'))['total'] or Decimal('0')

        facts = DailyFact.objects.filter(
            company=request.user.company,
            project_work=work,
            date__lte=today
        ).aggregate(total=Sum('actual_quantity'))['total'] or Decimal('0')

        deviation = facts - plans
        if deviation < 0:
            behind_schedule.append({
                'work': work,
                'project_name': work.section.construction_object.project.name,
                'plan_total': plans,
                'fact_total': facts,
                'deviation': deviation,
            })

    behind_schedule.sort(key=lambda x: x['deviation'])

    context = {
        'projects_count': projects_count,
        'approved_plans_count': approved_plans_count,
        'in_progress_count': in_progress_count,
        'behind_schedule_count': len(behind_schedule),
        'status_counts': status_counts,
        'last_7_days': last_7_days,
        'behind_schedule_works': behind_schedule[:10],
        'today': today,
    }
    return render(request, 'analytics/dashboard.html', context)