import os
import secrets
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured


def env_bool(name, default=False):
    value = os.environ.get(name)
    if value is None:
        return default
    if value.lower() in {'1', 'true', 'yes', 'on'}:
        return True
    if value.lower() in {'0', 'false', 'no', 'off'}:
        return False
    raise ImproperlyConfigured(f'{name} должен иметь значение 0 или 1.')


def get_secret_key(base_dir, debug):
    key = os.environ.get('DJANGO_SECRET_KEY', '').strip()
    if key:
        if not debug and (len(key) < 50 or key.startswith('django-insecure-')):
            raise ImproperlyConfigured('DJANGO_SECRET_KEY должен быть новым случайным ключом длиной не менее 50 символов.')
        return key
    if not debug:
        raise ImproperlyConfigured('Для рабочего сервера требуется DJANGO_SECRET_KEY.')
    # Keep development sessions valid across restarts without committing a secret.
    path = Path(base_dir) / '.dev-secret-key'
    if not path.exists():
        try:
            with path.open('x') as file:
                path.chmod(0o600)
                file.write(secrets.token_urlsafe(64))
        except FileExistsError:
            pass
    key = path.read_text().strip()
    if not key:
        raise ImproperlyConfigured('Локальный файл .dev-secret-key пуст; восстановите его или задайте DJANGO_SECRET_KEY.')
    return key
