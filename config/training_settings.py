"""A separate process, never a request-scoped database switch."""
from config.settings import *
from django.core.exceptions import ImproperlyConfigured
from uuid import UUID

TRAINING_MODE = True
TRAINING_CONTROL = Path(os.environ['GPR_TRAINING_CONTROL']).resolve()
TRAINING_GENERATION = os.environ['GPR_TRAINING_GENERATION']
try:
    UUID(TRAINING_CONTROL.name)
    UUID(TRAINING_GENERATION)
except ValueError:
    raise ImproperlyConfigured('Неверный идентификатор учебной копии.')
if CONFIG_DIR.parent != TRAINING_CONTROL or CONFIG_DIR.name != TRAINING_GENERATION:
    raise ImproperlyConfigured('Некорректная папка учебной копии.')
if Path(DATABASES['default']['NAME']).resolve() != CONFIG_DIR / 'db.sqlite3':
    raise ImproperlyConfigured('Учебный процесс может открыть только свою базу.')
AUTHENTICATION_BACKENDS = ['django.contrib.auth.backends.ModelBackend']
LDAP_ENABLED = False
LDAP_BIND_PASSWORD = ''
EMAIL_BACKEND = 'django.core.mail.backends.dummy.EmailBackend'
EMAIL_HOST = EMAIL_HOST_USER = EMAIL_HOST_PASSWORD = ''
SESSION_COOKIE_NAME = 'training_' + TRAINING_CONTROL.name.replace('-', '') + '_session'
CSRF_COOKIE_NAME = 'training_' + TRAINING_CONTROL.name.replace('-', '') + '_csrf'
SESSION_COOKIE_AGE = 12 * 60 * 60
STATIC_ROOT = Path(os.environ['GPR_TRAINING_STATIC_ROOT'])
MEDIA_ROOT = CONFIG_DIR / 'media'
ACTUATION_FILES_ROOT = CONFIG_DIR / 'private_uploads' / 'actuation'
# Verify the ticket before the usual company/LDAP checks. Both see only this DB.
MIDDLEWARE.insert(MIDDLEWARE.index('core.impersonation.ActAsMiddleware'), 'apps.accounts.training_middleware.TrainingGuard')
