"""Read-only AD group picker for company administrators."""
from django.conf import settings
from django.core.exceptions import ValidationError
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.views.decorators.http import require_GET
from core.permissions import require_roles


def search_groups(query=''):
    if not settings.LDAP_ENABLED or not settings.LDAP_BIND_PASSWORD:
        raise ValidationError('LDAP не настроен на сервере.')
    query = query.strip()
    if len(query) > 100:
        raise ValidationError('Введите не более 100 символов.')
    try:
        from ldap3 import Server, Connection, SIMPLE, NONE, SUBTREE
        from ldap3.utils.conv import escape_filter_chars
        from ldap3.core.exceptions import LDAPException
    except ImportError:
        raise ValidationError('Установите зависимости из requirements.txt.')
    base = getattr(settings, 'LDAP_GROUP_BASE_DN', '') or ','.join('DC=' + part for part in settings.LDAP_DOMAIN.split('.'))
    value = escape_filter_chars(query)
    expression = '(objectClass=group)' if not query else '(&(objectClass=group)(|(cn=*' + value + '*)(sAMAccountName=*' + value + '*)(description=*' + value + '*)))'
    for host in settings.LDAP_SERVERS:
        connection = None
        try:
            server = Server(host, port=settings.LDAP_PORT, use_ssl=False, connect_timeout=settings.LDAP_TIMEOUT, get_info=NONE)
            connection = Connection(server, user=settings.LDAP_BIND_USER, password=settings.LDAP_BIND_PASSWORD, authentication=SIMPLE, auto_bind=True, auto_referrals=False, raise_exceptions=True, receive_timeout=settings.LDAP_TIMEOUT)
            connection.search(base, expression, search_scope=SUBTREE, attributes=['cn', 'description'], paged_size=100)
            rows = [{'dn': entry.entry_dn, 'name': str(entry['cn'].value or ''), 'description': str(entry['description'].value or '')} for entry in connection.entries[:100]]
            cookie = connection.result.get('controls', {}).get('1.2.840.113556.1.4.319', {}).get('value', {}).get('cookie')
            return {'groups': sorted(rows, key=lambda row: (row['name'].casefold(), row['dn'].casefold())), 'more': bool(cookie)}
        except (LDAPException, OSError, ValueError, KeyError, TypeError):
            pass
        finally:
            if connection:
                try:
                    connection.unbind()
                except LDAPException:
                    pass
    raise ValidationError('Не удалось получить группы AD. Проверьте доступ служебной учётной записи к группам домена.')


@login_required
@require_GET
def group_search(request):
    require_roles(request.user, {'ADMIN'})
    try:
        response = JsonResponse(search_groups(request.GET.get('q', '')))
    except ValidationError as exc:
        response = JsonResponse({'error': ' '.join(exc.messages)}, status=400)
    response['Cache-Control'] = 'no-store'
    return response
