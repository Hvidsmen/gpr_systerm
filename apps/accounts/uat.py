"""Read-only discovery of the 1C UAT source using the existing ZUP identity."""
import json
import os
import re
import subprocess
import sys
from pathlib import Path

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ImproperlyConfigured
from django.core.paginator import Paginator
from django.http import HttpResponseBadRequest, JsonResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods

from core.permissions import require_roles
from .models import OneCConnection
from .one_c_catalog import catalogue, TYPES


KEYWORDS = ('путев', 'ремонт', 'транспорт', 'автомоб', 'техник', 'водител', 'маршрут', 'топлив', 'пробег', 'неисправ')
UAT_STAGES = (
    ('queued', 'Задание принято'),
    ('starting', 'Запуск обработчика'),
    ('connecting', 'Подключение к УАТ'),
    ('schema', 'Чтение SQL-структуры'),
    ('metadata', 'Расшифровка метаданных'),
    ('saving', 'Сохранение результата'),
)


def database_name():
    value = settings.UAT_DATABASE
    if not re.fullmatch(r'[\w\-]+', value):
        raise ImproperlyConfigured('UAT_DATABASE содержит недопустимые символы.')
    return value


def launch_staging_worker():
    """Start one short-lived UAT worker under the portal Windows identity."""
    runtime = Path(settings.CONFIG_DIR) / '.runtime'
    runtime.mkdir(parents=True, exist_ok=True)
    if staging_update_busy():
        raise RuntimeError('Сначала дождитесь завершения обновления тестового портала.')
    command = [sys.executable, str(Path(settings.BASE_DIR) / 'manage.py'), 'process_1c', '--uat-only']
    with (runtime / '1c-worker.log').open('ab') as output:
        return subprocess.Popen(
            command,
            cwd=str(settings.BASE_DIR),
            env=os.environ.copy(),
            stdin=subprocess.DEVNULL,
            stdout=output,
            stderr=subprocess.STDOUT,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
        )


def staging_update_busy():
    if not getattr(settings,'STAGING_CONFIG',None):return False
    from ops.staging_updates import BUSY
    from ops.windows_runtime import read_json
    runtime=Path(settings.CONFIG_DIR)/'.runtime'
    return read_json(runtime/'staging-update.json',{}).get('state') in BUSY


def _progress_file(run):
    if not run:
        return {}
    path = Path(settings.CONFIG_DIR) / '.runtime' / '1c-diagnostics' / run.pk.hex / 'progress.json'
    try:
        return json.loads(path.read_text(encoding='utf-8-sig'))
    except (OSError, ValueError, TypeError):
        return {}


def progress_status(run):
    """Return UI progress based on the database claim and PowerShell checkpoints."""
    if not run:
        return {
            'state': 'idle', 'busy': False, 'progress': 0, 'stage_number': None,
            'stage_total': len(UAT_STAGES), 'stage': 'Диагностика не запущена',
            'steps': [{'code': code, 'label': label, 'state': 'pending', 'number': number}
                      for number, (code, label) in enumerate(UAT_STAGES, 1)],
        }
    checkpoint = _progress_file(run)
    codes = [code for code, _ in UAT_STAGES]
    if run.status == 'DONE':
        current = len(UAT_STAGES) - 1
        state, stage, progress = 'done', 'Структура УАТ прочитана', 100
    else:
        fallback = 'queued' if run.status == 'QUEUED' else 'starting'
        code = checkpoint.get('stage') if checkpoint.get('stage') in codes else fallback
        current = codes.index(code)
        state = 'error' if run.status == 'ERROR' else 'running'
        stage = run.error if run.status == 'ERROR' and run.error else UAT_STAGES[current][1]
        progress = round(current * 100 / len(UAT_STAGES))
    steps = []
    for number, (code, label) in enumerate(UAT_STAGES, 1):
        if run.status == 'DONE' or number - 1 < current:
            step_state = 'complete'
        elif number - 1 == current:
            step_state = 'failed' if run.status == 'ERROR' else 'current'
        else:
            step_state = 'pending'
        steps.append({'code': code, 'label': label, 'state': step_state, 'number': number})
    return {
        'state': state, 'busy': run.status in {'QUEUED', 'RUNNING'}, 'progress': progress,
        'stage_number': current + 1, 'stage_total': len(UAT_STAGES), 'stage': stage,
        'run_id': str(run.pk), 'run_status': run.status, 'steps': steps,
    }


@login_required
@require_http_methods(['GET', 'POST'])
def discovery(request):
    require_roles(request.user, {'ADMIN'})
    connection, _ = OneCConnection.objects.get_or_create(company=request.user.company)
    database = database_name()
    if request.method == 'POST':
        action = request.POST.get('action')
        if action not in {'diagnose', 'run_pending'}:
            return HttpResponseBadRequest('Неизвестное действие.')
        if staging_update_busy():
            messages.error(request,'Сначала дождитесь завершения обновления тестового портала.')
            return redirect('accounts:uat')
        if action == 'diagnose':
            from .one_c import enqueue
            # The internal SQL Server presents a certificate whose issuing CA is
            # not installed in the portal account's Windows trust store. Keep TLS
            # encryption, but accept that configured internal certificate for UAT.
            run = enqueue(connection, request.user, database=database, uat_discovery=True,
                          trust_certificate=True)
        else:
            run = connection.runs.filter(
                database=database, report__uat_discovery=True, status='QUEUED',
            ).order_by('created_at', 'pk').first()
        if not run:
            messages.warning(request, 'Нет ожидающей диагностики. Обновите страницу и запустите её заново.')
            return redirect('accounts:uat')
        if getattr(settings, 'STAGING_CONFIG', None):
            try:
                launch_staging_worker()
            except (OSError,RuntimeError) as exc:
                messages.error(request, f'Не удалось запустить обработчик УАТ: {exc}')
                return redirect('accounts:uat')
        messages.success(request, 'Диагностика УАТ запущена. Статус обновится автоматически.')
        return redirect('accounts:uat')

    runs = connection.runs.filter(database=database, report__uat_discovery=True).order_by('-created_at', '-pk')
    last = runs.first()
    latest = runs.filter(status='DONE').first()
    if last:
        from .one_c_jobs import error_help
        last.error_help = error_help(last.error, last.server, last.database)
    report = latest.report if latest else {}
    labels = report.get('metadata_labels', {})
    all_rows, type_counts, total = catalogue(report, labels)
    recommended = [row for row in all_rows if any(word in (row['name'] + ' ' + row['key']).casefold() for word in KEYWORDS)]
    query = request.GET.get('q', '').strip()[:200]
    kind = request.GET.get('type', '')
    if kind not in dict(TYPES):kind = ''
    rows, _, _ = catalogue(report, labels, query, kind)
    page = Paginator(rows, 75).get_page(request.GET.get('page'))
    groups = []
    for row in page:
        row['type_label'] = dict(TYPES)[row['type']]
        if not groups or groups[-1]['type'] != row['type']:
            groups.append({'type': row['type'], 'label': row['type_label'], 'rows': []})
        groups[-1]['rows'].append(row)
    params = request.GET.copy();params.pop('page', None)
    return render(request, 'accounts/uat.html', {
        'connection': connection, 'database': database, 'last': last, 'latest': latest,
        'queued': bool(last and last.status == 'QUEUED'),
        'running': bool(last and last.status == 'RUNNING'),
        'uat_progress': progress_status(last),
        'recommended': recommended[:100], 'catalogue_groups': groups, 'catalogue_page': page,
        'catalogue_total': total, 'catalogue_query': query, 'catalogue_type': kind,
        'type_counts': type_counts, 'catalogue_params': params.urlencode(),
    })


@login_required
@require_http_methods(['GET'])
def status(request):
    require_roles(request.user, {'ADMIN'})
    connection, _ = OneCConnection.objects.get_or_create(company=request.user.company)
    run = connection.runs.filter(
        database=database_name(), report__uat_discovery=True,
    ).order_by('-created_at', '-pk').first()
    return JsonResponse(progress_status(run))
