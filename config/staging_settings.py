"""Permanent acceptance environment, separate from production and training."""
import json
import os
from pathlib import Path
from ops.staging import manifest, private_path

if not os.environ.get('GPR_CONFIG_DIR'):
    raise RuntimeError('Для тестового процесса нужна отдельная папка GPR_CONFIG_DIR.')
_staging_root = Path(os.environ['GPR_CONFIG_DIR']).resolve()
STAGING_CONFIG = manifest(_staging_root, os.environ.get('GPR_STAGING_CANDIDATE'))
_engine = str(STAGING_CONFIG.get('database_engine','sqlite')).casefold()
_database_environment = {'DJANGO_DB_ENGINE':'sqlite','DJANGO_DB_PATH':str(private_path(_staging_root,'data/db.sqlite3'))}
if _engine == 'mssql':
    _mssql = STAGING_CONFIG.get('mssql') or {}
    _database_environment = {'DJANGO_DB_ENGINE':'mssql','MSSQL_HOST':str(_mssql.get('host','')),
        'MSSQL_NAME':str(_mssql.get('name','')),'MSSQL_PORT':str(_mssql.get('port','')),
        'MSSQL_DRIVER':str(_mssql.get('driver','ODBC Driver 17 for SQL Server')),
        'MSSQL_TRUST_SERVER_CERTIFICATE':'true' if _mssql.get('trust_server_certificate',True) else 'false'}
os.environ.update(**_database_environment,
                  DJANGO_SECRET_KEY=private_path(_staging_root, 'secret.key').read_text().strip(),
                  DJANGO_DEBUG='0', DJANGO_SECURE_SSL_REDIRECT='0', DJANGO_COOKIE_SECURE='0',
                  DJANGO_ALLOWED_HOSTS=','.join(sorted(set(STAGING_CONFIG['hosts']) | {'localhost', '127.0.0.1'})))
from config.settings import *

# The acceptance refresh command reads production through this dedicated
# alias.  It is never selected as default and no normal request uses it.
_source_root = STAGING_CONFIG.get('source_config_dir')
if _source_root:
    try:
        _source_config = json.loads((Path(_source_root) / '.runtime' / 'installed.json').read_text(encoding='utf-8-sig'))
    except (OSError, ValueError):
        _source_config = {}
    _source_engine = str(_source_config.get('database_engine', 'sqlite')).casefold()
    _source_mssql = _source_config.get('mssql') or {}
    if _source_engine == 'mssql' and str(_source_mssql.get('name', '')).casefold() == 'yds_portal':
        from config.database import database_settings
        _source_environment = {
            'DJANGO_DB_ENGINE': 'mssql', 'MSSQL_HOST': str(_source_mssql.get('host', '')),
            'MSSQL_NAME': str(_source_mssql.get('name', '')), 'MSSQL_PORT': str(_source_mssql.get('port', '')),
            'MSSQL_DRIVER': str(_source_mssql.get('driver', 'ODBC Driver 17 for SQL Server')),
            'MSSQL_TRUST_SERVER_CERTIFICATE':
                'true' if _source_mssql.get('trust_server_certificate', True) else 'false',
            'MSSQL_CONN_MAX_AGE': '0',
        }
        DATABASES['production_mssql'] = database_settings(BASE_DIR, _source_environment)

STAGING_MODE = True
# Explicit owner preference for this isolated environment; production settings stay separate.
AUTH_PASSWORD_VALIDATORS = []
STAGING_CONFIG = manifest(CONFIG_DIR, os.environ.get('GPR_STAGING_CANDIDATE'))
if _engine == 'sqlite':DATABASES['default']['NAME'] = private_path(CONFIG_DIR, 'data/db.sqlite3')
MEDIA_ROOT = private_path(CONFIG_DIR, 'data/media')
ACTUATION_FILES_ROOT = private_path(CONFIG_DIR, 'data/private_uploads/actuation')
STATIC_ROOT = private_path(CONFIG_DIR, STAGING_CONFIG['static'])
SESSION_COOKIE_NAME = 'staging_' + STAGING_CONFIG['instance'].replace('-', '') + '_session'
CSRF_COOKIE_NAME = 'staging_' + STAGING_CONFIG['instance'].replace('-', '') + '_csrf'
SESSION_COOKIE_AGE = 12 * 60 * 60
AUTHENTICATION_BACKENDS = ['apps.accounts.staging_access.OwnerBackend']
# Only administrators' own AD binds are enabled. No directory provisioning,
# group synchronization or service-account secret is inherited.
from ops.staging_auth import directory_settings
STAGING_DIRECTORY = directory_settings(CONFIG_DIR, STAGING_CONFIG)
LDAP_ENABLED = STAGING_DIRECTORY['enabled']
LDAP_SERVERS = []
LDAP_BIND_USER = LDAP_BIND_PASSWORD = ''
EMAIL_BACKEND = 'django.core.mail.backends.dummy.EmailBackend'
EMAIL_HOST = EMAIL_HOST_USER = EMAIL_HOST_PASSWORD = ''
INVITATION_ASYNC_ENABLED = False
PORTAL_PUBLIC_URL = 'http://' + STAGING_CONFIG['hosts'][0] + ':' + str(STAGING_CONFIG['port']) + '/'
MIDDLEWARE.insert(MIDDLEWARE.index('core.impersonation.ActAsMiddleware'), 'apps.accounts.staging_access.StagingGuard')
