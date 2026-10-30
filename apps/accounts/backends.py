"""Separate corporate and local identities; never import directory privileges."""
import logging
from uuid import UUID
from django.conf import settings
from django.contrib.auth.backends import ModelBackend
from django.core.exceptions import PermissionDenied
from django.db import transaction,IntegrityError
from django.contrib.auth.hashers import make_password
from .models import User, AccessGroup

logger=logging.getLogger(__name__)


class LocalBackend(ModelBackend):
    def authenticate(self,request,username=None,password=None,**kwargs):
        if request is not None and request.POST.get('auth_source')=='ldap':return None
        user=super().authenticate(request,username=username,password=password,**kwargs)
        return user if user and user.auth_source=='LOCAL' else None


def normalize_login(value):
    value=(value or '').strip()
    domain=settings.LDAP_DOMAIN.casefold()
    if '\\' in value:
        prefix,value=value.split('\\',1)
        if prefix.casefold() not in {domain,domain.split('.')[0]}:return None
    if '@' in value:
        value,suffix=value.rsplit('@',1)
        if suffix.casefold()!=domain:return None
    if not value or len(value)>150 or any(c in value for c in ['\\','@','\x00']):return None
    return value


def directory_identity(login,password):
    from ldap3 import Server,Connection,SIMPLE,NONE,SUBTREE
    from ldap3.utils.conv import escape_filter_chars
    from ldap3.core.exceptions import LDAPException,LDAPInvalidCredentialsResult
    attributes=['objectGUID','sAMAccountName','givenName','sn','mail']
    search_filter='(&(objectCategory=person)(objectClass=user)(sAMAccountName='+escape_filter_chars(login)+')(!(userAccountControl:1.2.840.113556.1.4.803:=2)))'
    for host in settings.LDAP_SERVERS:
        service=None;user_connection=None
        try:
            server=Server(host,port=settings.LDAP_PORT,use_ssl=False,connect_timeout=settings.LDAP_TIMEOUT,get_info=NONE)
            service=Connection(server,user=settings.LDAP_BIND_USER,password=settings.LDAP_BIND_PASSWORD,authentication=SIMPLE,auto_bind=True,auto_referrals=False,raise_exceptions=True,receive_timeout=settings.LDAP_TIMEOUT)
            service.search(settings.LDAP_BASE_DN,search_filter,search_scope=SUBTREE,attributes=attributes,size_limit=2)
            if len(service.entries)!=1:return None
            entry=service.entries[0]
            # No directory identity is provisioned until its own password is verified.
            try:
                user_connection=Connection(server,user=entry.entry_dn,password=password,authentication=SIMPLE,auto_bind=True,auto_referrals=False,raise_exceptions=True,receive_timeout=settings.LDAP_TIMEOUT)
            except LDAPInvalidCredentialsResult:return None
            guid=UUID(bytes_le=entry['objectGUID'].raw_values[0])
            group_dns = []
            # AD evaluates transitive membership, including nested security groups.
            from ldap3 import BASE
            for dn in AccessGroup.objects.exclude(ad_group_dn='').values_list('ad_group_dn', flat=True):
                service.search(dn, '(&(objectClass=group)(member:1.2.840.113556.1.4.1941:=' + escape_filter_chars(entry.entry_dn) + '))', search_scope=BASE, attributes=['distinguishedName'])
                if service.entries:
                    group_dns.append(dn)
            def value(key):return str(entry[key].value or '')
            return dict(login=value('sAMAccountName'),group_dns=group_dns,guid=guid,first_name=value('givenName')[:150],last_name=value('sn')[:150],email=value('mail')[:254])
        except (LDAPException,OSError,ValueError,KeyError,TypeError):
            # Do not log LDAP exception text: providers may include bind information.
            logger.warning('LDAP server unavailable or returned invalid directory data: %s',host)
        finally:
            for conn in [user_connection,service]:
                if conn:
                    try:conn.unbind()
                    except LDAPException:pass
    return None


class DirectoryBackend(ModelBackend):
    def authenticate(self,request,username=None,password=None,**kwargs):
        if request is None or request.POST.get('auth_source')!='ldap':return None
        if not settings.LDAP_ENABLED or not password or not settings.LDAP_BIND_PASSWORD:raise PermissionDenied
        login=normalize_login(username)
        if not login:raise PermissionDenied
        try:identity=directory_identity(login,password)
        except ImportError:
            logger.error('LDAP dependency is not installed.')
            raise PermissionDenied
        if not identity:raise PermissionDenied
        with transaction.atomic():
            try:
                user,created=User.objects.get_or_create(ldap_guid=identity['guid'],defaults={
                    'username':'ad:'+identity['guid'].hex,'auth_source':'LDAP','password':make_password(None),
                })
            except IntegrityError:raise PermissionDenied
            if user.auth_source!='LDAP' or not self.user_can_authenticate(user):raise PermissionDenied
            # Company, role, objects, staff and superuser rights are assigned only locally.
            for field in ['first_name','last_name','email']:setattr(user,field,identity[field])
            user.ldap_login=normalize_login(identity.get('login') or login) or ''
            user.save(update_fields=['first_name','last_name','email','ldap_login'])
            from .access_groups import map_directory_group
            map_directory_group(user, identity)
            return user
