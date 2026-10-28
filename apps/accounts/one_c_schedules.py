"""Independent catalogue schedules sharing one serialized 1C connection."""
from datetime import timedelta
from django.db.models import Q
from .models import OneCConnection


CATALOGUES = (
    {'kind': 'employees', 'label': 'сотрудников', 'title': 'Обновление справочника сотрудников',
     'source': 'Справочник.Сотрудники и кадровые регистры ЗУП',
     'description': 'Обновляются кадровые сведения, табельные номера, должности и статус активности. Ручные записи и связи сотрудников сохраняются.'},
    {'kind': 'positions', 'label': 'должностей', 'title': 'Обновление справочника должностей',
     'source': 'Справочник.ШтатноеРасписание ЗУП',
     'description': 'Только утверждённые и незакрытые позиции штатного расписания без пометки удаления. Наименования должностей выделяются из штатных позиций. Существующие ставки и настройки сохраняются.'},
)


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
            deadline = previous.created_at + timedelta(minutes=interval) if previous else None
            if (deadline is None or deadline <= now) and source_ready(connection, kind):
                due.append((deadline, kind))
        # A catalogue never imported before goes first; then the oldest deadline.
        # The shared conditional claim in enqueue prevents parallel jobs.
        due.sort(key=lambda row: (row[0] is not None, row[0] or now, row[1]))
        if due:
            enqueue(connection, **{due[0][1]: True})
