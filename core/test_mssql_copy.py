from io import StringIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock,patch

from django.core.management.base import CommandError
from django.test import SimpleTestCase,override_settings

from apps.planning.models import EstimateReviewTask
from core.management.commands.cutover_mssql import Command as CutoverCommand
from core.management.commands.mssql_backup import Command as BackupCommand
from core.management.commands.prepare_mssql_copy import Command,dump_fixture,require_target_collation
from core.fixture_loading import fixture_loading,is_loading_fixture


class PrepareMssqlCopyTests(SimpleTestCase):
    def test_mssql_backup_uses_server_backup_directory_and_writes_metadata(self):
        with TemporaryDirectory() as directory:
            metadata=Path(directory)/'backup.json';database=MagicMock()
            database.settings_dict={'HOST':'MSK-DB2','NAME':'YDS_PORTAL','OPTIONS':{'driver':'ODBC Driver 17 for SQL Server','extra_params':'TrustServerCertificate=yes'}}
            cursor=database.cursor.return_value.__enter__.return_value
            cursor.fetchone.return_value=(r'C:\SQLBackups','YAMALGAZPROM\\dvivanov')
            with patch('core.management.commands.mssql_backup.connection',database):BackupCommand().create(metadata)
            saved=json.loads(metadata.read_text(encoding='utf-8'))
            self.assertEqual(saved['backup_path'],r'C:\SQLBackups\YDS_PORTAL_yds_portal_update.bak')
            statements=' '.join(call.args[0] for call in cursor.execute.call_args_list)
            self.assertIn('BACKUP DATABASE [YDS_PORTAL]',statements);self.assertIn('RESTORE VERIFYONLY',statements)

    def test_mssql_restore_uses_master_and_returns_database_to_multi_user(self):
        with TemporaryDirectory() as directory:
            metadata=Path(directory)/'backup.json'
            metadata.write_text(json.dumps({'server':'MSK-DB2','database':'YDS_PORTAL','backup_path':r'C:\SQLBackups\portal.bak'}),encoding='utf-8')
            database=MagicMock();database.settings_dict={'HOST':'MSK-DB2','NAME':'YDS_PORTAL','OPTIONS':{'driver':'ODBC Driver 17 for SQL Server','extra_params':'TrustServerCertificate=yes'}}
            master=MagicMock();pyodbc=MagicMock();pyodbc.connect.return_value=master
            with patch('core.management.commands.mssql_backup.connection',database),patch.dict('sys.modules',{'pyodbc':pyodbc}):BackupCommand().restore(metadata)
            statements=' '.join(call.args[0] for call in master.cursor.return_value.execute.call_args_list)
            self.assertIn('SET SINGLE_USER',statements);self.assertIn('RESTORE DATABASE',statements);self.assertIn('SET MULTI_USER',statements)

    def test_cutover_activates_verified_target_and_can_restore_sqlite_config(self):
        with TemporaryDirectory() as directory:
            root=Path(directory);runtime=root/'.runtime';runtime.mkdir();source=root/'db.sqlite3';source.touch()
            installed=runtime/'installed.json';installed.write_text('{"database":"'+str(source).replace('\\','\\\\')+'","media":"media","port":8001,"hosts":[]}',encoding='utf-8')
            output=runtime/'mssql-cutover'/'run'
            def verified_copy(*args,**kwargs):
                output.mkdir(parents=True);(output/'portal-data.json').write_bytes(b'fixture')
                (output/'verification.json').write_text('{"errors":[]}',encoding='utf-8')
            def unchanged_fixture(database,path):Path(path).write_bytes(b'fixture')
            command=CutoverCommand()
            with override_settings(CONFIG_DIR=root),patch('core.management.commands.cutover_mssql.recreate_database',return_value='ODBC Driver 17 for SQL Server'),patch('core.management.commands.cutover_mssql.call_command',side_effect=verified_copy),patch('core.management.commands.cutover_mssql.dump_fixture',side_effect=unchanged_fixture):
                command.handle(operation='prepare',output=str(output),server='MSK-DB2',database='YDS_PORTAL',driver='ODBC Driver 17 for SQL Server')
                active=json.loads(installed.read_text(encoding='utf-8'))
                self.assertEqual(active['database_engine'],'mssql');self.assertEqual(active['sqlite_database'],str(source))
                command.handle(operation='rollback',output=str(output),server='MSK-DB2',database='YDS_PORTAL',driver='ODBC Driver 17 for SQL Server')
                restored=json.loads(installed.read_text(encoding='utf-8'))
                self.assertNotIn('database_engine',restored);self.assertEqual(restored['database'],str(source))
                command.handle(operation='resume',output=str(output),server='MSK-DB2',database='YDS_PORTAL',driver='ODBC Driver 17 for SQL Server')
            resumed=json.loads(installed.read_text(encoding='utf-8'));self.assertEqual(resumed['database_engine'],'mssql')

    def test_cutover_refuses_to_resume_when_live_sqlite_changed(self):
        with TemporaryDirectory() as directory:
            root=Path(directory);runtime=root/'.runtime';runtime.mkdir();source=root/'db.sqlite3';source.touch()
            previous={'database':str(source),'media':'media','port':8001,'hosts':[]}
            target={**previous,'database_engine':'mssql','sqlite_database':str(source),'mssql':{'host':'MSK-DB2','name':'YDS_PORTAL'}}
            installed=runtime/'installed.json';installed.write_text(json.dumps(previous),encoding='utf-8')
            output=runtime/'mssql-cutover'/'run';output.mkdir(parents=True)
            (output/'portal-data.json').write_bytes(b'old fixture')
            (output/'verification.json').write_text('{"errors":[]}',encoding='utf-8')
            (output/'cutover.json').write_text(json.dumps({'previous_config':previous,'target_config':target,'rolled_back':True}),encoding='utf-8')
            with override_settings(CONFIG_DIR=root),patch('core.management.commands.cutover_mssql.dump_fixture',side_effect=lambda database,path:Path(path).write_bytes(b'changed fixture')):
                with self.assertRaisesMessage(CommandError,'SQLite изменилась'):
                    CutoverCommand().handle(operation='resume',output=str(output),server='MSK-DB2',database='YDS_PORTAL',driver='ODBC Driver 17 for SQL Server')
            self.assertEqual(json.loads(installed.read_text(encoding='utf-8')),previous)

    def test_prefixed_estimate_fingerprint_fits_mssql_schema(self):
        value='works-v1:'+'a'*64
        self.assertLessEqual(len(value),EstimateReviewTask._meta.get_field('fingerprint').max_length)

    def test_target_requires_binary_collation_before_any_copy(self):
        connection=MagicMock()
        cursor=connection.cursor.return_value.__enter__.return_value
        cursor.fetchone.return_value=('Cyrillic_General_CI_AS',)
        with self.assertRaisesMessage(CommandError,'Latin1_General_100_BIN2'):
            require_target_collation(connection)
        cursor.fetchone.return_value=('Latin1_General_100_BIN2',)
        self.assertEqual(require_target_collation(connection),'Latin1_General_100_BIN2')

    def test_fixture_loading_context_is_scoped(self):
        self.assertFalse(is_loading_fixture())
        with fixture_loading():self.assertTrue(is_loading_fixture())
        self.assertFalse(is_loading_fixture())

    def test_complete_traceback_is_duplicated_to_stdout(self):
        output=StringIO();command=Command(stdout=output)
        with patch.object(command,'_handle',side_effect=ValueError('SQL failure')):
            with self.assertRaises(CommandError):
                command.handle(target='mssql',output='unused')
        log=output.getvalue()
        self.assertIn('ПОЛНАЯ ОШИБКА ПОДГОТОВКИ MSSQL',log)
        self.assertIn('ValueError: SQL failure',log)
        self.assertIn('Рабочая SQLite-база не изменялась',log)

    def test_fixture_is_written_as_utf8_on_non_utf8_windows_locale(self):
        with TemporaryDirectory() as directory:
            fixture=Path(directory)/'portal-data.json'
            def serialize(command,**options):
                self.assertEqual(command,'dumpdata')
                self.assertNotIn('output',options)
                options['stdout'].write('{"employee":"Øстров"}')
            with patch('core.management.commands.prepare_mssql_copy.call_command',side_effect=serialize):
                dump_fixture('source_snapshot',fixture)
            self.assertEqual(fixture.read_bytes(),'{"employee":"Øстров"}'.encode('utf-8'))
