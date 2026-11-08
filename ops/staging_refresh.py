"""Replace acceptance data from a read-only production snapshot, while stopped."""
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
import shutil
import logging
import sqlite3
import uuid

from ops import staging
from ops.windows_runtime import atomic_json, singleton, retain_directories


def generation(state):
    return state.get('data_generation', state['instance'])


def owner_hash(root, state):
    database = staging.private_path(root, 'data/db.sqlite3')
    with closing(sqlite3.connect(database.as_uri() + '?mode=ro', uri=True)) as db:
        row = db.execute('SELECT password FROM accounts_user WHERE id=? AND company_id=?',
                         [state['owner_id'], state['company_id']]).fetchone()
    if not row or not row[0] or row[0].startswith('!'):
        raise ValueError('Не найден действующий отдельный пароль владельца теста.')
    return row[0]


def refresh(root, *, expected_generation, expected_revision, owner_id, progress=lambda phase: None):
    root = Path(root).resolve()
    state = staging.manifest(root)
    runtime = staging.private_path(root, '.runtime')
    if (runtime / 'staging-recovery.json').exists() or (runtime / 'staging-refresh-transaction.json').exists():
        raise RuntimeError('Сначала проверьте восстановление предыдущей операции теста.')
    with singleton(runtime / 'server.lock'), singleton(runtime / 'prepare.lock'):
        if (generation(state) != expected_generation or state['revision'] != expected_revision or
                state['owner_id'] != owner_id):
            raise ValueError('Тестовые данные или версия изменились. Подтвердите обновление данных заново.')
        password_hash = owner_hash(root, state)
        identifier = uuid.uuid4().hex
        backup = staging.private_path(root, '.runtime/backups/' + identifier)
        pending = staging.private_path(root, '.runtime/refresh/' + identifier)
        data = staging.private_path(root, 'data')
        backup.mkdir(parents=True)
        pending.mkdir(parents=True)
        (pending / 'data').mkdir()
        journal = runtime / 'staging-refresh-transaction.json'
        old_moved = new_moved = False
        try:
            progress('backing_up')
            staging.snapshot(data / 'db.sqlite3', backup / 'database.sqlite3')
            atomic_json(backup / 'staging.json', state)
            shutil.copy2(root / 'secret.key', backup / 'secret.key')
            progress('copying')
            # Backup API opens the source with mode=ro. No production ORM,
            # migrations, credentials or scheduled tasks are used here.
            staging.snapshot(state['source']['database'], pending / 'data/db.sqlite3')
            new_owner, new_company = staging.find_owner(pending / 'data/db.sqlite3', state['owner_login'])
            staging.copy_files(state['source']['media'], pending / 'data/media')
            staging.copy_files(state['source']['actuation'], pending / 'data/private_uploads/actuation')
            refreshed_at = datetime.now(timezone.utc).isoformat()
            refreshed = {**state, 'owner_id': new_owner, 'company_id': new_company,
                         'data_generation': identifier, 'data_refreshed_at': refreshed_at}
            # A temporary isolated config makes management commands see only
            # the pending DB/files, using the already installed test code.
            atomic_json(pending / 'staging.json', {**refreshed, 'code': str(pending)})
            shutil.copy2(root / 'secret.key', pending / 'secret.key')
            progress('sanitizing')
            for command, secret in [(['migrate', '--noinput'], None),
                    (['prepare_staging_copy', '--owner-password-hash'], password_hash), (['check'], None)]:
                staging.run_manage(pending, command, password=secret, code=Path(state['code']), python=state['python'])
            progress('swapping')
            atomic_json(journal, {'backup': str(backup), 'pending': str(pending), 'revision': state['revision']})
            data.rename(backup / 'data')
            old_moved = True
            (pending / 'data').rename(data)
            new_moved = True
            atomic_json(root / 'staging.json', refreshed)
            journal.unlink()
            retain_directories(runtime / 'backups')
            return refreshed
        except Exception:
            if old_moved:
                try:
                    if new_moved:
                        data.rename(pending / 'failed-data')
                    (backup / 'data').rename(data)
                    atomic_json(root / 'staging.json', state)
                    journal.unlink(missing_ok=True)
                except Exception:
                    atomic_json(runtime / 'staging-recovery.json',
                                {'backup': str(backup), 'reason': 'refresh-rollback-failed'})
                    raise
            raise
        finally:
            log = pending / '.runtime/prepare.log'
            if log.exists():
                try:
                    with (runtime / 'prepare.log').open('ab') as output:
                        output.write(b'\n--- Acceptance data refresh ---\n')
                        output.write(log.read_bytes())
                except OSError:
                    logging.exception('Could not append acceptance refresh diagnostics')
            # Preserve pending files and journal if recovery is needed.
            if not journal.exists() and not (runtime / 'staging-recovery.json').exists():
                shutil.rmtree(pending, ignore_errors=True)
