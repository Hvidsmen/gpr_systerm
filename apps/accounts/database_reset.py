"""Create a separate SQLite database containing accounts and access only."""
import os
from contextlib import closing
from pathlib import Path
import sqlite3
import subprocess
import sys
from uuid import uuid4

ACCOUNT_TABLES = (
    'django_content_type', 'auth_permission', 'auth_group', 'accounts_company',
    'accounts_role', 'accounts_accessgroup', 'accounts_user',
    'auth_group_permissions', 'accounts_user_groups', 'accounts_user_user_permissions',
)
OBJECT_TABLES = ('projects_project', 'projects_constructionobject',
                 'accounts_accessgroup_assigned_objects', 'accounts_user_assigned_objects')


def copy_access(source, destination, keep_objects):
    """Destination must already have the current schema and no user data."""
    tables=ACCOUNT_TABLES+(OBJECT_TABLES if keep_objects else ())
    with closing(sqlite3.connect(Path(source).resolve().as_uri()+'?mode=ro',uri=True)) as original, closing(sqlite3.connect(destination)) as target:
        original.execute('BEGIN')
        source_migrations=set(original.execute('SELECT app,name FROM django_migrations'))
        target_migrations=set(target.execute('SELECT app,name FROM django_migrations'))
        if source_migrations!=target_migrations:
            raise ValueError('Сначала обновите приложение: схемы текущей и новой базы должны совпадать.')
        if original.execute('SELECT COUNT(*) FROM accounts_user').fetchone()[0]==0:
            raise ValueError('В исходной базе нет пользователей. Перенос отменён.')
        if target.execute('SELECT COUNT(*) FROM accounts_user').fetchone()[0]:
            raise ValueError('База назначения уже содержит пользователей.')
        target.execute('PRAGMA foreign_keys=OFF')
        target.execute('BEGIN IMMEDIATE')
        try:
            for table in reversed(tables):target.execute(f'DELETE FROM "{table}"')
            counts={}
            for table in tables:
                columns=[r[1] for r in original.execute(f'PRAGMA table_info("{table}")')]
                expected=[r[1] for r in target.execute(f'PRAGMA table_info("{table}")')]
                if not columns or columns!=expected:raise ValueError(f'Не совпадает схема таблицы {table}.')
                names=','.join('"'+c+'"' for c in columns)
                rows=original.execute(f'SELECT {names} FROM "{table}"').fetchall()
                if rows:target.executemany(f'INSERT INTO "{table}" ({names}) VALUES ({",".join("?" for _ in columns)})',rows)
                counts[table]=len(rows)
            if target.execute('PRAGMA foreign_key_check').fetchone():raise ValueError('Нарушены связи прав доступа. Перенос отменён.')
            target.commit()
        except BaseException:
            target.rollback();raise
        if target.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise ValueError('Проверка новой базы не пройдена.')
        return counts


def create_users_database(source, destination, keep_objects):
    source=Path(source).resolve();destination=Path(destination).resolve()
    if not source.is_file():raise ValueError('Исходная база не найдена.')
    if source==destination or destination.exists():raise ValueError('Укажите новый файл: существующая база не перезаписывается.')
    destination.parent.mkdir(parents=True,exist_ok=True)
    pending=destination.with_name(destination.name+'.building-'+uuid4().hex)
    env={**os.environ,'DJANGO_DB_PATH':str(pending)}
    manage=Path(__file__).resolve().parents[2]/'manage.py'
    try:
        subprocess.run([sys.executable,str(manage),'migrate','--noinput'],cwd=manage.parent,env=env,check=True,stdout=subprocess.DEVNULL)
        counts=copy_access(source,pending,keep_objects)
        # Both files live in the same directory. Hard linking publishes the
        # completed database atomically and fails if the final name exists.
        os.link(pending,destination)
        return counts
    finally:
        for suffix in ('','-wal','-shm','-journal'):Path(str(pending)+suffix).unlink(missing_ok=True)
