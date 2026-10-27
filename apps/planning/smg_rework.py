"""Explicit administrator return of an operative SMG to the same draft revision."""
import json
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.serializers.json import DjangoJSONEncoder
from django.db import transaction
from django.utils.dateparse import parse_datetime
from core.permissions import require_roles
from apps.audit.models import AuditLog
from apps.projects.models import ConstructionObject
from .models import DailySchedule, DailyScheduleReview, DailyScheduleDecision


@transaction.atomic
def return_applied(user, schedule, comment, expected_version):
    require_roles(user, {'ADMIN'})
    if schedule.company_id != user.company_id:
        raise PermissionDenied
    # Application, revision creation and return serialize on the same object.
    ConstructionObject.objects.select_for_update().get(pk=schedule.construction_object_id, company=user.company)
    schedule = DailySchedule.objects.select_for_update().get(pk=schedule.pk, company=user.company)
    if schedule.status != 'APPLIED':
        raise ValidationError('Вернуть в доработку можно только применённый СМГ. Обновите страницу.')
    try:
        version = parse_datetime(expected_version or '')
    except (ValueError, TypeError):
        version = None
    if version != schedule.updated_at:
        raise ValidationError('СМГ уже изменён. Обновите страницу перед возвратом в доработку.')
    comment = comment.strip()
    if not comment or len(comment) > 4000:
        raise ValidationError('Укажите причину возврата в доработку, до 4000 символов.')
    other = DailySchedule.objects.filter(company=user.company, construction_object_id=schedule.construction_object_id,
        month=schedule.month, status__in=['DRAFT', 'SUBMITTED', 'APPROVED']).exclude(pk=schedule.pk).first()
    if other:
        raise ValidationError(f'На этот месяц уже есть незавершённая редакция СМГ № {other.revision}. Откройте её в списке СМГ.')

    from .daily_schedule_approval import current, planner, sync
    from .resource_targets import assignment_values
    from .resource_links import binding_values
    from . import preparation, estimate_review
    from .task_services import ensure
    entries = list(schedule.entries.order_by('pk').values('id', 'key', 'kind', 'label', 'gpr_work_id', 'metadata', 'original', 'rows', 'revision'))
    before = {'status': schedule.status, 'revision': schedule.revision, 'approval_round': schedule.approval_round,
        'source_id': schedule.source_id, 'locked_before': schedule.locked_before, 'entries': entries,
        'assignments': assignment_values(schedule), 'links': binding_values(schedule)}
    review = current(schedule)
    if review is None:
        # Legacy applied plans may predate approval rounds. Preserve their inputs too.
        schedule.approval_round += 1
        review = DailyScheduleReview.objects.create(company=user.company, schedule=schedule, number=schedule.approval_round,
            fingerprint=estimate_review.signature(schedule), snapshot=entries, submitted_by=user)
    DailyScheduleDecision.objects.create(company=user.company, review=review, section='ADMIN', action='REJECT',
        actor=user, actor_name=user.get_full_name() or user.username, actor_role='ADMIN', comment=comment)
    latest = schedule.estimate_reviews.order_by('-pk').first()
    if latest and latest.status == 'APPROVED':
        estimate_review.event(latest, user, 'APPLIED_RETURNED', {'comment': comment, 'previous_status': latest.status,
            'previous_fingerprint': latest.fingerprint, 'approved_at': latest.approved_at.isoformat() if latest.approved_at else None})
        latest.status = 'REJECTED'; latest.fingerprint = ''; latest.comment = comment
        latest.save(update_fields=['status', 'fingerprint', 'comment', 'updated_at'])
    prepared = preparation.record(schedule)
    if prepared:
        prepared.resources_ready_at = None; prepared.resources_fingerprint = ''
        prepared.save(update_fields=['resources_ready_at', 'resources_fingerprint', 'updated_at'])
    schedule.status = 'DRAFT'
    schedule.save(update_fields=['status', 'approval_round', 'updated_at'])
    sync(schedule)
    ensure(schedule.company, f'smg-repair:{schedule.pk}:{schedule.approval_round}', kind='MANUAL',
        title='Доработать СМГ и повторно согласовать', description=comment, assigned_to=planner(schedule, latest) if latest else schedule.created_by,
        created_by=user, schedule=schedule, construction_object_id=schedule.construction_object_id)
    AuditLog.objects.create(user=user, action='REJECT', entity_type='planning.dailyschedule', entity_id=str(schedule.pk),
        old_value=json.loads(json.dumps(before, cls=DjangoJSONEncoder)), new_value={'status': 'DRAFT', 'reason': comment},
        comment='Администратор вернул применённый СМГ в доработку')
    return schedule
