from django.utils import timezone
from core.permissions import role_code


def planning_tasks(request):
    user=request.user
    if not user.is_authenticated or not user.company_id:return {}
    role=role_code(user);result={}
    if role in {'ADMIN','PLANNER','ESTIMATOR'}:
        from apps.planning.models import EstimateReviewTask
        reviews=EstimateReviewTask.objects.filter(company_id=user.company_id,status='OPEN')
        if role=='ESTIMATOR':reviews=reviews.filter(assigned_to=user)
        result.update(estimate_review_access=True,estimate_reviews_due=reviews.count())
    if role in {'ADMIN','PLANNER'}:
        from apps.planning.models import CarryoverReviewTask
        tasks=CarryoverReviewTask.objects.filter(company_id=user.company_id,due_date__lte=timezone.localdate()).exclude(correction_version__status__in=['APPROVED','COMPLETED'])
        if role!='ADMIN':tasks=tasks.filter(assigned_to=user)
        result.update(planning_task_access=True,planning_tasks_due=tasks.count())
    return result
