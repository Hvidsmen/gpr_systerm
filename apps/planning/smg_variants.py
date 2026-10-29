"""One approved/operative SMG per object/month; replacement requires explicit cancellation."""
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils.dateparse import parse_datetime
from core.permissions import require_roles, role_code
from .models import DailySchedule, DailyScheduleDecision


def require_available(schedule):
    """Caller holds the construction-object lock; never cancel another plan implicitly."""
    other = DailySchedule.objects.select_for_update().filter(company=schedule.company,
        construction_object_id=schedule.construction_object_id, month=schedule.month,
        status__in=['APPROVED', 'APPLIED']).exclude(pk=schedule.pk).first()
    if other:
        raise ValidationError(f'На этот месяц уже утверждён СМГ «{other.name or "Вариант"}» №{other.revision}. Сначала явно отмените его утверждение. Новый вариант и действующий план сохранены.')
    prior = DailySchedule.objects.filter(company=schedule.company, construction_object_id=schedule.construction_object_id,
        month=schedule.month, status__in=['CANCELLED','ARCHIVED'], locked_before__isnull=False).exclude(pk=schedule.pk).order_by('-locked_before','-updated_at').first()
    if prior:
        require_closed_history(prior,schedule)


@transaction.atomic
def cancel_approval(user, schedule, comment, expected):
    require_roles(user, {'ADMIN','CEO'})
    if schedule.company_id != user.company_id:
        raise PermissionDenied
    from apps.projects.models import ConstructionObject
    ConstructionObject.objects.select_for_update().get(pk=schedule.construction_object_id,company=user.company)
    schedule=DailySchedule.objects.select_for_update().select_related('source').defer('source__snapshot').get(pk=schedule.pk,company=user.company)
    if schedule.status not in {'APPROVED','APPLIED'}:
        raise ValidationError('Отменить можно только утверждённый или применённый СМГ.')
    try:
        version=parse_datetime(expected or '')
    except (TypeError,ValueError):
        version=None
    if version != schedule.updated_at:
        raise ValidationError('СМГ изменился. Обновите страницу отмены перед подтверждением.')
    if not comment.strip() or len(comment.strip())>4000:
        raise ValidationError('Укажите причину отмены, до 4000 символов.')
    from .daily_schedule_approval import current, sync
    from apps.audit.models import AuditLog
    review=current(schedule)
    previous=schedule.status
    if review:
        DailyScheduleDecision.objects.create(company=user.company,review=review,section=role_code(user),action='CANCEL',
            actor=user,actor_name=user.get_full_name() or user.username,actor_role=role_code(user),comment=comment.strip())
    schedule.status='CANCELLED'
    schedule.save(update_fields=['status','updated_at'])
    sync(schedule)
    AuditLog.objects.create(user=user,action='UPDATE',entity_type='planning.dailyschedule',entity_id=str(schedule.pk),
        old_value={'status':previous},new_value={'status':'CANCELLED','reason':comment.strip()},comment='Явная отмена утверждения СМГ')
    return schedule


def require_closed_history(current, candidate):
    from .smg_comparison import quantities
    cutoff=current.locked_before
    first,second=quantities(current),quantities(candidate)
    for key in first.keys() | second.keys():
        old,new=first.get(key),second.get(key)
        old_values,new_values=old['values'] if old else {},new['values'] if new else {}
        for day in old_values.keys() | new_values.keys():
            if day < cutoff and old_values.get(day,0) != new_values.get(day,0):
                raise ValidationError(f'Вариант меняет закрытые дни до {cutoff:%d.%m.%Y}. Создайте копию прежнего СМГ и изменяйте только открытые дни. Новый вариант сохранён без утверждения.')
