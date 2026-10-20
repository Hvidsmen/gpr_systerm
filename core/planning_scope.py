"""Object assignment checks for planning endpoints with custom view lookups."""
from django.http import Http404
from django.shortcuts import get_object_or_404
from .permissions import role_code, scope_queryset


def check_planning_scope(request, match):
    if role_code(request.user) != 'PLANNER' or match.namespace != 'planning':
        return
    from apps.planning import models
    pk = match.kwargs.get('pk') or match.kwargs.get('plan_pk')
    if not pk:
        return
    route = match.route
    model = None
    # More specific paths must precede their enclosing plan paths.
    paths = [
        ('daily-schedule/entry/', models.DailyScheduleEntry),
        ('daily-schedule/', models.DailySchedule),
        ('workspace-versions/', models.GlobalPlanVersion),
        ('workspace/', models.PlanningWorkspace),
        ('global/', models.GlobalPlanVersion),
        ('gpr/<', models.GlobalPlanVersion),
        ('smg/<', models.PlanningWorkspace),
        ('macro/<', models.PlanningWorkspace),
        ('carryover-tasks/', models.CarryoverReviewTask),
        ('estimate-reviews/', models.EstimateReviewTask),
        ('tasks/<', models.WorkTask),
        ('project-plans/<', models.ProjectPlanVersion),
        ('versions/', models.PlanVersion),
    ]
    if match.url_name == 'estimate_review_assign':
        model = models.DailySchedule if match.kwargs.get('kind') == 'smg' else models.GlobalPlanVersion
    else:
        for path, candidate in paths:
            if path in route:
                model = candidate
                break
    if model is None and (match.kwargs.get('plan_pk') or match.url_name in {'plan_detail','plan_update','plan_delete'}):
        model = models.MonthlyPlan
    if model is not None:
        get_object_or_404(scope_queryset(model.objects.all(), request.user), pk=pk)
