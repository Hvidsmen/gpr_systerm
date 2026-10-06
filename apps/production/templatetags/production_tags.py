from django import template

register = template.Library()


@register.filter
def multiply(value, arg):
    """Умножение: value * arg"""
    try:
        return float(value) * float(arg)
    except (ValueError, TypeError):
        return 0


@register.filter
def divide(value, arg):
    """Деление: value / arg"""
    try:
        arg = float(arg)
        if arg == 0:
            return 0
        return float(value) / arg
    except (ValueError, TypeError):
        return 0


@register.filter
def subtract(value, arg):
    """Вычитание: value - arg"""
    try:
        return float(value) - float(arg)
    except (ValueError, TypeError):
        return 0


@register.filter
def add(value, arg):
    """Сложение: value + arg"""
    try:
        return float(value) + float(arg)
    except (ValueError, TypeError):
        return 0