from django import template

register = template.Library()


@register.filter
def subtract(value, arg):
    """Вычитание: value - arg"""
    try:
        return float(value) - float(arg)
    except (ValueError, TypeError):
        return 0


@register.filter
def sum_percentages(items):
    """Суммирует проценты элементов профиля."""
    try:
        return sum(item.percentage for item in items)
    except (TypeError, AttributeError):
        return 0



@register.filter
def get_item(form, field_name):
    """Получить поле формы по имени."""
    try:
        return form[field_name]
    except KeyError:
        return None