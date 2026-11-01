from pathlib import Path
from django.conf import settings
from django.http import HttpResponse
from ops.windows_runtime import read_json


class TrainingGuard:
    """The copied admin can change exercises, not the host or real integrations."""
    blocked = {'register','login','password_change','add_from_ad','ad_group_search','one_c',
        'one_c_jobs','one_c_report','one_c_table','portal_updates','portal_update_status',
        'user_invite','user_invite_bulk','invitation_list','invitation_detail','invitation_status',
        'invitation_settings','training_list','training_edit','training_reset','training_enter',
        'impersonation_start','impersonation_stop','impersonation_history'}

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        access = read_json(settings.TRAINING_CONTROL / 'access.json', {})
        current = read_json(settings.TRAINING_CONTROL / 'current.json', {})
        if not access.get('enabled') or current.get('generation') != settings.TRAINING_GENERATION:
            return HttpResponse('Учебная копия обновляется или отключена. Откройте её заново из основного портала.', status=503)
        request.training_copy = access
        request.training_return_url = current.get('return_url', '/')
        request.training_reset = '07:00 по Москве'
        return self.get_response(request)

    def process_view(self, request, view, args, kwargs):
        match = request.resolver_match
        if match.view_name in {'accounts:training_consume','accounts:training_health'}:
            return None
        if match.namespace == 'accounts' and match.url_name == 'logout':
            return None
        if not request.user.is_authenticated:
            return HttpResponse('Вход в учебную копию выполняется через задание в основном портале.', status=403)
        access = request.training_copy
        if request.user.pk not in access.get('members', []) or request.user.company_id != access.get('company'):
            return HttpResponse('Доступ к учебной группе завершён. Вернитесь в основной портал.', status=403)
        if 'admin' in match.namespaces or match.namespace == 'accounts' and match.url_name in self.blocked:
            return HttpResponse('Это действие выполняется только в основном портале. Учебные изменения не отправляются во внешние системы.', status=403)
        return None
