from apps.accounts.models import User, Role
from .global_services import GlobalPlanService


def approval_users(company):
    return {code: User.objects.create_user(username=f'approval-{company.pk}-{code}', company=company,
        role=Role.objects.get(code=code)) for code in ['PRODUCTION_HEAD', 'HR_HEAD', 'TECH_HEAD', 'SECURITY_HEAD', 'CEO']}


def departments(version, users):
    for code, action in [('PRODUCTION_HEAD','approve_production'), ('HR_HEAD','approve_hr'), ('TECH_HEAD','approve_tech'), ('SECURITY_HEAD','approve_security')]:
        version = GlobalPlanService.transition(version, users[code], action)
    return version


def estimator_check(obj, planner):
    """Exercise the real mandatory pre-review in workflow fixtures."""
    from . import estimate_review
    from apps.works.models import ProjectWork
    estimator,_=User.objects.get_or_create(username=f'estimator-{planner.company_id}',defaults={'company':planner.company,'role':Role.objects.get(code='ESTIMATOR')})
    from .preparation import handoff,ready
    manager=User.objects.filter(company=planner.company,role__code='MANAGER',is_active=True).first()
    if not manager:manager=User.objects.create_user(username=f'project-manager-{planner.company_id}',company=planner.company,role=Role.objects.get(code='MANAGER'))
    handoff(planner,obj,manager,estimator)
    ready(manager,obj)
    task=obj.estimate_reviews.order_by('-pk').first()
    for work in ProjectWork.objects.filter(pk__in=estimate_review.work_ids(obj)):
        task=estimate_review.review_work(estimator,task,work.pk,True)
    return estimate_review.finish(estimator,task,True)
