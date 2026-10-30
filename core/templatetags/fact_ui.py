"""Local calendar shortcuts for fact entry; no coupling to individual views."""
from datetime import timedelta
from django import template
from django.utils import timezone
register=template.Library()
@register.simple_tag
def fact_dates():
    today=timezone.localdate()
    return {'today':today,'yesterday':today-timedelta(days=1)}
