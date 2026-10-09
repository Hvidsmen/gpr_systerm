import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

# Monthly editors contain multiple fields per work/resource row. Allow the
# supported formsets to submit together while keeping a finite parser limit.
DATA_UPLOAD_MAX_NUMBER_FIELDS = 25000

from dotenv import load_dotenv
from config.runtime import env_bool, get_secret_key

load_dotenv(BASE_DIR / '.env')
DEBUG = env_bool('DJANGO_DEBUG', True)
SECRET_KEY = get_secret_key(BASE_DIR, DEBUG)
ALLOWED_HOSTS = [host.strip() for host in os.environ.get(
    'DJANGO_ALLOWED_HOSTS', 'localhost,127.0.0.1' if DEBUG else ''
).split(',') if host.strip()]
if not DEBUG and (not ALLOWED_HOSTS or '*' in ALLOWED_HOSTS):
    from django.core.exceptions import ImproperlyConfigured
    raise ImproperlyConfigured('Укажите конкретные домены в DJANGO_ALLOWED_HOSTS.')

CSRF_TRUSTED_ORIGINS = [origin.strip() for origin in os.environ.get(
    'DJANGO_CSRF_TRUSTED_ORIGINS', ''
).split(',') if origin.strip()]
SECURE_SSL_REDIRECT = env_bool('DJANGO_SECURE_SSL_REDIRECT', not DEBUG)
SESSION_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_SECURE = not DEBUG
SECURE_HSTS_SECONDS = 31536000 if not DEBUG else 0
SECURE_HSTS_INCLUDE_SUBDOMAINS = env_bool('DJANGO_HSTS_INCLUDE_SUBDOMAINS')
SECURE_HSTS_PRELOAD = env_bool('DJANGO_HSTS_PRELOAD')
if env_bool('DJANGO_TRUST_PROXY_SSL'):
    # Enable only behind a proxy that strips client-supplied forwarding headers.
    SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'core',
    'apps.accounts',
    'apps.projects',
    'apps.works',
    'apps.planning',
    'apps.rotation',
    'apps.production',
    'apps.resources',
    'apps.analytics',
    'apps.audit',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'core.middleware.CompanyAccessMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'config.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'config.wsgi.application'

database_path = Path(os.environ.get('DJANGO_DB_PATH', str(BASE_DIR / 'db.sqlite3')))
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': database_path if database_path.is_absolute() else BASE_DIR / database_path,
    }
}

AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
]

AUTH_USER_MODEL = 'accounts.User'

LANGUAGE_CODE = 'ru-ru'
TIME_ZONE = 'Europe/Moscow'
USE_I18N = True
USE_TZ = True

STATIC_URL = '/static/'
STATICFILES_DIRS = [BASE_DIR / 'static'] if (BASE_DIR / 'static').is_dir() else []
STATIC_ROOT = BASE_DIR / 'staticfiles'

MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR / 'media'

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

LOGIN_REDIRECT_URL = '/dashboard/'
LOGOUT_REDIRECT_URL = '/accounts/login/'
LOGIN_URL = '/accounts/login/'
