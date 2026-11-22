"""Read-only discovery of the 1C UAT source using the existing ZUP identity."""
import json
import os
import re
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ImproperlyConfigured
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import HttpResponseBadRequest, JsonResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods

from core.permissions import require_roles, UAT_INTEGRATION_ROLES
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
UAT_IMPORT_STAGES = (
    ('queued', 'Задание принято'),
    ('starting', 'Запуск обработчика'),
    ('connecting', 'Подключение к УАТ'),
    ('schema', 'Проверка источников и периода'),
    ('metadata', 'Чтение документов'),
    ('saving', 'Сохранение снимка'),
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


def waybill_work_rows(report, limit=200):
    """Attach each UAT work assignment to its waybill and resolved work scope."""
    headers = {row.get('source_id'): row for row in report.get('waybills', []) if row.get('source_id')}
    result = []
    for task in report.get('waybill_tasks', [])[:limit]:
        header = headers.get(task.get('parent_id'), {})
        result.append({
            **task,
            'number': header.get('number', ''),
            'document_date': header.get('document_date', ''),
            'vehicle': header.get('vehicle', ''),
            'plate': header.get('plate', ''),
            'driver_1': header.get('driver_1', ''),
            # UAT stores work scope as a hierarchy in the construction-object
            # directory: project -> site -> object. Department is an independent
            # document field and must not be used as a substitute for the site.
            'project': task.get('object_grandparent') or header.get('object_grandparent', ''),
            'work_site': task.get('object_parent') or header.get('object_parent', ''),
            'department': task.get('department') or header.get('department', ''),
            'construction_object': task.get('construction_object') or header.get('construction_object', ''),
            'construction_object_code': task.get('construction_object_code') or header.get('construction_object_code', ''),
        })
    return result


def waybill_rows(report, limit=50):
    result = []
    for header in report.get('waybills', [])[:limit]:
        result.append({
            **header,
            'project': header.get('object_grandparent', ''),
            'work_site': header.get('object_parent', ''),
        })
    return result


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
    stages = UAT_IMPORT_STAGES if run and run.report.get('uat_import') else UAT_STAGES
    if not run:
        return {
            'state': 'idle', 'busy': False, 'progress': 0, 'stage_number': None,
            'stage_total': len(stages), 'stage': 'Диагностика не запущена',
            'steps': [{'code': code, 'label': label, 'state': 'pending', 'number': number}
                      for number, (code, label) in enumerate(stages, 1)],
        }
    checkpoint = _progress_file(run)
    codes = [code for code, _ in stages]
    if run.status == 'DONE':
        current = len(stages) - 1
        state, stage, progress = 'done', 'Данные УАТ загружены' if run.report.get('uat_import') else 'Структура УАТ прочитана', 100
    else:
        fallback = 'queued' if run.status == 'QUEUED' else 'starting'
        code = checkpoint.get('stage') if checkpoint.get('stage') in codes else fallback
        current = codes.index(code)
        state = 'error' if run.status == 'ERROR' else 'running'
        stage = run.error if run.status == 'ERROR' and run.error else stages[current][1]
        progress = round(current * 100 / len(stages))
    steps = []
    for number, (code, label) in enumerate(stages, 1):
        if run.status == 'DONE' or number - 1 < current:
            step_state = 'complete'
        elif number - 1 == current:
            step_state = 'failed' if run.status == 'ERROR' else 'current'
        else:
            step_state = 'pending'
        steps.append({'code': code, 'label': label, 'state': step_state, 'number': number})
    return {
        'state': state, 'busy': run.status in {'QUEUED', 'RUNNING'}, 'progress': progress,
        'stage_number': current + 1, 'stage_total': len(stages), 'stage': stage,
        'run_id': str(run.pk), 'run_status': run.status, 'steps': steps,
    }


@login_required
@require_http_methods(['GET', 'POST'])
def discovery(request):
    require_roles(request.user, UAT_INTEGRATION_ROLES)
    connection, _ = OneCConnection.objects.get_or_create(company=request.user.company)
    database = database_name()
    if request.method == 'POST':
        action = request.POST.get('action')
        if action not in {'diagnose', 'import', 'run_pending'}:
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
        elif action == 'import':
            from .one_c import enqueue
            from .uat_sources import status as source_status
            structure = connection.runs.filter(
                database=database, report__uat_discovery=True, status='DONE',
            ).order_by('-created_at', '-pk').first()
            if not structure or not source_status(structure.report)['ready']:
                messages.error(request, 'Сначала прочитайте и подтвердите структуру УАТ.')
                return redirect('accounts:uat')
            try:
                since = date.fromisoformat(request.POST.get('from_date', ''))
            except ValueError:
                messages.error(request, 'Укажите корректную начальную дату загрузки.')
                return redirect('accounts:uat')
            if since > date.today() or since < date.today() - timedelta(days=366):
                messages.error(request, 'Начальная дата должна быть в пределах последнего года и не позднее сегодня.')
                return redirect('accounts:uat')
            run = enqueue(connection, request.user, database=database, uat_import_from=since,
                          trust_certificate=True)
        else:
            run = connection.runs.filter(
                Q(report__uat_discovery=True) | Q(report__uat_import=True),
                database=database, status='QUEUED',
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
        messages.success(request, 'Задание УАТ запущено. Статус обновится автоматически.')
        return redirect('accounts:uat')

    runs = connection.runs.filter(
        Q(report__uat_discovery=True) | Q(report__uat_import=True), database=database,
    ).order_by('-created_at', '-pk')
    last = runs.first()
    latest = runs.filter(report__uat_discovery=True, status='DONE').first()
    latest_import = runs.filter(report__uat_import=True, status='DONE').first()
    if last:
        from .one_c_jobs import error_help
        last.error_help = error_help(last.error, last.server, last.database)
    report = latest.report if latest else {}
    from .uat_sources import status as source_status
    source = source_status(report)
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
    import_report = latest_import.report if latest_import else {}
    return render(request, 'accounts/uat.html', {
        'connection': connection, 'database': database, 'last': last, 'latest': latest,
        'latest_import': latest_import, 'import_report': import_report,
        'waybills': waybill_rows(import_report),
        'waybill_work': waybill_work_rows(import_report),
        'waybill_work_total': import_report.get('waybill_task_count', len(import_report.get('waybill_tasks', []))),
        'repair_sheets': import_report.get('repair_sheets', [])[:50],
        'source_status': source, 'import_from': date.today() - timedelta(days=30),
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
    require_roles(request.user, UAT_INTEGRATION_ROLES)
    connection, _ = OneCConnection.objects.get_or_create(company=request.user.company)
    run = connection.runs.filter(
        Q(report__uat_discovery=True) | Q(report__uat_import=True), database=database_name(),
    ).order_by('-created_at', '-pk').first()
    return JsonResponse(progress_status(run))
