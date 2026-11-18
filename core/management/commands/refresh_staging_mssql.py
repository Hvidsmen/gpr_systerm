"""Replace TEST MSSQL data from production MSSQL, then sanitize it."""
import json
import ntpath
from pathlib import Path

from django.conf import settings
from django.contrib.auth.hashers import identify_hasher, is_password_usable
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db import connections

from apps.accounts.management.commands.prepare_staging_copy import sanitize_database
from apps.accounts.models import User
from config.mssql import integrated_connection_string
from core.management.commands.mssql_backup import quote_identifier
from core.management.commands.prepare_mssql_copy import compare_databases


def quote_literal(value):
    return "N'" + str(value).replace("'", "''") + "'"


def connection_details(database_connection):
    database_settings = database_connection.settings_dict
    options = database_settings.get('OPTIONS') or {}
    return {
        'server': str(database_settings.get('HOST') or ''),
        'database': str(database_settings.get('NAME') or ''),
        'driver': str(options.get('driver') or 'ODBC Driver 17 for SQL Server'),
        'trust_certificate': 'TrustServerCertificate=yes' in str(options.get('extra_params') or ''),
    }


def drain(cursor):
    while cursor.nextset():
        pass


def restore_moves(backup_files, target_files):
    """Map every data/log file in the production backup to TEST file paths."""
    source_by_type = {'D': [], 'L': []}
    target_by_type = {'D': [], 'L': []}
    for file_type, logical_name in backup_files:
        kind = str(file_type).upper()
        if kind not in source_by_type:
            raise CommandError('Резервная копия YDS_PORTAL содержит неподдерживаемый тип файла.')
        source_by_type[kind].append(str(logical_name))
    for file_type, physical_name in target_files:
        kind = 'D' if int(file_type) == 0 else 'L' if int(file_type) == 1 else ''
        if not kind:
            raise CommandError('YDS_PORTAL_TEST содержит неподдерживаемый тип файла.')
        target_by_type[kind].append(str(physical_name))
    if (not source_by_type['D'] or not source_by_type['L'] or
            any(len(source_by_type[kind]) != len(target_by_type[kind]) for kind in ('D', 'L'))):
        raise CommandError(
            'Состав файлов YDS_PORTAL и YDS_PORTAL_TEST различается; безопасное восстановление остановлено.')
    return [(logical, physical) for kind in ('D', 'L')
            for logical, physical in zip(source_by_type[kind], target_by_type[kind])]


def native_refresh_database(source_connection, target_connection):
    """Clone production over TEST using SQL Server itself, without Python fixtures."""
    source = connection_details(source_connection)
    target = connection_details(target_connection)
    if source['server'].casefold() != target['server'].casefold():
        raise CommandError('YDS_PORTAL и YDS_PORTAL_TEST должны находиться на одном SQL Server.')
    try:
        import pyodbc
    except ImportError as exc:
        raise CommandError('Не установлен pyodbc.') from exc

    master = pyodbc.connect(integrated_connection_string(
        target['server'], 'master', target['driver'], target['trust_certificate']),
        timeout=15, autocommit=True)
    restored = False
    cursor = None
    target_identifier = quote_identifier(target['database'])
    try:
        cursor = master.cursor()
        cursor.execute(
            "SELECT type,CONVERT(nvarchar(4000),physical_name) FROM sys.master_files "
            "WHERE database_id=DB_ID(?) AND type IN (0,1) ORDER BY type,file_id",
            target['database'])
        target_files = list(cursor.fetchall())
        cursor.execute(
            "SELECT TOP (1) CONVERT(nvarchar(4000),physical_name) FROM sys.master_files "
            "WHERE database_id=DB_ID(?) AND type=0 ORDER BY file_id",
            source['database'])
        row = cursor.fetchone()
        backup_directory = ntpath.dirname(str(row[0] if row else ''))
        if not backup_directory:
            raise CommandError('SQL Server не сообщил расположение файла YDS_PORTAL.')
        backup_path = ntpath.join(backup_directory, source['database'] + '_yds_test_refresh.bak')

        cursor.execute(
            f"BACKUP DATABASE {quote_identifier(source['database'])} TO DISK = ? "
            "WITH COPY_ONLY, INIT, CHECKSUM", backup_path)
        drain(cursor)
        cursor.execute('RESTORE VERIFYONLY FROM DISK = ? WITH CHECKSUM', backup_path)
        drain(cursor)
        cursor.execute('RESTORE FILELISTONLY FROM DISK = ?', backup_path)
        columns = {str(column[0]).casefold(): index for index, column in enumerate(cursor.description)}
        if 'logicalname' not in columns or 'type' not in columns:
            raise CommandError('SQL Server не вернул состав файлов резервной копии YDS_PORTAL.')
        backup_files = [(row[columns['type']], row[columns['logicalname']]) for row in cursor.fetchall()]
        moves = restore_moves(backup_files, target_files)
        move_sql = ','.join(
            f"MOVE {quote_literal(logical)} TO {quote_literal(physical)}"
            for logical, physical in moves)

        # Release Django's pooled source/target handles before SINGLE_USER.
        connections.close_all()
        cursor.execute(f'ALTER DATABASE {target_identifier} SET SINGLE_USER WITH ROLLBACK IMMEDIATE')
        drain(cursor)
        try:
            cursor.execute(
                f'RESTORE DATABASE {target_identifier} FROM DISK = ? '
                f'WITH REPLACE, CHECKSUM, RECOVERY,{move_sql}', backup_path)
            drain(cursor)
            restored = True
        finally:
            if not restored:
                try:
                    cursor.execute(f'ALTER DATABASE {target_identifier} SET MULTI_USER')
                    drain(cursor)
                except BaseException:
                    pass
        cursor.execute(f'ALTER DATABASE {target_identifier} SET MULTI_USER')
        drain(cursor)
        return backup_path
    finally:
        master.close()


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
        report_path = output / 'verification.json'
        result_path = output / 'result.json'

        self.stdout.write('SQL Server создаёт проверенную резервную копию YDS_PORTAL…')
        backup_path = native_refresh_database(connections[source], connections[target])
        self.stdout.write('YDS_PORTAL_TEST восстановлена средствами SQL Server; сверяем данные…')
        report, errors = compare_databases(source, target)
        report_path.write_text(json.dumps({'source': source_name, 'target': target_name,
            'backup_path': backup_path, 'models': report, 'errors': errors},
            ensure_ascii=False, indent=2), encoding='utf-8')
        if errors:
            raise CommandError('Сверка рабочей и тестовой копий не пройдена: ' + '; '.join(errors[:10]))
        # The staging branch can contain a schema migration not deployed to
        # production yet. Apply it only after the restored data was verified.
        call_command('migrate', database=target, interactive=False, verbosity=1)
        sanitize_database(password_hash, owner_id=owner_id, company_id=company_id)
        result_path.write_text(json.dumps({'owner_id': owner_id, 'company_id': company_id,
            'source': source_name, 'target': target_name, 'models_verified': len(report)},
            ensure_ascii=False, indent=2), encoding='utf-8')
        self.stdout.write(self.style.SUCCESS(
            f'ГОТОВО: YDS_PORTAL_TEST обновлена; до очистки сверены {len(report)} моделей.'))
