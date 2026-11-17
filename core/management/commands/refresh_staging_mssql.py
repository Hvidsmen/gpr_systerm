"""Replace TEST MSSQL data from production MSSQL, then sanitize it."""
import json
from pathlib import Path

from django.conf import settings
from django.contrib.auth.hashers import identify_hasher, is_password_usable
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db import connections

from apps.accounts.management.commands.prepare_staging_copy import sanitize_database
from apps.accounts.models import User
from core.fixture_loading import fixture_loading
from core.management.commands.prepare_mssql_copy import dump_fixture, compare_databases


def login_candidates(login):
    values = {login.strip().casefold()}
    if '\\' in login:
        values.add(login.rsplit('\\', 1)[-1].casefold())
    if '@' in login:
        values.add(login.rsplit('@', 1)[0].casefold())
    return values


def production_owner(alias, login):
    candidates = login_candidates(login)
    rows = User.objects.using(alias).filter(is_active=True, archived_at=None,
                                             company__isnull=False).values_list(
        'id', 'company_id', 'username', 'ldap_login')
    matches = [row for row in rows if {str(row[2]).casefold(), str(row[3]).casefold()} & candidates]
    if len(matches) != 1:
        raise CommandError('Логин владельца теста должен однозначно указывать на одного активного рабочего пользователя.')
    return matches[0][0], matches[0][1]


class Command(BaseCommand):
    help = 'Безопасно заменить YDS_PORTAL_TEST данными из YDS_PORTAL.'

    def add_arguments(self, parser):
        parser.add_argument('--output', required=True)

    def handle(self, *args, **options):
        if not getattr(settings, 'STAGING_MODE', False):
            raise CommandError('Команда разрешена только в отдельном тестовом портале.')
        source, target = 'production_mssql', 'default'
        if source not in connections:
            raise CommandError('Рабочая MSSQL не настроена как источник тестовых данных.')
        if connections[source].vendor not in {'microsoft', 'mssql'} or connections[target].vendor not in {'microsoft', 'mssql'}:
            raise CommandError('Для обновления обе базы должны работать на MSSQL.')
        source_name = str(connections[source].settings_dict['NAME'])
        target_name = str(connections[target].settings_dict['NAME'])
        if source_name.casefold() != 'yds_portal' or target_name.casefold() != 'yds_portal_test':
            raise CommandError('Разрешено только копирование YDS_PORTAL в отдельную YDS_PORTAL_TEST.')
        if (str(connections[source].settings_dict.get('HOST', '')).casefold(), source_name.casefold()) == (
                str(connections[target].settings_dict.get('HOST', '')).casefold(), target_name.casefold()):
            raise CommandError('Рабочая и тестовая базы должны быть раздельными.')

        state = settings.STAGING_CONFIG
        try:
            password_hash = User.objects.using(target).get(
                pk=state['owner_id'], company_id=state['company_id'], archived_at=None).password
            if not is_password_usable(password_hash):
                raise ValueError()
            identify_hasher(password_hash)
        except (User.DoesNotExist, ValueError) as exc:
            raise CommandError('Не найден действующий отдельный пароль владельца теста.') from exc

        owner_id, company_id = production_owner(source, state['owner_login'])
        output = Path(options['output']).resolve()
        output.mkdir(parents=True, exist_ok=False)
        fixture = output / 'portal-data.json'
        report_path = output / 'verification.json'
        result_path = output / 'result.json'

        self.stdout.write('Выгружаем рабочие данные из YDS_PORTAL…')
        dump_fixture(source, fixture)
        self.stdout.write('Очищаем только YDS_PORTAL_TEST и загружаем свежую копию…')
        call_command('flush', database=target, interactive=False, verbosity=0)
        with fixture_loading():
            call_command('loaddata', str(fixture), database=target, verbosity=1)
        report, errors = compare_databases(source, target)
        report_path.write_text(json.dumps({'source': source_name, 'target': target_name,
            'models': report, 'errors': errors}, ensure_ascii=False, indent=2), encoding='utf-8')
        if errors:
            raise CommandError('Сверка рабочей и тестовой копий не пройдена: ' + '; '.join(errors[:10]))
        sanitize_database(password_hash, owner_id=owner_id, company_id=company_id)
        result_path.write_text(json.dumps({'owner_id': owner_id, 'company_id': company_id,
            'source': source_name, 'target': target_name, 'models_verified': len(report)},
            ensure_ascii=False, indent=2), encoding='utf-8')
        self.stdout.write(self.style.SUCCESS(
            f'ГОТОВО: YDS_PORTAL_TEST обновлена; до очистки сверены {len(report)} моделей.'))
