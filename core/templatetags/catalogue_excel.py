from django import template
from core.permissions import READ_ROLES, role_code

register = template.Library()


@register.simple_tag(takes_context=True)
def catalogue_export_url(context):
    request = context['request']
    if not request.user.is_authenticated or not request.user.company_id or role_code(request.user) not in READ_ROLES:
        return ''
    params = request.GET.copy()
    params.pop('page', None)
    params['export'] = 'xlsx'
    return request.path + '?' + params.urlencode()
