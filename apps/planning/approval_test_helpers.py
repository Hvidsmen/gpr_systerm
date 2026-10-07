from apps.accounts.models import User, Role
from .global_services import GlobalPlanService


def approval_users(company):
    return {code: User.objects.create_user(username=f'approval-{company.pk}-{code}', company=company,
        role=Role.objects.get(code=code)) for code in ['PRODUCTION_HEAD', 'HR_HEAD', 'TECH_HEAD', 'CEO']}


def departments(version, users):
    for code, action in [('PRODUCTION_HEAD','approve_production'), ('HR_HEAD','approve_hr'), ('TECH_HEAD','approve_tech')]:
        version = GlobalPlanService.transition(version, users[code], action)
    return version
