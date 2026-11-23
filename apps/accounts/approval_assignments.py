"""Company approval-stage owners and audited manual replacement."""
from collections import OrderedDict

from django.core.exceptions import ValidationError
from django.db import transaction

from core.permissions import role_code
from .models import ApprovalAssignment, ApprovalAssignmentChange, User


MOVEMENT_STAGES = OrderedDict([
    ('MANAGER', ('Руководитель проекта', 'MANAGER')),
    ('SITE_CHIEF', ('Начальник участка', 'SITE_CHIEF')),
    ('PEREVAKHTA_SPECIALIST', ('Специалист перевахты', 'PEREVAKHTA_SPECIALIST')),
    ('DUDINKA_HEAD', ('Руководитель ОП Дудинка', 'DUDINKA_HEAD')),
    ('DUDINKA_SECURITY_DEPUTY', ('Заместитель руководителя ОП по безопасности', 'DUDINKA_SECURITY_DEPUTY')),
    ('CHIEF_MECHANIC', ('Главный механик', 'CHIEF_MECHANIC')),
    ('PARAMEDIC', ('Фельдшер', 'PARAMEDIC')),
    ('PERSONNEL_SPECIALIST', ('Специалист по персоналу', 'PERSONNEL_SPECIALIST')),
    ('LABOR_ECONOMIST', ('Эксперт по экономике труда и заработной плате', 'LABOR_ECONOMIST')),
])
PLAN_STAGES = OrderedDict([
    ('PRODUCTION', ('Работы', 'PRODUCTION_HEAD')),
    ('HR', ('Должности', 'HR_HEAD')),
    ('TECH', ('Техника и ГСМ', 'TECH_HEAD')),
    ('SECURITY', ('Служба безопасности', 'SECURITY_HEAD')),
    ('SBO', ('Служба СБО', 'SBO_HEAD')),
    ('CEO', ('Финальное утверждение', 'CEO')),
])
PROCESS_STAGES = OrderedDict([
    ('MOVEMENT', ('Заявки на заезд и выезд', MOVEMENT_STAGES)),
    ('GPR', ('ГПР', PLAN_STAGES)),
    ('SMG', ('СМГ', PLAN_STAGES)),
])


def stage(process, stage_code):
    try:return PROCESS_STAGES[process][1][stage_code]
    except KeyError:raise ValidationError('Неизвестный этап согласования.')


def assignment(company, process, stage_code):
    return ApprovalAssignment.objects.select_related('user__role').filter(
        company=company, process=process, stage_code=stage_code).first()


def assignee(company, process, stage_code, expected_role=None):
    """Explicit owner first; a single role holder is the backwards-compatible fallback."""
    expected_role=expected_role or stage(process,stage_code)[1]
    row=assignment(company,process,stage_code)
    if row:
        valid=row.user.is_active and row.user.company_id==company.pk and row.user.archived_at is None and role_code(row.user) in {expected_role,'ADMIN'}
        return row.user if valid else None
    people=list(User.objects.filter(company=company,is_active=True,archived_at__isnull=True,role__code=expected_role).order_by('pk')[:2])
    return people[0] if len(people)==1 else None


def authorized(user, process, stage_code, expected_role=None):
    if role_code(user)=='ADMIN':return True
    expected_role=expected_role or stage(process,stage_code)[1]
    row=assignment(user.company,process,stage_code)
    if row:return row.user_id==user.pk and row.user.is_active and role_code(user)==expected_role
    return role_code(user)==expected_role


@transaction.atomic
def replace(*, company, process, stage_code, user, actor, reason=''):
    label,expected_role=stage(process,stage_code)
    if user.company_id!=company.pk or not user.is_active or user.archived_at is not None:
        raise ValidationError('Выберите активного пользователя своей компании.')
    if role_code(user) not in {expected_role,'ADMIN'}:
        raise ValidationError(f'Для этапа «{label}» нужен пользователь с ролью «{expected_role}» или администратор.')
    current=ApprovalAssignment.objects.select_for_update().filter(company=company,process=process,stage_code=stage_code).first()
    previous=current.user if current else None
    if previous and previous.pk==user.pk:return current
    reason=reason.strip()
    if previous and not reason:raise ValidationError('При замене согласующего укажите причину.')
    if current:
        current.user=user;current.changed_by=actor;current.save(update_fields=['user','changed_by','updated_at'])
    else:
        current=ApprovalAssignment.objects.create(company=company,process=process,stage_code=stage_code,user=user,changed_by=actor)
    ApprovalAssignmentChange.objects.create(assignment=current,previous_user=previous,new_user=user,changed_by=actor,reason=reason)
    return current


def sync_active(company, process):
    """Apply a replacement to open tasks without changing completed decisions."""
    if process in {'GPR','SMG'}:
        from apps.planning.task_services import sync_company
        sync_company(company);return
    from apps.personnel.models import MovementReviewRequest
    from apps.personnel.departure_workflow import sync_approval_task
    for row in MovementReviewRequest.objects.filter(company=company,status__in=['PENDING_APPROVAL','RETURNED']).iterator():
        sync_approval_task(row)
