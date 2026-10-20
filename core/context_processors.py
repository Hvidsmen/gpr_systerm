from core.permissions import role_code


def planning_tasks(request):
    user=request.user
    if not user.is_authenticated or not user.company_id:return {}
    role=role_code(user);result={}
    from .permissions import ROLES
    if role in ROLES:
        from apps.planning.task_services import generate_daily,visible
        generate_daily(user)
        result.update(task_inbox_access=True,task_inbox_count=visible(user).filter(status__in=['OPEN','IN_REVIEW']).count())
    if role in {'ADMIN','PLANNER','ESTIMATOR'}:
        from apps.planning.models import EstimateReviewTask
        reviews=EstimateReviewTask.objects.filter(company_id=user.company_id,status='OPEN')
        if role=='ESTIMATOR':reviews=reviews.filter(assigned_to=user)
        result.update(estimate_review_access=True,estimate_reviews_due=reviews.count())
    return result
