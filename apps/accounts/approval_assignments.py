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


def _object_path(company, construction_object):
    """Return the selected object followed by its parents, most specific first."""
    if not construction_object:return []
    from apps.projects.models import ConstructionObject
    current=(construction_object if hasattr(construction_object,'parent_id') else
        ConstructionObject.objects.filter(company=company,pk=construction_object).select_related('parent').first())
    result=[];seen=set()
    while current and current.pk not in seen:
        seen.add(current.pk);result.append(current.pk)
        current=(current.parent if hasattr(current,'parent') else
            ConstructionObject.objects.filter(company=company,pk=current.parent_id).select_related('parent').first()) if current.parent_id else None
    return result


def assignment(company, process, stage_code, *, construction_object=None, camp=None):
    """Resolve the most specific rule that matches the approval context."""
    object_path=_object_path(company,construction_object);camp_id=getattr(camp,'pk',camp)
    rows=list(ApprovalAssignment.objects.select_related('user__role','construction_object','camp').filter(
        company=company,process=process,stage_code=stage_code,is_active=True))
    ranked=[]
    for row in rows:
        if row.construction_object_id and row.construction_object_id not in object_path:continue
        if row.camp_id and row.camp_id!=camp_id:continue
        if row.camp_id and not camp_id:continue
        if row.construction_object_id:
            object_rank=len(object_path)-object_path.index(row.construction_object_id)
            base=300 if row.camp_id else 200
        else:
            object_rank=0;base=100 if row.camp_id else 0
        ranked.append((base+object_rank,-row.pk,row))
    return max(ranked,default=(0,0,None))[2]


def assignee(company, process, stage_code, expected_role=None, *, construction_object=None, camp=None):
    """Explicit owner first; a single role holder is the backwards-compatible fallback."""
    expected_role=expected_role or stage(process,stage_code)[1]
    row=assignment(company,process,stage_code,construction_object=construction_object,camp=camp)
    if row:
        valid=row.user.is_active and row.user.company_id==company.pk and row.user.archived_at is None and role_code(row.user) in {expected_role,'ADMIN'}
        return row.user if valid else None
    people=list(User.objects.filter(company=company,is_active=True,archived_at__isnull=True,role__code=expected_role).order_by('pk')[:2])
    return people[0] if len(people)==1 else None


def authorized(user, process, stage_code, expected_role=None, *, construction_object=None, camp=None):
    if role_code(user)=='ADMIN':return True
    expected_role=expected_role or stage(process,stage_code)[1]
    row=assignment(user.company,process,stage_code,construction_object=construction_object,camp=camp)
    if row:return row.user_id==user.pk and row.user.is_active and role_code(user)==expected_role
    return role_code(user)==expected_role


@transaction.atomic
def replace(*, company, process, stage_code, user, actor, reason='', construction_object=None, camp=None):
    label,expected_role=stage(process,stage_code)
    if camp and process!='MOVEMENT':
        raise ValidationError('Условие по местонахождению применяется только к заявкам на заезд и выезд.')
    if user.company_id!=company.pk or not user.is_active or user.archived_at is not None:
        raise ValidationError('Выберите активного пользователя своей компании.')
    if role_code(user) not in {expected_role,'ADMIN'}:
        raise ValidationError(f'Для этапа «{label}» нужен пользователь с ролью «{expected_role}» или администратор.')
    if construction_object and construction_object.company_id!=company.pk:raise ValidationError('Строительный объект другой компании.')
    if camp and camp.company_id!=company.pk:raise ValidationError('Местонахождение другой компании.')
    current=ApprovalAssignment.objects.select_for_update().filter(company=company,process=process,stage_code=stage_code,
        construction_object=construction_object,camp=camp).first()
    previous=current.user if current and current.is_active else None
    if previous and previous.pk==user.pk:return current
    reason=reason.strip()
    if previous and not reason:raise ValidationError('При замене согласующего укажите причину.')
    if current:
        current.user=user;current.is_active=True;current.changed_by=actor
        current.save(update_fields=['user','is_active','changed_by','updated_at'])
    else:
        current=ApprovalAssignment.objects.create(company=company,process=process,stage_code=stage_code,
            construction_object=construction_object,camp=camp,user=user,changed_by=actor)
    ApprovalAssignmentChange.objects.create(assignment=current,previous_user=previous,new_user=user,changed_by=actor,reason=reason)
    return current


@transaction.atomic
def disable(*, company, assignment_id, actor, reason):
    """Disable one scoped rule without deleting its audited history."""
    current=ApprovalAssignment.objects.select_for_update().filter(company=company,pk=assignment_id,is_active=True).first()
    if current is None:raise ValidationError('Условие уже удалено или не найдено.')
    reason=reason.strip()
    if not reason:raise ValidationError('Укажите причину удаления условия.')
    previous=current.user;current.is_active=False;current.changed_by=actor
    current.save(update_fields=['is_active','changed_by','updated_at'])
    ApprovalAssignmentChange.objects.create(assignment=current,previous_user=previous,new_user=None,
        changed_by=actor,reason=reason)
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
