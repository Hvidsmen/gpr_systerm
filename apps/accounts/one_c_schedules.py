"""Independent catalogue schedules sharing one serialized 1C connection."""
from datetime import datetime, time, timedelta
from django.db.models import Q
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from .models import OneCConnection


CATALOGUES = (
    {'kind': 'employees', 'label': 'сотрудников', 'title': 'Обновление справочника сотрудников',
     'source': 'Справочник.Сотрудники и кадровые регистры ЗУП',
     'description': 'Обновляются кадровые сведения, табельные номера, должности и статус активности. Ручные записи и связи сотрудников сохраняются.'},
    {'kind': 'positions', 'label': 'должностей', 'title': 'Обновление справочника должностей',
     'source': 'Справочник.ШтатноеРасписание ЗУП',
     'description': 'Только утверждённые и незакрытые позиции штатного расписания без пометки удаления. Наименования должностей выделяются из штатных позиций. Существующие ставки и настройки сохраняются.'},
)

WEEKDAYS = tuple(enumerate(('Понедельник', 'Вторник', 'Среда', 'Четверг', 'Пятница', 'Суббота', 'Воскресенье')))


def local_launch(day, clock):
    return timezone.make_aware(datetime.combine(day, clock), timezone.get_default_timezone())


def first_launch(frequency, clock, weekday, now):
    local = timezone.localtime(now, timezone.get_default_timezone())
    day = local.date()
    if frequency == 'weekly':
        day += timedelta(days=(weekday - day.weekday()) % 7)
    candidate = local_launch(day, clock)
    if candidate < now:
        candidate = local_launch(day + timedelta(days=7 if frequency == 'weekly' else 1), clock)
    return candidate


def schedule_initial(connection, kind):
    interval = getattr(connection, kind + '_interval_minutes')
    schedule = getattr(connection, kind + '_schedule')
    return {'frequency': schedule.get('frequency', 'interval' if interval else 'manual'),
            'time': schedule.get('time', '09:00'), 'weekday': schedule.get('weekday', 0),
            'interval': interval or 60}


def schedule_label(connection, kind):
    interval = getattr(connection, kind + '_interval_minutes')
    if not interval:
        return 'Только вручную'
    schedule = getattr(connection, kind + '_schedule')
    frequency = schedule.get('frequency')
    if frequency == 'daily':
        return f"Ежедневно в {schedule['time']}"
    if frequency == 'weekly':
        return f"Еженедельно · {dict(WEEKDAYS)[schedule['weekday']].lower()} · {schedule['time']}"
    if frequency == 'interval':
        return f"Каждые {interval} мин. · отсчёт от {schedule['time']}"
    return f'Каждые {interval} мин. от последнего запуска'


def scheduled_deadline(connection, kind, now, previous=None):
    """Return one due slot or the next slot; never replay every missed occurrence."""
    interval = getattr(connection, kind + '_interval_minutes')
    if not interval:
        return None
    schedule = getattr(connection, kind + '_schedule')
    if not schedule:
        # Existing interval schedules keep their cadence until explicitly edited.
        return previous.created_at + timedelta(minutes=interval) if previous else now
    start = parse_datetime(schedule['starts_at'])
    if now < start:
        return start
    frequency = schedule['frequency']
    if frequency == 'interval':
        step = timedelta(minutes=interval)
        slot = start + ((now - start) // step) * step
        return slot + step if previous and previous.created_at >= slot else slot
    clock = time.fromisoformat(schedule['time'])
    local = timezone.localtime(now, timezone.get_default_timezone())
    day = local.date()
    days = 7 if frequency == 'weekly' else 1
    if frequency == 'weekly':
        day -= timedelta(days=(day.weekday() - schedule['weekday']) % 7)
    slot = local_launch(day, clock)
    if slot > now:
        day -= timedelta(days=days)
        slot = local_launch(day, clock)
    if slot < start:
        return start
    if previous and previous.created_at >= slot:
        return local_launch(day + timedelta(days=days), clock)
    return slot


def source_ready(connection, kind):
    if kind == 'employees':
        from .one_c_employees import source_confirmed
    else:
        from .one_c_positions import source_confirmed
    return source_confirmed(connection)


def last_run(connection, kind):
    return connection.runs.filter(
        server=connection.server, database=connection.database,
        **{'report__' + kind + '_import': True},
    ).order_by('-created_at', '-pk').first()


def enqueue_due_imports(now, kinds=('employees', 'positions')):
    from .one_c import enqueue
    enabled = Q()
    for kind in kinds:
        enabled |= Q(**{kind + '_interval_minutes__gt': 0})
    if not kinds:
        return
    for connection in OneCConnection.objects.filter(enabled, active_until__isnull=True):
        due = []
        for kind in kinds:
            interval = getattr(connection, kind + '_interval_minutes')
            if not interval:
                continue
            previous = last_run(connection, kind)
            deadline = scheduled_deadline(connection, kind, now, previous)
            if deadline <= now and source_ready(connection, kind):
                due.append((deadline, kind))
        # Run the oldest due slot first.
        # The shared conditional claim in enqueue prevents parallel jobs.
        due.sort(key=lambda row: (row[0] is not None, row[0] or now, row[1]))
        if due:
            enqueue(connection, **{due[0][1]: True})
