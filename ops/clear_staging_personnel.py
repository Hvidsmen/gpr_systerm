"""Clear only rotation assignments and movements in the separate TEST on port 8002."""
import argparse
from contextlib import closing
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import sys
import uuid

TABLES = ('personnel_housingevent', 'personnel_campstay',
          'personnel_staffingslot', 'personnel_shiftpattern')


def runtime(root):
    root = Path(root).resolve()
    if (root/'.env').exists() or (root/'.runtime/installed.json').exists():
        raise ValueError('Production installation refused.')
    config = json.loads((root/'staging.json').read_text(encoding='utf-8'))
    if config.get('kind') != 'gpr-acceptance-v1' or config.get('port') != 8002:
        raise ValueError('Only the separate TEST on port 8002 is allowed.')
    code = Path(config['code']).resolve()
    if code != root and not code.is_relative_to(root/'.runtime/releases'):
        raise ValueError('The selected code must belong to TEST.')
    sys.path.insert(0, str(code))
    from ops import staging, staging_updates
    config = staging.manifest(root)
    for marker in ('staging-recovery.json', 'staging-refresh-transaction.json'):
        if (root/'.runtime'/marker).exists():
            raise ValueError('Pending TEST database recovery; nothing cleared.')
    state = staging.read_json(root/'.runtime/staging-update.json', {})
    if state.get('state') in staging_updates.BUSY:
        raise ValueError('An update or data refresh is active; nothing cleared.')
    return root, config, staging


def clear(root, execute=False):
    root, config, staging = runtime(root)
    database = staging.private_path(root, 'data/db.sqlite3')
    company = config['company_id']
    def counts(db):
        return {table: db.execute(f'SELECT COUNT(*) FROM {table} WHERE company_id=?', [company]).fetchone()[0] for table in TABLES}
    if not execute:
        with closing(sqlite3.connect(database.as_uri()+'?mode=ro', uri=True)) as db:
            return {'port':8002, 'company_id':company, 'would_clear':counts(db)}
    if not (root/'.runtime/server.stop').is_file():
        raise ValueError('Stop the separate TEST task first; nothing cleared.')
    from ops.windows_runtime import singleton, atomic_json
    # Share all installer/queue locks. A live HTTP worker prevents any deletion.
    with singleton(root/'.runtime/staging-queue.lock'), singleton(root/'.runtime/server.lock'), singleton(root/'.runtime/prepare.lock'):
        runtime(root)
        folder = root/'.runtime/backups'/('personnel-clear-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+uuid.uuid4().hex)
        folder.mkdir(parents=True)
        backup = folder/'database.sqlite3'
        staging.snapshot(database, backup)
        with closing(sqlite3.connect(database.as_uri()+'?mode=rw', uri=True)) as db:
            db.execute('PRAGMA foreign_keys=ON')
            with db:
                db.execute('BEGIN IMMEDIATE')
                before = counts(db)
                # Preserve employee cards. Remove their pointer to deleted schedules.
                db.execute('UPDATE personnel_person SET schedule_slot_id=NULL WHERE company_id=?', [company])
                for table in TABLES:
                    db.execute(f'DELETE FROM {table} WHERE company_id=?', [company])
                if any(counts(db).values()):raise RuntimeError('Personnel cleanup was incomplete.')
        result = {'port':8002, 'company_id':company, 'cleared':before, 'backup':str(backup)}
        atomic_json(folder/'result.json', result)
        return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', required=True, type=Path)
    parser.add_argument('--execute', action='store_true')
    args = parser.parse_args()
    try:
        print(json.dumps(clear(args.root, args.execute)));return 0
    except Exception as exc:
        print('TEST personnel cleanup failed: '+str(exc));return 1


if __name__ == '__main__':raise SystemExit(main())
