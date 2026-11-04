"""Explicit, owner-requested updates of the acceptance installation only."""
from datetime import datetime, timezone
import errno
import json
import logging
import os
from pathlib import Path
import re
import subprocess
import time
import uuid
from urllib.request import Request, urlopen, build_opener, ProxyHandler

from ops.windows_runtime import atomic_json, read_json, singleton

REPOSITORY = 'Hvidsmen/gpr_systerm'
BRANCH = 'codex/staging-portal-20261102'
BUSY = {'queued', 'downloading', 'stopping', 'applying', 'starting', 'backing_up', 'copying', 'sanitizing', 'swapping'}
STAGES = {'queued': 'Задание принято', 'downloading': 'Скачивание выбранной версии',
          'stopping': 'Завершение запросов тестового портала',
          'applying': 'Резервная копия, зависимости и проверка новой версии',
          'starting': 'Запуск и проверка тестового портала',
          'backing_up': 'Резервная копия тестовых данных', 'copying': 'Копирование рабочих данных и документов',
          'sanitizing': 'Подготовка отдельной тестовой базы и проверка', 'swapping': 'Замена только тестовых данных'}


UPDATE_STEPS = [
    ('queued', 'Задание принято'), ('downloading', 'Скачивание версии'),
    ('stopping', 'Остановка теста'), ('applying', 'Подготовка и установка'),
    ('starting', 'Запуск и проверка'),
]
REFRESH_STEPS = [
    ('queued', 'Задание принято'), ('stopping', 'Остановка теста'),
    ('backing_up', 'Резервная копия'), ('copying', 'Копирование данных'),
    ('sanitizing', 'Подготовка базы'), ('swapping', 'Замена тестовых данных'),
    ('starting', 'Запуск и проверка'),
]


def timestamp(value):
    try:
        parsed = datetime.fromisoformat(value)
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def duration_label(seconds):
    if seconds is None:
        return '—'
    minutes, seconds = divmod(max(0, int(seconds)), 60)
    hours, minutes = divmod(minutes, 60)
    return (f'{hours} ч ' if hours else '') + (f'{minutes} мин ' if hours or minutes else '') + f'{seconds:02d} с'


def progress_summary(job, phase):
    steps = REFRESH_STEPS if job.get('kind') == 'refresh' else UPDATE_STEPS
    failed = phase in {'error', 'recovery'}
    last = (job.get('failed_stage') or job.get('state')) if failed else phase
    index = next((i for i, (code, _) in enumerate(steps) if code == last), None)
    finished = phase == 'done'
    terminal = finished or failed
    end = timestamp(job.get('finished_at')) if terminal else None
    # Old launchers only recorded updated_at; they still get the same progress UI.
    if end is None and terminal and job.get('state') in {'done', 'error', 'recovery'}:
        end = timestamp(job.get('updated_at'))
    end = end or timestamp(now())
    created = timestamp(job.get('created_at'))
    started = timestamp(job.get('phase_started_at') or job.get('updated_at') or job.get('created_at'))
    elapsed = max(0, int((end-created).total_seconds())) if created and end else None
    stage_elapsed = max(0, int((end-started).total_seconds())) if started and end and index is not None else None
    progress = 100 if finished else int(100*index/len(steps)) if index is not None else 0
    rows = []
    for i, (code, label) in enumerate(steps):
        state = 'complete' if finished or index is not None and i < index else ('failed' if failed else 'current') if i == index else 'pending'
        rows.append({'code': code, 'label': label, 'state': state, 'number': i+1})
    return {'progress': progress, 'stage_number': len(steps) if finished else index+1 if index is not None else None,
            'stage_total': len(steps), 'steps': rows, 'created_at': job.get('created_at'),
            'updated_at': job.get('updated_at'), 'phase_started_at': job.get('phase_started_at'),
            'finished_at': job.get('finished_at'), 'elapsed_seconds': elapsed,
            'stage_elapsed_seconds': stage_elapsed, 'elapsed_label': duration_label(elapsed),
            'stage_elapsed_label': duration_label(stage_elapsed), 'job_id': job.get('id', '')}


def record_progress(root, job, phase):
    stamp = now()
    if phase in {'error', 'recovery'}:
        job['failed_stage'] = job.get('state')
    if phase in {'done', 'error', 'recovery'}:
        job['finished_at'] = stamp
    else:
        job['phase_started_at'] = stamp
    job.update(state=phase, updated_at=stamp)
    atomic_json(runtime(root) / 'staging-update.json', job)


def now():
    return datetime.now(timezone.utc).isoformat()


def runtime(root):
    from ops.staging import private_path
    return private_path(root, '.runtime')


def supervisor_running(root):
    try:
        with singleton(runtime(root) / 'staging-supervisor.lock'):
            return False
    except OSError as exc:
        return exc.errno in {errno.EACCES, errno.EAGAIN, errno.EDEADLK}


def valid_sha(value):
    return isinstance(value, str) and re.fullmatch('[0-9a-f]{40}', value) is not None


def check_release(root):
    from ops.staging import manifest
    current = manifest(root)
    request = Request(f'https://api.github.com/repos/{REPOSITORY}/git/ref/heads/{BRANCH}',
                      headers={'Accept': 'application/vnd.github+json', 'User-Agent': 'YDS-Test-Updater'})
    with urlopen(request, timeout=12) as response:
        data = json.loads(response.read(1024 * 1024))
    sha = data.get('object', {}).get('sha')
    if data.get('ref') != 'refs/heads/' + BRANCH or not valid_sha(sha):
        raise ValueError('Не удалось определить версию тестовой ветки.')
    result = {'revision': sha, 'from_revision': current['revision'], 'checked_at': now(), 'branch': BRANCH}
    atomic_json(runtime(root) / 'staging-latest.json', result)
    return result


def state(root):
    from ops.staging import manifest
    current = manifest(root)
    job = read_json(runtime(root) / 'staging-update.json', {})
    latest = read_json(runtime(root) / 'staging-latest.json', {})
    ready = supervisor_running(root)
    phase = job.get('state', 'idle')
    if (runtime(root) / 'staging-recovery.json').exists():
        phase = 'recovery'
    if (runtime(root) / 'staging-refresh-transaction.json').exists():
        phase = 'recovery'
    busy = phase in BUSY
    if busy and not ready:
        phase, busy = 'error', False
    messages = {'idle': 'Проверьте, есть ли новая тестовая версия.',
                'done': 'Тестовый портал обновлён и доступен.',
                'error': 'Обновление не завершено. Подробности в журнале тестовой установки.',
                'recovery': 'Тест остановлен: требуется проверка восстановления базы администратором сервера.'}
    message = STAGES.get(phase, messages.get(phase, messages['idle']))
    if phase == 'done' and job.get('kind') == 'refresh':
        message = 'Данные теста обновлены.'
    if not ready and phase == 'idle':
        message = 'Обработчик обновлений теста не запущен. Перезапустите задание YDS Portal Test.'
    available = latest.get('revision', '')
    if not valid_sha(available) or latest.get('branch') != BRANCH:
        available = ''
    if phase == 'idle' and ready and available and available != current['revision']:
        message = 'Доступна новая тестовая версия. Можно обновить портал.'
    features = read_json(runtime(root) / 'staging-supervisor.json', {}).get('features', [])
    from ops.staging_refresh import generation
    return {**progress_summary(job, phase), 'version': current['revision'], 'available': available, 'checked_at': latest.get('checked_at'),
            'branch': BRANCH, 'ready': ready and phase != 'recovery', 'busy': busy, 'state': phase,
            'operation': job.get('kind', 'update'), 'data_generation': generation(current),
            'data_refreshed_at': current.get('data_refreshed_at'),
            'refresh_supported': 'refresh-data' in features,
            'can_refresh': ready and not busy and phase != 'recovery' and 'refresh-data' in features,
            'message': message, 'stage': STAGES.get(phase, ''),
            'can_update': ready and not busy and phase != 'recovery' and bool(available) and
                          available != current['revision'] and latest.get('from_revision') == current['revision']}


def enqueue(root, *, revision, from_revision, owner_id):
    from ops.staging import manifest
    with singleton(runtime(root) / 'staging-queue.lock'):
        current = manifest(root)
        status = state(root)
        if owner_id != current['owner_id']:
            raise ValueError('Обновление доступно только владельцу теста.')
        if not status['can_update'] or status['available'] != revision or current['revision'] != from_revision:
            raise ValueError('Версия или состояние теста изменились. Повторите проверку обновлений.')
        job = {'id': uuid.uuid4().hex, 'state': 'queued', 'revision': revision,
               'from_revision': from_revision, 'owner_id': owner_id, 'created_at': now(), 'branch': BRANCH}
        atomic_json(runtime(root) / 'staging-update.json', job)
        return job


def enqueue_refresh(root, *, from_revision, data_generation, owner_id):
    from ops.staging import manifest
    with singleton(runtime(root) / 'staging-queue.lock'):
        current = manifest(root)
        status = state(root)
        if (owner_id != current['owner_id'] or not status['can_refresh'] or
                current['revision'] != from_revision or status['data_generation'] != data_generation):
            raise ValueError('Тестовые данные или состояние изменились. Откройте страницу и подтвердите замену заново.')
        job = {'id': uuid.uuid4().hex, 'state': 'queued', 'kind': 'refresh',
               'from_revision': from_revision, 'data_generation': data_generation,
               'owner_id': owner_id, 'created_at': now()}
        atomic_json(runtime(root) / 'staging-update.json', job)
        return job


def download(root, revision):
    if not valid_sha(revision):
        raise ValueError('Некорректная версия теста.')
    target = runtime(root) / ('staging-download-' + uuid.uuid4().hex + '.zip')
    request = Request(f'https://github.com/{REPOSITORY}/archive/{revision}.zip',
                      headers={'User-Agent': 'YDS-Test-Updater'})
    started, size = time.monotonic(), 0
    try:
        with urlopen(request, timeout=30) as response, target.open('xb') as output:
            while chunk := response.read(1024 * 1024):
                size += len(chunk)
                if size > 256 * 1024 * 1024 or time.monotonic() - started > 300:
                    raise ValueError('Превышен размер или время скачивания тестовой версии.')
                output.write(chunk)
        return target
    except Exception:
        target.unlink(missing_ok=True)
        raise


def start_http(root):
    from ops.staging import manifest, environment
    current = manifest(root)
    env = environment(root)
    env['GPR_STAGING_PARENT_PIPE'] = '1'
    return subprocess.Popen([current['python'], str(Path(current['code']) / 'ops/staging.py'),
                             'http', '--root', str(root)], env=env, stdin=subprocess.PIPE)


def stop_http(child, *, graceful_timeout=115, terminate_timeout=15, kill_timeout=15):
    """Stop only our Popen HTTP child; never apply a release until it has exited."""
    if child.stdin and not child.stdin.closed:
        try:
            child.stdin.close()
        except OSError:
            logging.warning('TEST HTTP input pipe could not be closed; waiting for owned child exit.')
    try:
        child.wait(timeout=graceful_timeout)
        return
    except subprocess.TimeoutExpired:
        logging.warning('TEST HTTP child pid=%s did not exit within %ss; terminating this child only.',
                        child.pid, graceful_timeout)
    if child.poll() is None:
        child.terminate()
    try:
        child.wait(timeout=terminate_timeout)
    except subprocess.TimeoutExpired:
        logging.warning('TEST HTTP child pid=%s is still alive; killing this child only.', child.pid)
        if child.poll() is None:
            child.kill()
        child.wait(timeout=kill_timeout)


def wait_http(root, child, parent_gone):
    from ops.staging import manifest
    current = manifest(root)
    opener = build_opener(ProxyHandler({}))
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline and not parent_gone.is_set():
        if child.poll() is not None:
            break
        try:
            with opener.open(f"http://127.0.0.1:{current['port']}/accounts/login/", timeout=1) as response:
                if (response.status == 200 and response.headers.get('X-Portal-Environment') == 'staging'
                        and response.headers.get('X-Portal-Version') == current['revision'][:12]):
                    return
        except OSError:
            pass
        parent_gone.wait(.2)
    raise RuntimeError('Тестовый HTTP-процесс не подтвердил запуск выбранной версии.')


def process_job(root, child, job, parent_gone):
    from ops import staging
    archive = None
    stopped = False
    release = None
    existed = False
    def progress(phase):
        record_progress(root, job, phase)
    try:
        current = staging.manifest(root)
        if (job.get('branch') != BRANCH or not valid_sha(job.get('revision')) or
                job.get('owner_id') != current['owner_id'] or job.get('from_revision') != current['revision']):
            raise ValueError('Задание не соответствует текущей тестовой установке.')
        release = runtime(root) / 'releases' / job['revision']
        existed = release.exists()
        progress('downloading')
        archive = download(root, job['revision'])
        if parent_gone.is_set() or (runtime(root) / 'server.stop').exists():
            raise RuntimeError('Обновление отменено остановкой теста.')
        progress('stopping')
        stop_http(child)
        stopped = True
        if parent_gone.is_set():
            raise RuntimeError('Обновление отменено остановкой родительского процесса.')
        progress('applying')
        staging.update(root, archive, job['revision'])
        progress('starting')
        child = start_http(root)
        stopped = False
        wait_http(root, child, parent_gone)
        progress('done')
    except Exception:
        logging.exception('Acceptance web update failed')
        recovery = (runtime(root) / 'staging-recovery.json').exists()
        progress('recovery' if recovery else 'error')
        if recovery:
            raise
        # update() restores the stopped test DB before raising. A failed download
        # keeps the original HTTP worker; neither case restarts production.
        if stopped and not parent_gone.is_set() and not (runtime(root) / 'server.stop').exists():
            if release is not None and not existed and release.exists() and staging.manifest(root)['revision'] != job['revision']:
                import shutil
                try:shutil.rmtree(release)
                except OSError:logging.exception('Could not remove incomplete TEST release; retaining it and restarting the selected TEST version.')
            child = start_http(root)
            wait_http(root, child, parent_gone)
    finally:
        if archive is not None:
            archive.unlink(missing_ok=True)
    return child


def process_refresh(root, child, job, parent_gone):
    from ops.staging_refresh import refresh, generation
    from ops.staging import manifest
    stopped = False
    def progress(phase):
        record_progress(root, job, phase)
    try:
        current = manifest(root)
        if (job.get('owner_id') != current['owner_id'] or job.get('from_revision') != current['revision'] or
                job.get('data_generation') != generation(current) or parent_gone.is_set()):
            raise ValueError('Задание обновления данных устарело или тест остановлен.')
        progress('stopping')
        stop_http(child)
        stopped = True
        if parent_gone.is_set() or (runtime(root) / 'server.stop').exists():
            raise RuntimeError('Обновление данных отменено остановкой теста.')
        refresh(root, expected_generation=job['data_generation'], expected_revision=job['from_revision'],
                owner_id=job['owner_id'], progress=progress)
        progress('starting')
        child = start_http(root)
        stopped = False
        wait_http(root, child, parent_gone)
        progress('done')
    except Exception:
        logging.exception('Acceptance data refresh failed')
        recovery = (runtime(root) / 'staging-recovery.json').exists() or (runtime(root) / 'staging-refresh-transaction.json').exists()
        progress('recovery' if recovery else 'error')
        if recovery:
            raise
        if stopped and not parent_gone.is_set() and not (runtime(root) / 'server.stop').exists():
            child = start_http(root)
            wait_http(root, child, parent_gone)
    return child


def supervise(root, parent_gone):
    """The scheduled test launcher stays alive while its HTTP child restarts."""
    root = Path(root).resolve()
    stop = runtime(root) / 'server.stop'
    with singleton(runtime(root) / 'staging-supervisor.lock'):
        stop.unlink(missing_ok=True)
        previous = read_json(runtime(root) / 'staging-update.json', {})
        if previous.get('state') in {'applying', 'swapping'} or (runtime(root) / 'staging-refresh-transaction.json').exists():
            atomic_json(runtime(root) / 'staging-recovery.json', {'reason': 'interrupted-update', 'at': now()})
        if (runtime(root) / 'staging-recovery.json').exists():
            raise RuntimeError('Проверьте восстановление тестовой базы перед повторным запуском.')
        if previous.get('state') in BUSY - {'queued'}:
            atomic_json(runtime(root) / 'staging-update.json', {**previous, 'state': 'error'})
        atomic_json(runtime(root) / 'staging-supervisor.json', {'pid': os.getpid(), 'features': ['refresh-data']})
        child = start_http(root)
        try:
            wait_http(root, child, parent_gone)
            while not parent_gone.is_set() and not stop.exists():
                if child.poll() is not None:
                    raise RuntimeError('HTTP-процесс теста завершился.')
                job = read_json(runtime(root) / 'staging-update.json', {})
                if job.get('state') == 'queued':
                    child = (process_refresh if job.get('kind') == 'refresh' else process_job)(root, child, job, parent_gone)
                parent_gone.wait(.5)
        finally:
            try:
                stop_http(child)
            finally:
                (runtime(root) / 'staging-supervisor.json').unlink(missing_ok=True)
