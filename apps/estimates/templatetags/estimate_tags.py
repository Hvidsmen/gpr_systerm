from decimal import Decimal, InvalidOperation
from django import template

register = template.Library()


@register.filter
def estimate_quantity(value):
    if value is None or value == '':
        return '—'
    try:
        amount = Decimal(str(value))
        if not amount.is_finite():
            return '—'
        result = format(amount, ',f')
        if '.' in result:
            result = result.rstrip('0').rstrip('.')
        return result.replace(',', '\u00a0').replace('.', ',')
    except (InvalidOperation, ValueError):
        return '—'
