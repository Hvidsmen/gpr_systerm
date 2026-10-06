from types import SimpleNamespace
from django import template
from django.core.exceptions import PermissionDenied
from core.permissions import check_route

register = template.Library()


@register.filter
def may(user, route):
    if not user.is_authenticated or ':' not in route:
        return False
    namespace, name = route.split(':', 1)
    try:
        check_route(user, SimpleNamespace(namespace=namespace, url_name=name, kwargs={}), 'GET')
    except PermissionDenied:
        return False
    return True
