"""Verify a listed portal administrator using their own AD bind, with no writes."""
import logging
from uuid import UUID
from django.views.decorators.debug import sensitive_variables

logger = logging.getLogger(__name__)


@sensitive_variables()
def verify(person, password, config):
    if not config.get('enabled') or not password or not person.get('ldap_guid') or not person.get('ldap_login'):
        return False
    from ldap3 import Server, Connection, SIMPLE, NONE, SUBTREE
    from ldap3.core.exceptions import LDAPException, LDAPInvalidCredentialsResult
    from ldap3.utils.conv import escape_filter_chars
    for host in config['servers']:
        connection = None
        try:
            server = Server(host, port=config['port'], use_ssl=False, connect_timeout=config['timeout'], get_info=NONE)
            connection = Connection(server, user=person['ldap_login'] + '@' + config['domain'], password=password,
                authentication=SIMPLE, auto_bind=True, auto_referrals=False, read_only=True,
                raise_exceptions=True, receive_timeout=config['timeout'])
            connection.search(config['base_dn'], '(&(objectCategory=person)(objectClass=user)(sAMAccountName=' +
                escape_filter_chars(person['ldap_login']) + ')(!(userAccountControl:1.2.840.113556.1.4.803:=2)))',
                search_scope=SUBTREE, attributes=['objectGUID'], size_limit=2)
            return len(connection.entries) == 1 and UUID(bytes_le=connection.entries[0]['objectGUID'].raw_values[0]) == UUID(person['ldap_guid'])
        except LDAPInvalidCredentialsResult:
            return False
        except (LDAPException, OSError, ValueError, KeyError, TypeError):
            # Directory exception text can contain bind information.
            logger.warning('AD authentication unavailable for a staging administrator')
        finally:
            if connection is not None:
                try:connection.unbind()
                except LDAPException:pass
    return False
