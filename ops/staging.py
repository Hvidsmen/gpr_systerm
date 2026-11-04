"""Persistent acceptance portal. Its runner never updates or stops production."""
import argparse
from contextlib import closing
import getpass
import json
import logging
import os
from pathlib import Path
import secrets
import shutil
import socket
import sqlite3
import subprocess
import sys
import threading
import time
import uuid
import zipfile

CODE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CODE_ROOT))
from ops.windows_runtime import atomic_json, read_json, singleton, log_setup, extract_archive, python_at


KIND = 'gpr-acceptance-v1'


def disjoint(left, right):
    left, right = Path(left).resolve(), Path(right).resolve()
    if left == right or left.is_relative_to(right) or right.is_relative_to(left):
        raise ValueError('Рабочие и тестовые папки должны быть полностью раздельными.')


def private_path(root, relative):
    root = Path(root).resolve()
    target = root / relative
    if target.resolve() != target.absolute():
        raise ValueError('Ссылки и junction в тестовых путях запрещены.')
    if not target.resolve().is_relative_to(root):
        raise ValueError('Тестовый путь выходит за пределы своей папки.')
    return target


def manifest(root, candidate=None):
    root = Path(root).resolve()
    if candidate is not None:
        candidate = Path(candidate).resolve()
        if not candidate.is_relative_to(root / '.runtime'):raise ValueError('Конфигурация кандидата должна быть внутри теста.')
    data = read_json(candidate or root / 'staging.json', {})
    if data.get('kind') != KIND:
        raise ValueError('Не найдена конфигурация отдельного тестового портала.')
    uuid.UUID(data['instance'])
    if not data.get('owner_id') or not data.get('company_id') or not data.get('owner_login'):
        raise ValueError('Не указан единственный владелец тестового портала.')
    if not 1024 <= data['port'] <= 65535:
        raise ValueError('Некорректный порт тестового портала.')
    for relative in ['data/db.sqlite3', 'data/media', 'data/private_uploads/actuation', 'staticfiles', '.runtime', 'secret.key']:
        private_path(root, relative)
    code = Path(data['code']).resolve()
    if code != root and not code.is_relative_to(root / '.runtime' / 'releases'):
        raise ValueError('Код теста должен быть внутри его собственной установки.')
    private_path(root, str(code.relative_to(root)))
    private_path(root, data['static'])
    for name in ['database', 'media', 'actuation']:
        disjoint(root, data['source'][name])
    if data.get('source_config_dir'):
        disjoint(root, data['source_config_dir'])
    return data


def environment(root, candidate=None):
    # Do not inherit live DB paths, .env, LDAP, SMTP, 1C or update credentials.
    env = {key: value for key, value in os.environ.items() if key.upper() in {
        'PATH', 'SYSTEMROOT', 'WINDIR', 'COMSPEC', 'PATHEXT', 'TEMP', 'TMP', 'LANG', 'LC_ALL'}}
    env.update(DJANGO_SETTINGS_MODULE='config.staging_settings', GPR_CONFIG_DIR=str(Path(root).resolve()),
               PYTHONUNBUFFERED='1')
    if candidate is not None:env['GPR_STAGING_CANDIDATE'] = str(candidate)
    return env


def dependency_environment(root):
    env = environment(root)
    for key, value in os.environ.items():
        if key.upper() in {'HTTP_PROXY', 'HTTPS_PROXY', 'NO_PROXY', 'ALL_PROXY', 'SSL_CERT_FILE', 'SSL_CERT_DIR',
                           'REQUESTS_CA_BUNDLE', 'CURL_CA_BUNDLE', 'PIP_CERT', 'PIP_INDEX_URL', 'PIP_EXTRA_INDEX_URL'}:
            env[key] = value
    return env


def snapshot(source, target):
    source, target = Path(source).resolve(), Path(target).resolve()
    if not source.is_file() or source == target or target.exists():
        raise ValueError('Нужна существующая рабочая база и новый отдельный файл снимка.')
    started = time.monotonic()
    def progress(*_):
        if time.monotonic() - started > 600:
            raise RuntimeError('Превышено время создания снимка.')
    with closing(sqlite3.connect(source.as_uri() + '?mode=ro', uri=True, timeout=10)) as live:
        with closing(sqlite3.connect(target)) as copy:
            live.backup(copy, pages=256, progress=progress, sleep=.05)
            if copy.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise RuntimeError('Снимок не прошёл проверку SQLite.')


def copy_files(source, target):
    source, target = Path(source).resolve(), Path(target).resolve()
    disjoint(source, target)
    if not source.exists():
        target.mkdir(parents=True)
        return
    for item in source.rglob('*'):
        if item.is_symlink() or getattr(item, 'is_junction', lambda: False)() or not item.resolve().is_relative_to(source):
            raise ValueError('В исходных файлах есть ссылки на другие папки. Копирование отменено.')
    shutil.copytree(source, target)


def find_owner(database, login):
    # A copied snapshot only; no ORM connection to the working database.
    login = login.strip()
    candidates = {login.casefold()}
    if '\\' in login:candidates.add(login.rsplit('\\', 1)[-1].casefold())
    if '@' in login:candidates.add(login.rsplit('@', 1)[0].casefold())
    with closing(sqlite3.connect(database)) as db:
        rows = db.execute('SELECT id,company_id,username,ldap_login,is_active,archived_at FROM accounts_user').fetchall()
    matches = [row for row in rows if row[4] and row[5] is None and row[1] and
               ({str(row[2]).casefold(), str(row[3]).casefold()} & candidates)]
    if len(matches) != 1:
        raise ValueError('Логин должен однозначно указывать на одного активного пользователя рабочей компании.')
    return matches[0][0], matches[0][1]


def run_manage(root, command, *, password=None, code=None, python=None, candidate=None):
    from ops.staging_venv import run_setup
    code = Path(code or CODE_ROOT)
    timeout=120 if command[0]=='check' else 1800
    run_setup([python or sys.executable,'-I','-u',str(CODE_ROOT/'ops/staging_manage.py'),'--code',str(code),*command],
              cwd=code,env=environment(root,candidate),log=Path(root)/'.runtime/prepare.log',
              label='Django '+command[0],timeout=timeout,input_data=password.encode() if password is not None else None)


def prepare(root, source_root, login, password, *, port=8002, hosts=None, revision='', actuation=None):
    root, source_root = Path(root).resolve(), Path(source_root).resolve()
    disjoint(root, source_root)
    if root != CODE_ROOT:raise ValueError('Подготовку запускайте из папки кода тестового портала (--root).')
    if not login.strip():raise ValueError('Укажите логин владельца теста.')
    if not 1 <= len(password) <= 4096:
        raise ValueError('Введите непустой тестовый пароль длиной не более 4096 символов.')
    if len(revision) != 40 or any(c not in '0123456789abcdef' for c in revision):
        raise ValueError('Укажите точный SHA версии (40 символов), а не ветку main.')
    if not 1024 <= port <= 65535:
        raise ValueError('Неверный порт.')
    config = read_json(source_root / '.runtime' / 'installed.json', {})
    if not config.get('database') or not config.get('media'):
        raise ValueError('Нужна папка установленного рабочего портала с .runtime/installed.json.')
    if port == config.get('port', 8001):
        raise ValueError('Тест не может использовать порт рабочего портала.')
    source = {'database': str(Path(config['database']).resolve()), 'media': str(Path(config['media']).resolve()),
              'actuation': str(Path(actuation or source_root / 'private_uploads' / 'actuation').resolve())}
    for path in source.values():disjoint(root, path)
    if (root / '.env').exists():
        raise ValueError('Уберите .env из тестовой папки: рабочие настройки и секреты сюда не копируются.')
    for name in ['staging.json', 'data', 'secret.key', 'staticfiles']:
        if (root / name).exists():raise ValueError('Тест уже подготовлен или папка занята. Существующие данные не перезаписываются.')
    hosts = hosts or config.get('hosts') or ['192.168.70.9']
    if not hosts or any(not h or any(c in h for c in '/*\\:@ \t\n') for h in hosts):
        raise ValueError('Нужны конкретные имена сервера без протокола и порта.')
    with socket.socket() as check:check.bind(('0.0.0.0', port))
    runtime = root / '.runtime'; runtime.mkdir(parents=True, exist_ok=True)
    with singleton(runtime / 'prepare.lock'):
        pending = runtime / ('preparing-' + uuid.uuid4().hex); pending.mkdir()
        try:
            (pending / 'data').mkdir()
            snapshot(source['database'], pending / 'data' / 'db.sqlite3')
            owner_id, company_id = find_owner(pending / 'data' / 'db.sqlite3', login)
            state = {'kind': KIND, 'instance': str(uuid.uuid4()), 'owner_id': owner_id, 'company_id': company_id,
                     'owner_login': login.strip(), 'port': port, 'hosts': hosts, 'revision': revision, 'source': source,
                     'source_config_dir': str(source_root),
                     'code': str(root), 'python': sys.executable, 'static': 'staticfiles',
                     'production_url': 'http://' + (config.get('hosts') or hosts)[0] + ':' + str(config.get('port', 8001)) + '/'}
            preparing_state = {**state, 'code': str(pending)}
            atomic_json(pending / 'staging.json', preparing_state)
            key = pending / 'secret.key'; key.write_text(secrets.token_urlsafe(64)); key.chmod(0o600)
            # No external integrations run in these management processes.
            run_manage(pending, ['migrate', '--noinput'])
            run_manage(pending, ['prepare_staging_copy'], password=password)
            copy_files(source['media'], pending / 'data' / 'media')
            copy_files(source['actuation'], pending / 'data' / 'private_uploads' / 'actuation')
            run_manage(pending, ['collectstatic', '--noinput'])
            run_manage(pending, ['check'])
            for name in ['data', 'secret.key', 'staticfiles']:(pending / name).rename(root / name)
            shutil.copy2(pending / '.runtime' / 'prepare.log', runtime / 'prepare.log')
            atomic_json(root / 'staging.json', state)  # Publish only a completed copy.
            return state
        except Exception:
            log = pending / '.runtime' / 'prepare.log'
            if log.exists():shutil.copy2(log, runtime / 'prepare.log')
            raise
        finally:
            shutil.rmtree(pending, ignore_errors=True)


def serve(root):
    root = Path(root).resolve(); state = manifest(root)
    log_setup(root / '.runtime', 'server.log')
    if Path(state['code']).resolve() != CODE_ROOT:
        child_env = environment(root); child_env['GPR_STAGING_PARENT_PIPE'] = '1'
        child = subprocess.Popen([state['python'], str(Path(state['code']) / 'ops/staging.py'), 'serve', '--root', str(root)], env=child_env, stdin=subprocess.PIPE)
        try:
            if child.wait():raise RuntimeError('Тестовый процесс завершился с ошибкой.')
        finally:
            if child.stdin:child.stdin.close()
        return
    from ops.staging_updates import supervise
    parent_gone = threading.Event()
    if os.environ.get('GPR_STAGING_PARENT_PIPE') == '1':
        def monitor_parent():
            sys.stdin.buffer.read(1); parent_gone.set()
        threading.Thread(target=monitor_parent, daemon=True).start()
    supervise(root, parent_gone)


def serve_http(root):
    root = Path(root).resolve(); state = manifest(root)
    if Path(state['code']).resolve() != CODE_ROOT:
        raise ValueError('HTTP-процесс должен соответствовать выбранной тестовой версии.')
    if (root / '.runtime/staging-recovery.json').exists():
        raise RuntimeError('Тестовая база требует проверки восстановления.')
    log_setup(root / '.runtime', 'server.log')
    watch_parent = os.environ.get('GPR_STAGING_PARENT_PIPE') == '1'
    env = environment(root)
    os.environ.clear(); os.environ.update(env)
    sys.path.insert(0, str(CODE_ROOT))
    os.chdir(CODE_ROOT)
    from django.core.wsgi import get_wsgi_application
    from django.conf import settings
    from waitress import create_server
    from whitenoise import WhiteNoise
    runtime = root / '.runtime'; runtime.mkdir(exist_ok=True)
    with singleton(runtime / 'server.lock'):
        stop = runtime / 'server.stop'
        application = WhiteNoise(get_wsgi_application(), root=str(settings.STATIC_ROOT), prefix=settings.STATIC_URL)
        server = create_server(application, listen='0.0.0.0:' + str(state['port']), threads=4,
                               max_request_body_size=128 * 1024 * 1024, channel_timeout=120)
        thread = threading.Thread(target=server.run, daemon=True); thread.start()
        parent_gone = threading.Event()
        if watch_parent:
            def monitor_parent():
                sys.stdin.buffer.read(1); parent_gone.set()
            threading.Thread(target=monitor_parent, daemon=True).start()
        atomic_json(runtime / 'status.json', {'state': 'running', 'revision': state['revision'], 'port': state['port']})
        try:
            while thread.is_alive() and not stop.exists() and not parent_gone.is_set():thread.join(.5)
        finally:
            server.close(); server.task_dispatcher.shutdown(timeout=90); thread.join(timeout=10)
            atomic_json(runtime / 'status.json', {'state': 'stopped', 'revision': state['revision'], 'port': state['port']})



def update_inputs(root, revision):
    root=Path(root).resolve();previous=manifest(root)
    if len(revision)!=40 or any(c not in '0123456789abcdef' for c in revision):
        raise ValueError('Нужен точный SHA проверяемого релиза, а не ветка main.')
    if (root/'.runtime/staging-recovery.json').exists():
        raise ValueError('Сначала проверьте восстановление тестовой базы и снимите маркер восстановления.')
    return root,previous


def prepare_release(root, archive, revision, previous, progress):
    """Build only a private candidate. The selected TEST can keep serving requests."""
    runtime=root/'.runtime';release=private_path(root,'.runtime/releases/'+revision)
    if release.exists():raise ValueError('Эта версия уже подготовлена. Не перезаписывайте существующий релиз.')
    with zipfile.ZipFile(archive) as zipped:
        roots={item.filename.split('/')[0] for item in zipped.infolist()}
    if roots!={'gpr_systerm-'+revision}:raise ValueError('Архив должен быть выгрузкой GitHub именно указанного SHA.')
    release.mkdir(parents=True);code=release/'code';extract_archive(Path(archive),code)
    if not (code/'config/staging_settings.py').is_file() or not (code/'ops/staging.py').is_file():
        raise ValueError('Архив должен поддерживать отдельный тестовый портал.')
    from ops.staging_venv import create_test_venv,run_setup
    progress('Создание отдельного Python-окружения')
    python=create_test_venv(release/'venv',env=dependency_environment(root),log=runtime/'prepare.log')
    requirements=code/'requirements.staging.lock'
    if not requirements.is_file():raise ValueError('В релизе отсутствует фиксированный requirements.staging.lock.')
    progress('Установка фиксированных зависимостей')
    run_setup([python,'-I','-m','pip','install','--no-input','--disable-pip-version-check','-r',str(requirements)],
              env=dependency_environment(root),log=runtime/'prepare.log',label='Install pinned TEST dependencies',timeout=1800)
    candidate={**previous,'revision':revision,'code':str(code),'python':python,
               'static':str((release/'staticfiles').relative_to(root))}
    candidate_file=runtime/('candidate-'+uuid.uuid4().hex+'.json');atomic_json(candidate_file,candidate)
    try:
        progress('Проверка новой версии Django; тест продолжает работать')
        run_manage(root,['check'],code=code,python=python,candidate=candidate_file)
    except BaseException:
        candidate_file.unlink(missing_ok=True);raise
    return {'root':str(root),'previous':previous,'candidate':candidate,'candidate_file':str(candidate_file)}


def prepare_update(root, archive, revision, *, progress=lambda label:None):
    root,previous=update_inputs(root,revision)
    with singleton(root/'.runtime/prepare.lock'):
        return prepare_release(root,archive,revision,previous,progress)


def update(root, archive, revision, *, prepared=None, progress=lambda label:None):
    """Apply a checked candidate only while TEST HTTP is stopped; back up latest data."""
    root,previous=update_inputs(root,revision);runtime=root/'.runtime'
    with singleton(runtime/'server.lock'),singleton(runtime/'prepare.lock'):
        if prepared is None:prepared=prepare_release(root,archive,revision,previous,progress)
        if prepared.get('root')!=str(root) or prepared.get('previous')!=previous:
            raise ValueError('Тестовая версия изменилась после подготовки. Повторите обновление.')
        candidate=prepared['candidate'];candidate_file=private_path(root,str(Path(prepared['candidate_file']).relative_to(root)))
        release=private_path(root,'.runtime/releases/'+revision)
        expected={**previous,'revision':revision,'code':str(release/'code'),'python':python_at(release/'venv'),
                  'static':str((release/'staticfiles').relative_to(root))}
        if (candidate!=expected or
                candidate_file.parent!=runtime or not candidate_file.name.startswith('candidate-') or
                read_json(candidate_file,{})!=candidate or manifest(root,candidate_file)!=candidate):
            raise ValueError('Конфигурация подготовленной тестовой версии не совпала.')
        backup=private_path(root,'.runtime/backups/'+uuid.uuid4().hex);backup.mkdir(parents=True)
        try:
            progress('Резервная копия последних тестовых данных')
            snapshot(root/'data/db.sqlite3',backup/'database.sqlite3')
            atomic_json(backup/'staging.json',previous)
            try:
                for command,label in [(['migrate','--noinput'],'Применение миграций тестовой базы'),
                                      (['collectstatic','--noinput'],'Подготовка статических файлов')]:
                    progress(label)
                    run_manage(root,command,code=Path(candidate['code']),python=candidate['python'],candidate=candidate_file)
                atomic_json(root/'staging.json',candidate)
            except Exception:
                # The complete backup was taken after HTTP stopped, so no user writes are lost.
                try:
                    for suffix in ['-wal','-shm']:Path(str(root/'data/db.sqlite3')+suffix).unlink(missing_ok=True)
                    shutil.copy2(backup/'database.sqlite3',root/'data/db.sqlite3')
                except Exception:
                    atomic_json(runtime/'staging-recovery.json',{'backup':str(backup),'revision':previous['revision']})
                    raise
                raise
        finally:candidate_file.unlink(missing_ok=True)
        print('Updated TEST only. Revision: '+revision+'. Start its separate scheduled task.')


def prompt_password():
    """Validate before preparation and allow corrections without restarting setup."""
    while True:
        try:
            password = getpass.getpass('Новый отдельный пароль для тестового портала (можно короткий): ')
            if not 1 <= len(password) <= 4096:
                print('Пароль не должен быть пустым или длиннее 4096 символов. Введите другой пароль.', file=sys.stderr)
                continue
            confirmation = getpass.getpass('Повторите тестовый пароль: ')
        except EOFError:
            raise ValueError('Ввод пароля отменён. Установку можно запустить повторно.') from None
        if password == confirmation:
            return password
        print('Пароли не совпали. Введите пароль заново.', file=sys.stderr)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['prepare', 'serve', 'http', 'stop', 'status', 'update'])
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--source-root', type=Path)
    parser.add_argument('--owner-login')
    parser.add_argument('--port', type=int, default=8002)
    parser.add_argument('--host', action='append')
    parser.add_argument('--revision', default='')
    parser.add_argument('--actuation-root', type=Path)
    parser.add_argument('--archive', type=Path)
    args = parser.parse_args()
    if args.command == 'prepare':
        if not args.source_root or not args.owner_login:parser.error('Нужны --source-root и --owner-login.')
        password = prompt_password()
        state = prepare(args.root, args.source_root, args.owner_login, password, port=args.port, hosts=args.host,
                        revision=args.revision, actuation=args.actuation_root)
        print('Тест подготовлен: http://' + state['hosts'][0] + ':' + str(state['port']) + '/; логин: ' + state['owner_login'])
    elif args.command == 'serve':serve(args.root)
    elif args.command == 'http':serve_http(args.root)
    elif args.command == 'update':
        if not args.archive:parser.error('Нужен --archive: архив выбранного релиза.')
        update(args.root, args.archive, args.revision)
    elif args.command == 'stop':
        manifest(args.root); (args.root / '.runtime' / 'server.stop').touch(); print('Остановка только тестового портала запрошена.')
    else:
        manifest(args.root); print(json.dumps(read_json(args.root / '.runtime' / 'status.json', {'state': 'not-started'})))


if __name__ == '__main__':
    try:main()
    except KeyboardInterrupt:
        print('Установка отменена. Её можно запустить повторно.', file=sys.stderr); sys.exit(130)
    except Exception as error:
        if logging.getLogger().handlers:logging.exception('Test portal command failed')
        print(str(error), file=sys.stderr); sys.exit(1)
