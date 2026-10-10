from decimal import Decimal
from django import template

register = template.Library()


@register.filter
def money(value):
    if value is None:
        return "—"
    return f"{Decimal(value):,.0f}".replace(",", " ").replace(".", ",")


@register.filter
def number(value, digits=1):
    if value is None:
        return "—"
    return f"{Decimal(value):,.{int(digits)}f}".replace(",", " ").replace(".", ",")


@register.filter
def percent(value):
    if value is None:
        return "—"
    return f"{Decimal(value):.1f}".replace(".", ",") + "%"


@register.filter
def resource_number(value, kind):
    return number(value, 0 if kind in ("labor", "equipment") else 1)
