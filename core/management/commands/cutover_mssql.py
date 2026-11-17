"""Build the final fresh MSSQL copy and atomically select it for the Windows runtime."""
import hashlib
import json
from pathlib import Path

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand,CommandError
from django.db import connections

from config.mssql import PORTAL_COLLATION,available_sql_server_driver,integrated_connection_string
from core.management.commands.prepare_mssql_copy import dump_fixture


EXPECTED_LOGIN=r'YAMALGAZPROM\dvivanov'


def atomic_json(path,value):
    path=Path(path);temporary=path.with_name(path.name+'.new')
    temporary.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')
    temporary.replace(path)


def quote_identifier(value):
    return '['+str(value).replace(']',']]')+']'


def file_digest(path):
    digest=hashlib.sha256()
    with Path(path).open('rb') as source:
        for chunk in iter(lambda:source.read(1024*1024),b''):digest.update(chunk)
    return digest.hexdigest()


def recreate_database(server,database,driver,expected_login=EXPECTED_LOGIN):
    if database.casefold() in {'master','model','msdb','tempdb'}:raise CommandError('Системную базу пересоздавать запрещено.')
    try:import pyodbc
    except ImportError as exc:raise CommandError('Не установлен pyodbc.') from exc
    selected=available_sql_server_driver(pyodbc.drivers(),driver)
    if not selected:raise CommandError(f'ODBC-драйвер {driver} не установлен.')
    connection=pyodbc.connect(integrated_connection_string(server,'master',selected),timeout=15,autocommit=True)
    try:
        cursor=connection.cursor();login=str(cursor.execute('SELECT SUSER_SNAME()').fetchone()[0])
        if login.casefold()!=expected_login.casefold():raise CommandError(f'Подключение выполнено как {login}, ожидался {expected_login}.')
        identifier=quote_identifier(database)
        if cursor.execute('SELECT DB_ID(?)',database).fetchone()[0] is not None:
            cursor.execute(f'ALTER DATABASE {identifier} SET SINGLE_USER WITH ROLLBACK IMMEDIATE')
            cursor.execute(f'DROP DATABASE {identifier}')
        cursor.execute(f'CREATE DATABASE {identifier} COLLATE {PORTAL_COLLATION}')
    finally:connection.close()
    return selected


class Command(BaseCommand):
    help='Во время остановки портала создать свежую MSSQL-копию и выбрать её рабочей.'

    def add_arguments(self,parser):
        parser.add_argument('operation',choices=['prepare','resume','rollback'])
        parser.add_argument('--output',required=True)
        parser.add_argument('--server',default='MSK-DB2')
        parser.add_argument('--database',default='YDS_PORTAL')
        parser.add_argument('--driver',default='ODBC Driver 17 for SQL Server')

    def handle(self,*args,**options):
        output=Path(options['output']).resolve();journal=output/'cutover.json'
        config_path=Path(settings.CONFIG_DIR)/'.runtime/installed.json'
        if options['operation']=='rollback':
            try:data=json.loads(journal.read_text(encoding='utf-8'))
            except (OSError,ValueError) as exc:raise CommandError('Не найден журнал переключения; автоматический откат невозможен.') from exc
            atomic_json(config_path,data['previous_config'])
            atomic_json(journal,{**data,'rolled_back':True})
            self.stdout.write(self.style.SUCCESS('Настройка портала возвращена на SQLite.'))
            return
        if options['operation']=='resume':
            try:data=json.loads(journal.read_text(encoding='utf-8'))
            except (OSError,ValueError) as exc:raise CommandError('Не найден журнал проверенной MSSQL-копии.') from exc
            fixture=output/'portal-data.json';verification=output/'verification.json';current=output/'resume-current.json'
            try:report=json.loads(verification.read_text(encoding='utf-8'))
            except (OSError,ValueError) as exc:raise CommandError('Не найден успешный отчёт сверки MSSQL.') from exc
            if report.get('errors'):raise CommandError('Сохранённая MSSQL-копия содержит расхождения и не может быть активирована.')
            try:
                dump_fixture('default',current)
                if not fixture.is_file() or file_digest(current)!=file_digest(fixture):
                    raise CommandError('Рабочая SQLite изменилась после проверенной копии; требуется свежий перенос.')
            finally:current.unlink(missing_ok=True)
            atomic_json(config_path,data['target_config'])
            atomic_json(journal,{**data,'rolled_back':False,'resumed':True})
            self.stdout.write(self.style.SUCCESS('Текущие данные не изменились; выбрана уже проверенная MSSQL-копия.'))
            return
        try:previous=json.loads(config_path.read_text(encoding='utf-8'))
        except (OSError,ValueError) as exc:raise CommandError('Не удалось прочитать установленную конфигурацию портала.') from exc
        if str(previous.get('database_engine','sqlite')).casefold()!='sqlite':raise CommandError('Портал уже настроен не на SQLite; переключение остановлено.')
        source=Path(previous.get('database',''))
        if not source.is_file():raise CommandError('Рабочая SQLite-база не найдена.')
        driver=recreate_database(options['server'],options['database'],options['driver'])
        connections.close_all()
        call_command('prepare_mssql_copy',target='mssql',output=str(output),verbosity=1)
        target={**previous,'database_engine':'mssql','sqlite_database':str(source.resolve()),
                'mssql':{'host':options['server'],'name':options['database'],'port':'','driver':driver,
                         'trust_server_certificate':True}}
        atomic_json(journal,{'previous_config':previous,'target_config':target,'source_snapshot':str(output/'source.sqlite3')})
        atomic_json(config_path,target)
        self.stdout.write(self.style.SUCCESS('Свежая копия проверена; рабочая конфигурация выбрана MSSQL.'))
