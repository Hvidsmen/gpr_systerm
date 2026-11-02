"""Permanent acceptance environment, separate from production and training."""
import os
from pathlib import Path
from ops.staging import manifest, private_path

if not os.environ.get('GPR_CONFIG_DIR'):
    raise RuntimeError('Для тестового процесса нужна отдельная папка GPR_CONFIG_DIR.')
_staging_root = Path(os.environ['GPR_CONFIG_DIR']).resolve()
STAGING_CONFIG = manifest(_staging_root, os.environ.get('GPR_STAGING_CANDIDATE'))
os.environ.update(DJANGO_DB_PATH=str(private_path(_staging_root, 'data/db.sqlite3')),
                  DJANGO_SECRET_KEY=private_path(_staging_root, 'secret.key').read_text().strip(),
                  DJANGO_DEBUG='0', DJANGO_SECURE_SSL_REDIRECT='0', DJANGO_COOKIE_SECURE='0',
                  DJANGO_ALLOWED_HOSTS=','.join(sorted(set(STAGING_CONFIG['hosts']) | {'localhost', '127.0.0.1'})))
from config.settings import *

STAGING_MODE = True
STAGING_CONFIG = manifest(CONFIG_DIR, os.environ.get('GPR_STAGING_CANDIDATE'))
DATABASES['default']['NAME'] = private_path(CONFIG_DIR, 'data/db.sqlite3')
MEDIA_ROOT = private_path(CONFIG_DIR, 'data/media')
ACTUATION_FILES_ROOT = private_path(CONFIG_DIR, 'data/private_uploads/actuation')
STATIC_ROOT = private_path(CONFIG_DIR, STAGING_CONFIG['static'])
SESSION_COOKIE_NAME = 'staging_' + STAGING_CONFIG['instance'].replace('-', '') + '_session'
CSRF_COOKIE_NAME = 'staging_' + STAGING_CONFIG['instance'].replace('-', '') + '_csrf'
SESSION_COOKIE_AGE = 12 * 60 * 60
AUTHENTICATION_BACKENDS = ['apps.accounts.staging_access.OwnerBackend']
LDAP_ENABLED = False
LDAP_SERVERS = []
LDAP_BIND_USER = LDAP_BIND_PASSWORD = ''
EMAIL_BACKEND = 'django.core.mail.backends.dummy.EmailBackend'
EMAIL_HOST = EMAIL_HOST_USER = EMAIL_HOST_PASSWORD = ''
INVITATION_ASYNC_ENABLED = False
PORTAL_PUBLIC_URL = 'http://' + STAGING_CONFIG['hosts'][0] + ':' + str(STAGING_CONFIG['port']) + '/'
MIDDLEWARE.insert(MIDDLEWARE.index('core.impersonation.ActAsMiddleware'), 'apps.accounts.staging_access.StagingGuard')
