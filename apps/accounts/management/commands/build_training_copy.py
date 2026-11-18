"""Build and sanitize an isolated SQLite training database from a trusted fixture."""
from pathlib import Path

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db import connection

from core.fixture_loading import fixture_loading


class Command(BaseCommand):
    help = 'Создать отдельную учебную SQLite-базу из снимка данных основного портала.'

    def add_arguments(self, parser):
        parser.add_argument('--fixture', required=True)

    def handle(self, *args, **options):
        if not getattr(settings, 'TRAINING_MODE', False):
            raise CommandError('Команда разрешена только в отдельном учебном процессе.')
        if connection.vendor != 'sqlite':
            raise CommandError('Учебная база должна использовать SQLite.')
        fixture = Path(options['fixture']).resolve()
        generation = Path(settings.CONFIG_DIR).resolve()
        if fixture.parent != generation or not fixture.is_file() or fixture.is_symlink():
            raise CommandError('Файл снимка должен находиться внутри создаваемой учебной копии.')

        call_command('migrate', interactive=False, verbosity=0)
        # Remove migration seed rows; the fixture contains the authoritative
        # production records. Post-migrate recreates content types and
        # permissions, which are deliberately excluded from the fixture.
        call_command('flush', interactive=False, verbosity=0)
        with fixture_loading():
            call_command('loaddata', str(fixture), verbosity=0)
        call_command('prepare_training_copy', verbosity=0)
        self.stdout.write(self.style.SUCCESS('Учебная SQLite-база создана из рабочего снимка и обезличена.'))
