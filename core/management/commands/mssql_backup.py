"""Create and restore the protected SQL Server backup used by portal updates."""
import json
import ntpath
from pathlib import Path

from django.core.management.base import BaseCommand,CommandError
from django.db import connection

from config.mssql import integrated_connection_string


def quote_identifier(value):
    return '['+str(value).replace(']',']]')+']'


def atomic_json(path,value):
    path=Path(path);temporary=path.with_name(path.name+'.new')
    temporary.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')
    temporary.replace(path)


def connection_details():
    settings=connection.settings_dict;options=settings.get('OPTIONS') or {}
    return {'server':str(settings.get('HOST') or ''),'database':str(settings.get('NAME') or ''),
            'driver':str(options.get('driver') or 'ODBC Driver 17 for SQL Server'),
            'trust_certificate':'TrustServerCertificate=yes' in str(options.get('extra_params') or '')}


class Command(BaseCommand):
    help='Создать или восстановить резервную копию рабочей MSSQL перед обновлением портала.'

    def add_arguments(self,parser):
        parser.add_argument('operation',choices=['create','restore'])
        parser.add_argument('--metadata',required=True)

    def handle(self,*args,**options):
        if connection.vendor not in {'microsoft','mssql'}:raise CommandError('Рабочая база портала не MSSQL.')
        metadata=Path(options['metadata']).resolve()
        if options['operation']=='create':return self.create(metadata)
        return self.restore(metadata)

    def create(self,metadata):
        details=connection_details();database=details['database']
        with connection.cursor() as cursor:
            cursor.execute("SELECT TOP (1) CONVERT(nvarchar(4000),physical_name),SUSER_SNAME() FROM sys.database_files WHERE type=0 ORDER BY file_id")
            data_file,login=cursor.fetchone()
            backup_directory=ntpath.dirname(str(data_file or ''))
            if not backup_directory:raise CommandError('SQL Server не сообщил расположение файла рабочей базы.')
            separator='' if str(backup_directory).endswith(('\\','/')) else '\\'
            backup_path=str(backup_directory)+separator+database+'_yds_portal_update.bak'
            cursor.execute(f'BACKUP DATABASE {quote_identifier(database)} TO DISK = %s WITH COPY_ONLY, INIT, CHECKSUM',[backup_path])
            cursor.execute('RESTORE VERIFYONLY FROM DISK = %s WITH CHECKSUM',[backup_path])
        metadata.parent.mkdir(parents=True,exist_ok=True)
        atomic_json(metadata,{**details,'backup_path':backup_path,'login':str(login)})
        self.stdout.write(self.style.SUCCESS('Резервная копия MSSQL создана и проверена.'))

    def restore(self,metadata):
        try:data=json.loads(metadata.read_text(encoding='utf-8'))
        except (OSError,ValueError) as exc:raise CommandError('Не удалось прочитать описание резервной копии MSSQL.') from exc
        current=connection_details()
        for key in ('server','database'):
            if str(data.get(key,'')).casefold()!=current[key].casefold():raise CommandError('Резервная копия относится к другой базе MSSQL.')
        try:import pyodbc
        except ImportError as exc:raise CommandError('Не установлен pyodbc.') from exc
        database=current['database'];identifier=quote_identifier(database)
        master=pyodbc.connect(integrated_connection_string(current['server'],'master',current['driver'],current['trust_certificate']),timeout=15,autocommit=True)
        try:
            cursor=master.cursor()
            cursor.execute(f'ALTER DATABASE {identifier} SET SINGLE_USER WITH ROLLBACK IMMEDIATE')
            try:cursor.execute(f'RESTORE DATABASE {identifier} FROM DISK = ? WITH REPLACE, CHECKSUM',str(data['backup_path']))
            finally:cursor.execute(f'ALTER DATABASE {identifier} SET MULTI_USER')
        finally:master.close()
        self.stdout.write(self.style.SUCCESS('Рабочая MSSQL восстановлена из резервной копии.'))
