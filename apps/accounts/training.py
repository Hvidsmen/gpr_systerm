"""Control files and task links, with no ORM routing to a training database."""
from datetime import datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
from django.conf import settings
from django.core.exceptions import PermissionDenied, ImproperlyConfigured
from django.utils import timezone
from ops.windows_runtime import atomic_json, read_json

MOSCOW = ZoneInfo('Europe/Moscow')


def reset_slot(now):
    local = now.astimezone(MOSCOW)
    slot = datetime.combine(local.date(), time(7), MOSCOW)
    return slot if now >= slot else slot - timedelta(days=1)


def next_reset(now):
    return reset_slot(now) + timedelta(days=1)


def root():
    value = Path(settings.TRAINING_ROOT)
    if not value.is_absolute():
        raise ImproperlyConfigured('Хранилище учебных копий должно иметь абсолютный путь.')
    value = value.resolve()
    for public in [settings.MEDIA_ROOT, settings.STATIC_ROOT, *settings.STATICFILES_DIRS]:
        if value.is_relative_to(Path(public).resolve()):
            raise ImproperlyConfigured('Учебные базы нельзя размещать в публичных media/static.')
    return value


def folder(copy):
    # UUID model primary keys are the only path components accepted here.
    return root() / str(copy.pk)


def member_ids(copy):
    return list(copy.members.filter(company_id=copy.company_id, is_active=True, archived_at=None).values_list('pk', flat=True))


def write_access(copy):
    target = folder(copy)
    target.mkdir(parents=True, exist_ok=True)
    # This manifest is outside the cloned DB. Copied admin screens cannot edit it.
    atomic_json(target / 'access.json', {'copy': str(copy.pk), 'company': copy.company_id,
        'name': copy.name, 'members': member_ids(copy), 'enabled': copy.enabled})


def permitted(user, copy):
    return (not getattr(settings, 'TRAINING_MODE', False) and user.is_active and
        user.company_id == copy.company_id and copy.enabled and
        copy.members.filter(pk=user.pk, company_id=copy.company_id, is_active=True, archived_at=None).exists())


def require_member(user, copy):
    if not permitted(user, copy):
        raise PermissionDenied('Эта учебная копия доступна только активным участникам её группы.')


def sync_tasks(copy):
    from apps.planning.models import WorkTask
    from apps.planning.task_services import ensure, state, ACTIVE
    ids = member_ids(copy) if copy.enabled else []
    prefix = f'training:{copy.pk}:'
    for task in WorkTask.objects.filter(company_id=copy.company_id, key__startswith=prefix, status__in=ACTIVE).exclude(assigned_to_id__in=ids):
        state(task, 'SUPERSEDED', text='Участие в учебной группе завершено.')
    for user in copy.members.filter(pk__in=ids).select_related('role'):
        task = ensure(copy.company, prefix + str(user.pk), kind='TRAINING',
            title=f'Учебная копия · {copy.name}', assigned_to=user,
            target_role=user.role.code if user.role_id else '', created_by=copy.created_by,
            description='Откройте учебный портал по кнопке в задании. Внутри вы администратор своей учебной группы. '
                'Общая база группы ежедневно заменяется данными основного портала в 07:00 по Москве. '
                'Учебные изменения и файлы при этом удаляются. Основные данные не меняются.')
        if task.status == 'SUPERSEDED':
            state(task, 'OPEN', text='Доступ к учебной группе возобновлён.')


def queue(copy):
    copy.requested_at = timezone.now()
    copy.state = 'QUEUED' if copy.enabled else 'STOPPED'
    copy.error = ''
    copy.save(update_fields=['requested_at','state','error','updated_at'])
    write_access(copy)
    sync_tasks(copy)


def available(copy):
    manifest = read_json(folder(copy) / 'current.json', {})
    return copy.state == 'READY' and bool(manifest.get('generation'))
