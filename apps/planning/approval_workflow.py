"""Company-scoped, serialized decisions on immutable review rounds."""
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone
from core.permissions import approval_role, require_global_action
from .approval_context import authorized_transition
from .models import GlobalPlanVersion, GlobalPlanReview, GlobalPlanDecision

SECTIONS = [('PRODUCTION', 'Работы', 'PRODUCTION_HEAD', 'approve_production'),
            ('HR', 'Люди', 'HR_HEAD', 'approve_hr'),
            ('TECH', 'Техника и ГСМ', 'TECH_HEAD', 'approve_tech')]


def current_review(version):
    return version.review_rounds.filter(number=version.approval_round).first()


def approved_sections(review):
    return set(review.decisions.filter(action='APPROVE').values_list('section', flat=True)) if review else set()


def ready(review):
    return {'ESTIMATE', 'PRODUCTION', 'HR', 'TECH'} <= approved_sections(review)


def baseline_snapshot(version):
    from apps.resources.brigade_merge import normalize_resource_snapshot
    snapshot = version.baseline_review.snapshot if version.baseline_review_id else version.workspace.baseline_version.snapshot
    return normalize_resource_snapshot(snapshot, version.company)


@transaction.atomic
def transition(version, user, action, comment=''):
    require_global_action(user, action)
    version = GlobalPlanVersion.objects.select_for_update().get(pk=version.pk)
    if version.company_id != user.company_id:
        raise PermissionDenied('Версия другой компании.')
    if version.status == 'COMPLETED':
        raise ValidationError('План завершён. Изменение статуса запрещено.')
    comment = comment.strip()
    if len(comment) > 4000:
        raise ValidationError('Комментарий не должен превышать 4000 символов.')
    role = approval_role(user)
    review = current_review(version)
    section = dict((r, s) for s, _, r, _ in SECTIONS).get(role, 'CEO')
    if action in {s[3] for s in SECTIONS}:
        section = {a: s for s, _, _, a in SECTIONS}[action]
    event = 'APPROVE'
    if action == 'submit':
        if version.status not in ['DRAFT', 'REJECTED']:
            raise ValidationError('На согласование отправляется только черновик или план на доработке.')
        from .estimate_review import require_approved, signature
        submitted_fingerprint = signature(version)
        try:
            estimator_review = require_approved(version)
        except ValidationError:
            estimator_review = None
        from .global_services import build_snapshot
        version.snapshot = build_snapshot(version)
        version.approval_round += 1
        review = GlobalPlanReview.objects.create(company=version.company, version=version,
            number=version.approval_round, snapshot=version.snapshot, submitted_by=user, input_fingerprint=submitted_fingerprint)
        if estimator_review:
            GlobalPlanDecision.objects.create(company=version.company, review=review, section='ESTIMATE',
                action='APPROVE', actor=estimator_review.assigned_to,
                actor_name=estimator_review.assigned_to.get_full_name() or estimator_review.assigned_to.username,
                actor_role='ESTIMATOR', comment=estimator_review.comment or 'Цены, названия и состав работ проверены.')
        version.status = 'SUBMITTED'
        version.approved_by = None
        version.approved_at = None
        version.comment = comment
        section, event = 'PLANNER', 'SUBMIT'
    elif not review:
        raise ValidationError('У плана отсутствует раунд согласования. Выполните миграции базы.')
    elif action in {s[3] for s in SECTIONS}:
        if version.status != 'SUBMITTED':
            raise ValidationError('Согласовать раздел можно только у отправленного плана.')
        if section in approved_sections(review):
            raise ValidationError('Раздел уже согласован в этом раунде.')
    elif action == 'approve':
        if version.status != 'SUBMITTED' or not ready(review):
            raise ValidationError('Требуются согласования сметчика и всех трех профильных служб.')
        from .estimate_review import require_approved
        require_approved(version)
        version.status = 'APPROVED'
        version.approved_by = user
        version.approved_at = timezone.now()
    elif action == 'reject':
        permitted = version.status == 'SUBMITTED' and (role != 'CEO' or ready(review))
        permitted |= version.status == 'APPROVED' and role in {'CEO', 'ADMIN'}
        if not permitted:
            raise ValidationError('На этом этапе возврат на доработку недоступен.')
        if not comment:
            raise ValidationError('Укажите причину возврата на доработку.')
        event = 'REJECT'
        version.status = 'REJECTED'
        version.comment = comment
        version.approved_by = None
        version.approved_at = None
    elif action == 'complete':
        if version.status != 'APPROVED':
            raise ValidationError('Завершить можно только полностью согласованный план.')
        event = 'COMPLETE'
        version.status = 'COMPLETED'
    else:
        raise ValidationError('Неизвестное действие согласования.')
    GlobalPlanDecision.objects.create(company=version.company, review=review, section=section,
        action=event, actor=user, actor_name=user.get_full_name() or user.username,
        actor_role=role, comment=comment)
    with authorized_transition(version.pk):
        version.save()
    from .task_services import sync_version,create_repair,state
    sync_version(version)
    if action=='reject':create_repair(version,user,comment)
    if action=='submit':
        from .models import WorkTask
        for task in WorkTask.objects.filter(version=version,key__startswith='repair:',status__in=['OPEN','IN_REVIEW']):state(task,'DONE',user,'Исправленный план отправлен на новый раунд согласования.')
    return version


def panel(version, user):
    review = current_review(version)
    approved = approved_sections(review)
    role = approval_role(user)
    submitted = version.status == 'SUBMITTED'
    sections = []
    estimator_decision = review.decisions.filter(section='ESTIMATE', action='APPROVE').first() if review else None
    sections.append({'label':'Сметчик — цены и состав работ','decision':estimator_decision,'approved':bool(estimator_decision) and version.status not in {'DRAFT','REJECTED'},'can_approve':False})
    for section, label, assigned, action in SECTIONS:
        decision = review.decisions.filter(section=section, action='APPROVE').first() if review else None
        sections.append({'label': label, 'decision': decision, 'approved': section in approved and version.status not in ['DRAFT', 'REJECTED'],
                         'action': action, 'can_approve': submitted and role in {assigned, 'ADMIN'} and not decision})
    return {'legacy_review': bool(review and review.legacy_approved and version.status not in ['DRAFT', 'REJECTED']), 'approval_sections': sections, 'approval_ready': ready(review),
            'can_final_approve': submitted and role in {'CEO', 'ADMIN'} and ready(review),
            'can_return': (submitted and (role in {'PRODUCTION_HEAD', 'HR_HEAD', 'TECH_HEAD', 'ADMIN'} or role == 'CEO' and ready(review))) or version.status == 'APPROVED' and role in {'CEO', 'ADMIN'},
            'can_complete': version.status == 'APPROVED' and role in {'CEO', 'ADMIN'},
            'review_history': version.review_rounds.prefetch_related('decisions').order_by('-number')}
