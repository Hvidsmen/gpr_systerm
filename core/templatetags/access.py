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


@register.filter
def can_delete_workspace(workspace, user):
    from apps.planning.deletion import workspace_reason
    if not workspace or not may(user, 'planning:workspace_delete'):
        return False
    return not workspace_reason(workspace, user)
