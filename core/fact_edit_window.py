"""Dispatcher actual entry stays open on the fact date and the next local day."""
from datetime import timedelta
from django.core.exceptions import ValidationError
from django.utils import timezone
from .permissions import DISPATCHER_ROLES, role_code


def edit_deadline(day):
    return day + timedelta(days=1)


def can_edit_fact_day(user, day, *, today=None):
    today = today or timezone.localdate()
    if day is None or day > today:
        return False
    # Supervisory access is retained; ordinary dispatchers share the same window
    # in production, personnel, transport and the combined dispatcher role.
    return role_code(user) not in DISPATCHER_ROLES or today <= edit_deadline(day)


def window_message(user, day, *, today=None):
    today = today or timezone.localdate()
    if day is None:return 'Укажите дату факта.'
    if day > today:
        return 'Будущий день доступен для просмотра. Факт можно вносить начиная с даты его выполнения.'
    if role_code(user) not in DISPATCHER_ROLES:
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
