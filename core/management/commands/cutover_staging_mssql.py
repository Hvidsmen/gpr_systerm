"""Build, verify and atomically select the isolated TEST MSSQL database."""
import json
from pathlib import Path

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand,CommandError
from django.db import connections

from core.management.commands.cutover_mssql import atomic_json,recreate_database


class Command(BaseCommand):
    help='Перенести отдельный тестовый портал с SQLite в YDS_PORTAL_TEST.'

    def add_arguments(self,parser):
        parser.add_argument('operation',choices=['prepare','rollback'])
        parser.add_argument('--output',required=True)
        parser.add_argument('--server',default='MSK-DB2')
        parser.add_argument('--database',default='YDS_PORTAL_TEST')
        parser.add_argument('--driver',default='ODBC Driver 17 for SQL Server')

    def handle(self,*args,**options):
        if not getattr(settings,'STAGING_MODE',False):raise CommandError('Команда разрешена только в отдельном тестовом портале.')
        root=Path(settings.CONFIG_DIR).resolve();output=Path(options['output']).resolve();journal=output/'cutover.json'
        config_path=root/'staging.json'
        if options['operation']=='rollback':
            try:data=json.loads(journal.read_text(encoding='utf-8'))
            except (OSError,ValueError) as exc:raise CommandError('Не найден журнал переключения тестового портала.') from exc
            atomic_json(config_path,data['previous_config']);atomic_json(journal,{**data,'rolled_back':True})
            self.stdout.write(self.style.SUCCESS('Тестовый портал возвращён на SQLite.'));return
        try:previous=json.loads(config_path.read_text(encoding='utf-8'))
        except (OSError,ValueError) as exc:raise CommandError('Не удалось прочитать staging.json.') from exc
        if str(previous.get('database_engine','sqlite')).casefold()!='sqlite':raise CommandError('Тестовый портал уже использует не SQLite.')
        if options['database'].casefold()!='yds_portal_test':raise CommandError('Для теста разрешена только отдельная база YDS_PORTAL_TEST.')
        if connections['default'].vendor!='sqlite':raise CommandError('Исходная тестовая база должна быть SQLite.')
        if 'mssql' not in connections:raise CommandError('Не настроено целевое подключение MSSQL для теста.')
        selected=recreate_database(options['server'],options['database'],options['driver'])
        connections.close_all()
        call_command('prepare_mssql_copy',target='mssql',output=str(output),verbosity=1)
        target={**previous,'database_engine':'mssql','sqlite_database':str((root/'data/db.sqlite3').resolve()),
                'mssql':{'host':options['server'],'name':options['database'],'port':'','driver':selected,
                         'trust_server_certificate':True}}
        atomic_json(journal,{'previous_config':previous,'target_config':target,'source_snapshot':str(output/'source.sqlite3')})
        atomic_json(config_path,target)
        self.stdout.write(self.style.SUCCESS('Тестовая копия проверена; выбран MSSQL MSK-DB2 / YDS_PORTAL_TEST.'))
