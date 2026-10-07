from decimal import Decimal
from django import template

register = template.Library()


@register.filter
def money(value):
    if value is None:
        return "—"
    return f"{Decimal(value):,.2f}".replace(",", " ").replace(".", ",")


@register.filter
def number(value):
    if value is None:
        return "—"
    return f"{Decimal(value):,.1f}".replace(",", " ").replace(".", ",")


@register.filter
def percent(value):
    if value is None:
        return "—"
    return f"{Decimal(value):.1f}".replace(".", ",") + "%"
