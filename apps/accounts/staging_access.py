"""Production administrators authenticate in the isolated test environment."""
import logging
import sqlite3
from django.conf import settings
from django.contrib.auth.backends import ModelBackend
from django.contrib.auth.hashers import check_password, make_password
from django.db import transaction, IntegrityError
from django.http import HttpResponse
from django.views.decorators.debug import sensitive_variables
from .models import User, Company, Role
from core.permissions import role_code
from ops.staging_auth import administrators, login_matches, same_identity

logger = logging.getLogger(__name__)


def allowed(user):
    if not user or not user.is_active or user.archived_at is not None or not user.company_id:return False
    state = settings.STAGING_CONFIG
    return (user.pk == state['owner_id'] and user.company_id == state['company_id']) or role_code(user) == 'ADMIN'


class OwnerBackend(ModelBackend):
    # Keep the backend path for existing staging sessions after upgrades.
    @sensitive_variables()
    def authenticate(self, request, username=None, password=None, **kwargs):
        if not getattr(settings, 'STAGING_MODE', False):return None
        state = settings.STAGING_CONFIG
        directory = getattr(settings, 'STAGING_DIRECTORY', {})
        source = request.POST.get('auth_source', 'local') if request else kwargs.get('auth_source', 'local')
        # The existing owner's separate password remains an emergency local entry.
        if source != 'ldap' and (username or '').strip().casefold() == state['owner_login'].casefold():
            owner = self.get_user(state['owner_id'])
            if owner and owner.check_password(password):return owner
        try:
            namespace = 'LDAP' if source == 'ldap' else 'LOCAL'
            candidates = [person for person in administrators(state) if person['auth_source'] == namespace and
                          login_matches(person, username, directory.get('domain', ''))]
        except (OSError, ValueError, sqlite3.Error):
            logger.warning('Production administrator list unavailable for test login')
            return None
        if len(candidates) != 1 or not password:return None
        person = candidates[0]
        if person['auth_source'] == 'LDAP':
            if source != 'ldap':return None
            from .staging_directory import verify
            if not verify(person, password, directory):return None
        elif source == 'ldap' or not check_password(password, person['password']):
            return None
        try:
            with transaction.atomic():
                user = User.objects.select_for_update().filter(pk=person['id']).first()
                if user and (not same_identity(user, person) or not user.is_active or user.archived_at is not None or user.company_id != person['company_id']):return None
                if user is None:
                    if not Company.objects.filter(pk=person['company_id']).exists():return None
                    if User.objects.filter(username=person['username']).exists():return None
                    user = User(pk=person['id'], username=person['username'], company_id=person['company_id'],
                        auth_source=person['auth_source'], ldap_guid=person['ldap_guid'], ldap_login=person['ldap_login'],
                        first_name=person['first_name'], last_name=person['last_name'], email=person['email'])
                user.role = Role.objects.get(code='ADMIN')
                user.is_superuser = user.is_staff = False
                if person['auth_source'] == 'LOCAL':user.password = person['password']
                elif not user.pk or not user.password:user.password = make_password(None)
                user.save()
                return user
        except (IntegrityError, ValueError):
            return None

    @sensitive_variables()
    def get_user(self, user_id):
        if not getattr(settings, 'STAGING_MODE', False):return None
        user = User.objects.select_related('role').filter(pk=user_id).first()
        if not allowed(user):return None
        state = settings.STAGING_CONFIG
        if user.pk == state['owner_id'] and user.company_id == state['company_id']:return user
        try:
            return user if any(same_identity(user, person) and user.company_id == person['company_id'] for person in administrators(state)) else None
        except (OSError, ValueError, sqlite3.Error):
            return None


class StagingGuard:
    blocked = {'register', 'add_from_ad', 'ad_group_search', 'one_c', 'one_c_jobs', 'one_c_report', 'one_c_table',
               'user_invite', 'user_invite_bulk', 'invitation_list',
               'invitation_detail', 'invitation_status', 'invitation_settings', 'training_list', 'training_edit',
               'training_reset', 'training_enter', 'training_consume', 'training_health'}

    def __init__(self, get_response):self.get_response = get_response

    def __call__(self, request):
        request.staging = settings.STAGING_CONFIG
        # Check the authenticated administrator before ActAs switches roles.
        if request.user.is_authenticated and not allowed(request.user):
            return HttpResponse('Тестовый портал доступен только администраторам.', status=403)
        response = self.get_response(request)
        response['X-Portal-Environment'] = 'staging'
        response['X-Portal-Version'] = request.staging['revision'][:12]
        response['Cache-Control'] = 'no-store'
        return response

    def process_view(self, request, view, args, kwargs):
        match = request.resolver_match
        if 'admin' in match.namespaces or match.namespace == 'accounts' and match.url_name in self.blocked:
            return HttpResponse('На тестовом портале отключены внешние обмены, отправка приглашений, учебные копии и обновление рабочего сервера.', status=403)
        return None
