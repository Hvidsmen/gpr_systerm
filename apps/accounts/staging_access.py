"""The original authenticated owner is checked before role impersonation."""
from django.conf import settings
from django.contrib.auth.backends import ModelBackend
from django.http import HttpResponse
from .models import User


class OwnerBackend(ModelBackend):
    def authenticate(self, request, username=None, password=None, **kwargs):
        if not getattr(settings, 'STAGING_MODE', False):return None
        state = settings.STAGING_CONFIG
        if (username or '').strip().casefold() != state['owner_login'].casefold():return None
        owner = self.get_user(state['owner_id'])
        return owner if owner and owner.check_password(password) else None

    def get_user(self, user_id):
        if not getattr(settings, 'STAGING_MODE', False) or str(user_id) != str(settings.STAGING_CONFIG['owner_id']):return None
        owner = User.objects.filter(pk=user_id, company_id=settings.STAGING_CONFIG['company_id'], archived_at=None).first()
        return owner if owner and owner.is_active else None


class StagingGuard:
    blocked = {'register', 'add_from_ad', 'ad_group_search', 'one_c', 'one_c_jobs', 'one_c_report', 'one_c_table',
               'portal_updates', 'portal_update_status', 'user_invite', 'user_invite_bulk', 'invitation_list',
               'invitation_detail', 'invitation_status', 'invitation_settings', 'training_list', 'training_edit',
               'training_reset', 'training_enter', 'training_consume', 'training_health'}

    def __init__(self, get_response):self.get_response = get_response

    def __call__(self, request):
        request.staging = settings.STAGING_CONFIG
        # AuthenticationMiddleware still holds the real owner here, before ActAs.
        if request.user.is_authenticated and (request.user.pk != request.staging['owner_id'] or request.user.company_id != request.staging['company_id']):
            return HttpResponse('Тестовый портал доступен только его владельцу.', status=403)
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
