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
    projects_count = Project.objects.filter(
        company=request.user.company, status="ACTIVE"
    ).count()
    approved_plans_count = PlanVersion.objects.filter(
        company=request.user.company, status="APPROVED"
    ).count()
    in_progress_count = ProjectWork.objects.filter(
        company=request.user.company, status="IN_PROGRESS"
    ).count()

    # Статусы работ
    status_counts = dict(
        ProjectWork.objects.filter(company=request.user.company)
        .values_list("status")
        .annotate(count=Count("id"))
        .values_list("status", "count")
    )

    from apps.works.progress import WorkProgressService
    from collections import defaultdict

    # Keep quantities separate by unit; never add cubic metres to pieces.
    dates = [today - timedelta(days=i) for i in range(6, -1, -1)]
    by_unit = defaultdict(
        lambda: {day: {"plan": Decimal("0"), "fact": Decimal("0")} for day in dates}
    )
    behind_schedule = []
    for work in ProjectWork.objects.filter(company=request.user.company):
        plans = WorkProgressService.planned(work)
        facts = WorkProgressService.facts(work, until=today)
        for day in dates:
            by_unit[work.unit][day]["plan"] += plans.get(day, {}).get("daily", 0)
            by_unit[work.unit][day]["fact"] += facts.get(day, {}).get("daily", 0)
        plan_total = sum(
            (v["daily"] for d, v in plans.items() if d <= today), Decimal("0")
        )
        fact_total = (
            next(reversed(facts.values()))["cumulative"] if facts else Decimal("0")
        )
        if work.status in ["PLANNED", "IN_PROGRESS"] and fact_total < plan_total:
            behind_schedule.append(
                {
                    "work": work,
                    "project_name": work.section.construction_object.project.name,
                    "plan_total": plan_total,
                    "fact_total": fact_total,
                    "deviation": fact_total - plan_total,
                }
            )
    last_7_days = [
        {"date": day, "unit": unit, **values}
        for unit, days in by_unit.items()
        for day, values in days.items()
    ]

    behind_schedule.sort(key=lambda x: x["deviation"])

    context = {
        "projects_count": projects_count,
        "approved_plans_count": approved_plans_count,
        "in_progress_count": in_progress_count,
        "behind_schedule_count": len(behind_schedule),
        "status_counts": status_counts,
        "last_7_days": last_7_days,
        "behind_schedule_works": behind_schedule[:10],
        "today": today,
    }
    return render(request, "analytics/dashboard.html", context)
