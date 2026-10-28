from django import template
from apps.personnel.services import is_work
register=template.Library()


@register.filter
def calendar_class(value):
    if value=='ДП':return 'housing-travel'
    if is_work(value):return 'housing-work'
    return 'housing-rest' if value else ''
