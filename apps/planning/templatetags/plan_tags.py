from django import template

register = template.Library()


@register.filter
def get_daily_plan(plans_by_date, date):
    """Получить дневной план по дате из словаря."""
    if not date:
        return None
    date_str = date.isoformat() if hasattr(date, 'isoformat') else str(date)
    return plans_by_date.get(date_str)


@register.filter
def get_date_total(date_totals, date):
    """Получить итоги по дате из словаря."""
    if not date:
        return {'quantity': 0, 'value': 0}
    date_str = date.isoformat() if hasattr(date, 'isoformat') else str(date)
    return date_totals.get(date_str, {'quantity': 0, 'value': 0})

@register.filter
def get_value(total_by_date, date_str):
    """Получить значение из словаря total_by_date по дате."""
    if not date_str:
        return '—'
    value = total_by_date.get(date_str, 0)
    if value == 0:
        return '—'
    return value


@register.filter
def get_plan(plans_by_date, date_str):
    """Получить план из словаря plans_by_date по дате."""
    if not date_str:
        return None
    return plans_by_date.get(date_str)