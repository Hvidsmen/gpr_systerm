"""Read production administrator identities and directory options without writes."""
from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import subprocess

from dotenv import dotenv_values

DIRECTORY_KEYS = {'LDAP_ENABLED', 'LDAP_SERVERS', 'LDAP_PORT', 'LDAP_DOMAIN', 'LDAP_BASE_DN', 'LDAP_TIMEOUT'}


def production_root(root, state):
    from ops.staging import disjoint
    database = Path(state['source']['database']).resolve()
    candidates = []
    if state.get('source_config_dir'):
        candidates.append(Path(state['source_config_dir']))
    for key in ['database', 'media']:
        path = Path(state['source'][key]).resolve()
        candidates.extend(list(path.parents)[:4])
    def valid(candidate):
        try:
            candidate = candidate.resolve()
            disjoint(root, candidate)
            data = json.loads((candidate / '.runtime/installed.json').read_text(encoding='utf-8-sig'))
            return candidate if Path(data['database']).resolve() == database else None
        except (OSError, ValueError, KeyError, TypeError):
            return None
    for candidate in dict.fromkeys(candidates):
        resolved = valid(candidate)
        if resolved is not None:
            return resolved
    if os.name == 'nt':
        # Compatibility with old manifests and databases outside the code folder.
        # Query only the production task's working directory; never start/stop it.
        command = '[Console]::OutputEncoding=[Text.Encoding]::UTF8;(Get-ScheduledTask -TaskName "YDS Portal").Actions.WorkingDirectory | ConvertTo-Json -Compress'
        try:
            result = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', command],
                                    capture_output=True, timeout=12, check=True)
            value = json.loads(result.stdout.decode('utf-8-sig'))
            if isinstance(value, str):return valid(Path(value))
        except (OSError, ValueError, subprocess.SubprocessError):
            pass
    return None


def directory_settings(root, state):
    values = {key: os.environ[key] for key in DIRECTORY_KEYS if key in os.environ}
    if os.name == 'nt':
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r'SYSTEM\CurrentControlSet\Control\Session Manager\Environment') as environment:
                for key in DIRECTORY_KEYS:
                    try:values[key] = str(winreg.QueryValueEx(environment, key)[0])
                    except OSError:pass
        except OSError:
            pass
    source = production_root(root, state)
    if source is not None:
        parsed = dotenv_values(source / '.env', interpolate=False)
        values.update({key: value for key, value in parsed.items() if key in DIRECTORY_KEYS and value is not None})
    return dict(enabled=values.get('LDAP_ENABLED', '').strip().lower() in {'1','true','yes','on'},
        servers=[v.strip() for v in values.get('LDAP_SERVERS', '192.168.70.12,192.168.70.13').split(',') if v.strip()],
        port=int(values.get('LDAP_PORT', 389)), domain=values.get('LDAP_DOMAIN', 'yamalgazprom.local'),
        base_dn=values.get('LDAP_BASE_DN', 'OU=Yamaldorstroy,DC=yamalgazprom,DC=local'),
        timeout=int(values.get('LDAP_TIMEOUT', 5)))


def administrators(state):
    path = state.get('source', {}).get('database')
    if not path:raise OSError('Production authentication database is not configured')
    database = Path(path).resolve()
    if not database.is_file():raise OSError('Production authentication database is unavailable')
    with closing(sqlite3.connect(database.as_uri() + '?mode=ro', uri=True, timeout=5)) as db:
        db.row_factory = sqlite3.Row
        rows = db.execute('''SELECT u.id,u.company_id,u.username,u.auth_source,u.ldap_login,u.ldap_guid,
            u.password,u.first_name,u.last_name,u.email FROM accounts_user u
            LEFT JOIN accounts_role r ON r.id=u.role_id
            WHERE u.is_active=1 AND u.archived_at IS NULL AND u.company_id IS NOT NULL
                AND (r.code='ADMIN' OR u.is_superuser=1)''').fetchall()
    return [dict(row) for row in rows]


def login_matches(person, login, domain=''):
    value = (login or '').strip().casefold()
    if value == person['username'].casefold():return True
    if person['auth_source'] != 'LDAP':return False
    if '\\' in value:
        prefix, value = value.split('\\', 1)
        if prefix not in {domain.casefold(), domain.split('.')[0].casefold()}:return False
    if '@' in value:
        value, suffix = value.rsplit('@', 1)
        if suffix != domain.casefold():return False
    return bool(person['ldap_login']) and value == person['ldap_login'].casefold()


def same_identity(user, person):
    if person['auth_source'] == 'LDAP':
        return bool(user.ldap_guid and person['ldap_guid']) and str(user.ldap_guid).replace('-', '').casefold() == person['ldap_guid'].replace('-', '').casefold()
    return user.pk == person['id'] and user.username.casefold() == person['username'].casefold() and user.auth_source == 'LOCAL'
