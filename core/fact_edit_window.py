"""Dispatchers edit the fact date and next day; admins may edit any date."""
from datetime import timedelta
from django.core.exceptions import ValidationError
from django.utils import timezone
from .permissions import DISPATCHER_ROLES,TRANSPORT_DISPATCHER,role_code

WINDOWED_ROLES=DISPATCHER_ROLES|{TRANSPORT_DISPATCHER,'COMMANDANT'}


def edit_deadline(day):
    return day + timedelta(days=1)


def can_edit_fact_day(user, day, *, today=None):
    today = today or timezone.localdate()
    if day is None:
        return False
    if role_code(user) == 'ADMIN':
        return True
    if day > today:
        return False
    if getattr(user, 'company_id', None):
        from .models import OpenFactPeriod
        if OpenFactPeriod.objects.filter(company_id=user.company_id,date_from__lte=day,date_to__gte=day).exists():
            return True
    # Supervisory access is retained; ordinary dispatchers share the same window
    # in production, personnel, transport and the combined dispatcher role.
    return role_code(user) not in WINDOWED_ROLES or today <= edit_deadline(day)


def window_message(user, day, *, today=None):
    today = today or timezone.localdate()
    if day is None:return 'Укажите дату факта.'
    if role_code(user) == 'ADMIN':
        return ''
    if day > today:
        return 'Будущий день доступен для просмотра. Факт можно вносить начиная с даты его выполнения.'
    if getattr(user, 'company_id', None):
        from .models import OpenFactPeriod
        period=OpenFactPeriod.objects.filter(company_id=user.company_id,date_from__lte=day,date_to__gte=day).first()
        if period:
            return f'Дата входит в открытый период «{period}». Ввод и исправление факта разрешены администратором.'
    if role_code(user) not in WINDOWED_ROLES:
        return ''
    deadline = edit_deadline(day)
    if today > deadline:
        return f'Ввод и исправление факта за {day:%d.%m.%Y} закрыты с {deadline + timedelta(days=1):%d.%m.%Y}. Доступен просмотр. Для исправления обратитесь к администратору.'
    return f'Ввод и повторные исправления доступны до конца {deadline:%d.%m.%Y} включительно. Все изменения сохраняются в истории.'


def require_fact_day(user, day):
    today = timezone.localdate()
    if not can_edit_fact_day(user, day, today=today):
        raise ValidationError(window_message(user, day, today=today))


def window_context(user, day):
    today = timezone.localdate()
    return {'fact_day_editable': can_edit_fact_day(user, day, today=today), 'fact_edit_deadline': edit_deadline(day), 'fact_window_message': window_message(user, day, today=today)}
