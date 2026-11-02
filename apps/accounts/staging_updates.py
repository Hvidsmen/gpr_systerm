"""Owner-only web controls for the separate test supervisor."""
import logging
from pathlib import Path

from django.conf import settings
from django.contrib import messages
from django.core import signing
from django.core.exceptions import PermissionDenied
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.utils.dateparse import parse_datetime

from ops import staging_updates as updater

SALT = 'gpr-staging-update-v1'
REFRESH_SALT = 'gpr-staging-refresh-v1'


def owner_root(request):
    state = getattr(settings, 'STAGING_CONFIG', {})
    if (not getattr(settings, 'STAGING_MODE', False) or not request.user.is_authenticated or
            request.user.pk != state.get('owner_id') or request.user.company_id != state.get('company_id') or
            getattr(request, 'acting_admin', None)):
        raise PermissionDenied('Обновлять тест может только его владелец. Вернитесь из просмотра роли к администратору.')
    return Path(settings.CONFIG_DIR)


def updates(request):
    root = owner_root(request)
    if request.method == 'POST':
        try:
            action = request.POST.get('action')
            if action == 'check':
                updater.check_release(root)
                messages.success(request, 'Проверка тестовой ветки завершена.')
            elif action == 'update':
                selected = signing.loads(request.POST.get('version_token', ''), salt=SALT, max_age=3600)
                if selected['instance'] != settings.STAGING_CONFIG['instance']:
                    raise ValueError('Выбранная версия относится к другой тестовой установке.')
                updater.enqueue(root, revision=selected['revision'], from_revision=selected['from_revision'],
                                owner_id=request.user.pk)
                messages.success(request, 'Обновление тестового портала принято. Можно закрыть страницу.')
            elif action == 'refresh':
                if request.POST.get('confirm_replace') != 'yes':
                    raise ValueError('Подтвердите замену тестовой базы и документов.')
                selected = signing.loads(request.POST.get('refresh_token', ''), salt=REFRESH_SALT, max_age=3600)
                if selected['instance'] != settings.STAGING_CONFIG['instance']:
                    raise ValueError('Подтверждение относится к другой тестовой установке.')
                updater.enqueue_refresh(root, from_revision=selected['revision'],
                                        data_generation=selected['data_generation'], owner_id=request.user.pk)
                messages.success(request, 'Обновление данных принято. После завершения войдите с прежним тестовым паролем.')
            else:
                raise ValueError('Выберите действие на странице обновления теста.')
        except (signing.BadSignature, KeyError, TypeError):
            messages.error(request, 'Выбор версии устарел. Повторите проверку обновлений.')
        except ValueError as exc:
            messages.error(request, str(exc))
        except Exception:
            logging.exception('Acceptance update request failed')
            messages.error(request, 'Не удалось связаться с GitHub или обработчиком теста. Повторите попытку; журнал — .runtime/server.log.')
        return redirect('accounts:portal_updates')
    status = updater.state(root)
    token = signing.dumps({'instance': settings.STAGING_CONFIG['instance'], 'revision': status['available'],
                           'from_revision': status['version']}, salt=SALT) if status['can_update'] else ''
    refresh_token = signing.dumps({'instance': settings.STAGING_CONFIG['instance'], 'revision': status['version'],
                                  'data_generation': status['data_generation']}, salt=REFRESH_SALT) if status['can_refresh'] else ''
    return render(request, 'accounts/staging_updates.html', {'update': status, 'version_token': token,
        'refresh_token': refresh_token, 'data_refreshed_at': parse_datetime(status['data_refreshed_at']) if status['data_refreshed_at'] else None})


def status(request):
    response = JsonResponse(updater.state(owner_root(request)))
    response['Cache-Control'] = 'no-store'
    return response
