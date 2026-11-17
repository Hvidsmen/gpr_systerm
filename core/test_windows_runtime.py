import io
from http.client import IncompleteRead
import importlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile
from unittest.mock import patch, Mock, MagicMock
import zipfile
from django.test import SimpleTestCase, override_settings
from ops import windows_runtime as runtime


class WindowsRuntimeTests(SimpleTestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.state=self.root/'.runtime';self.state.mkdir()
        self.database=self.root/'existing.sqlite3'
        with sqlite3.connect(self.database) as db:db.execute('CREATE TABLE marker (value text)');db.execute("INSERT INTO marker VALUES ('before')")
        self.config={'database':str(self.database),'media':str(self.root/'media'),'port':8001,'hosts':['192.168.70.9']}
        runtime.atomic_json(self.state/'installed.json',self.config)
        (self.state/'secret.key').write_text('server-secret-'*8)

    def test_environment_uses_persistent_database_media_and_directory_credentials(self):
        (self.root/'.env').write_text('LDAP_ENABLED=true\nLDAP_BIND_PASSWORD="a;quoted=password"\nDJANGO_DB_PATH=wrong.sqlite3\nDJANGO_ALLOWED_HOSTS=example.local\n')
        env=runtime.environment(self.root,self.config)
        self.assertEqual(env['DJANGO_DB_PATH'],str(self.database))
        self.assertEqual(env['LDAP_BIND_PASSWORD'],'a;quoted=password')
        self.assertEqual(env['GPR_CONFIG_DIR'],str(self.root));self.assertEqual(env['DJANGO_DEBUG'],'0')
        self.assertIn('192.168.70.9',env['DJANGO_ALLOWED_HOSTS']);self.assertIn('127.0.0.1',env['DJANGO_ALLOWED_HOSTS'])
        self.assertEqual(env['DJANGO_COOKIE_SECURE'],'0')

    def test_environment_uses_persistent_mssql_configuration_over_env_file(self):
        (self.root/'.env').write_text('DJANGO_DB_ENGINE=sqlite\nDJANGO_DB_PATH=wrong.sqlite3\n')
        config={**self.config,'database_engine':'mssql','mssql':{'host':'MSK-DB2','name':'YDS_PORTAL','port':'','driver':'ODBC Driver 17 for SQL Server','trust_server_certificate':True}}
        env=runtime.environment(self.root,config)
        self.assertEqual(env['DJANGO_DB_ENGINE'],'mssql');self.assertEqual(env['MSSQL_HOST'],'MSK-DB2')
        self.assertEqual(env['MSSQL_NAME'],'YDS_PORTAL');self.assertEqual(env['MSSQL_DRIVER'],'ODBC Driver 17 for SQL Server')

    def test_mssql_update_backup_and_restore_use_active_release_command(self):
        config={**self.config,'database_engine':'mssql','mssql':{'host':'MSK-DB2','name':'YDS_PORTAL','driver':'ODBC Driver 17 for SQL Server'}}
        current={'python':'release-python','code':str(self.root/'release')}
        def create_metadata(args,cwd,env):Path(args[-1]).write_text('{}')
        with patch.object(runtime,'run',side_effect=create_metadata) as run:
            backup=runtime.backup_database(config,self.state,current)
            runtime.restore_database(config,backup,current,self.root)
        self.assertEqual(run.call_count,2);self.assertEqual(run.call_args_list[0].args[0][2:5],['mssql_backup','create','--metadata'])
        self.assertEqual(run.call_args_list[1].args[0][2:5],['mssql_backup','restore','--metadata'])

    def test_backup_is_consistent_and_restores_database_without_touching_media(self):
        media=self.root/'media';media.mkdir();(media/'file.txt').write_text('keep')
        backup=runtime.backup_database(self.config,self.state)
        with sqlite3.connect(self.database) as db:db.execute("UPDATE marker SET value='after'")
        runtime.restore_database(self.config,backup)
        with sqlite3.connect(self.database) as db:self.assertEqual(db.execute('SELECT value FROM marker').fetchone()[0],'before')
        self.assertEqual((media/'file.txt').read_text(),'keep')

    def test_bad_archive_cannot_escape_release_directory(self):
        archive=self.root/'release.zip'
        for name in ['repo/../../outside.txt','repo/.env','repo/.runtime/installed.json']:
            with zipfile.ZipFile(archive,'w') as zipped:zipped.writestr(name,'unsafe')
            with self.assertRaises(RuntimeError):runtime.extract_archive(archive,self.root/'release')
        self.assertFalse((self.root/'outside.txt').exists())

    def test_archive_extracts_only_with_required_application_files(self):
        archive=self.root/'release.zip'
        with zipfile.ZipFile(archive,'w') as zipped:
            for name in ['manage.py','requirements.txt','ops/windows_runtime.py']:zipped.writestr('repo/'+name,'content')
        runtime.extract_archive(archive,self.root/'release')
        self.assertTrue((self.root/'release/ops/windows_runtime.py').is_file())

    def test_interrupted_update_download_retries_and_replaces_partial_file(self):
        archive=self.state/'update.zip';partial=archive.with_name('update.zip.part')
        archive.write_bytes(b'stale');partial.write_bytes(b'partial')
        interrupted=MagicMock()
        interrupted.__enter__.return_value=interrupted;interrupted.__exit__.return_value=False
        interrupted.read.side_effect=IncompleteRead(b'broken',5)
        complete=io.BytesIO(b'complete archive')
        with patch.object(runtime,'urlopen',side_effect=[interrupted,complete]) as opening,patch.object(runtime.time,'sleep') as sleep:
            runtime.download_update_archive('https://example.invalid/archive',{},archive)
        self.assertEqual(archive.read_bytes(),b'complete archive')
        self.assertFalse(partial.exists());self.assertEqual(opening.call_count,2);sleep.assert_called_once_with(2)

    def test_download_failure_does_not_stop_the_current_server(self):
        process=Mock();current={'sha':'old'}
        with patch.object(runtime,'stage_update',side_effect=OSError('offline')),patch.object(runtime,'stop_process') as stop:
            with self.assertRaises(OSError):runtime.update(self.root,process,current)
            stop.assert_not_called()

    def test_migration_failure_restores_old_code_and_database(self):
        current={'sha':'old','code':str(self.root),'python':'python'}
        candidate={'sha':'a'*40,'code':str(self.root/'release'),'python':'new-python'}
        def fail(*args):
            with sqlite3.connect(self.database) as db:db.execute("UPDATE marker SET value='new schema'")
            raise RuntimeError('migration failed')
        with patch.object(runtime,'stage_update',return_value=candidate),patch.object(runtime,'stop_process'),patch.object(runtime,'run',side_effect=fail),patch.object(runtime,'launch',return_value=(Mock(),8001)) as launch:
            _,result,message=runtime.update(self.root,Mock(),current)
            self.assertEqual(result,current);self.assertTrue(message.startswith('Update failed'))
            launch.assert_called_once_with(self.root,current)
        with sqlite3.connect(self.database) as db:self.assertEqual(db.execute('SELECT value FROM marker').fetchone()[0],'before')

    def test_mssql_migration_failure_invokes_native_restore_before_old_start(self):
        config={**self.config,'database_engine':'mssql','mssql':{'host':'MSK-DB2','name':'YDS_PORTAL','driver':'ODBC Driver 17 for SQL Server'}}
        runtime.atomic_json(self.state/'installed.json',config)
        current={'sha':'old','code':str(self.root),'python':'python'}
        candidate={'sha':'e'*40,'code':str(self.root/'release'),'python':'new-python'}
        backup=self.state/'backups/run/mssql-backup.json';backup.parent.mkdir(parents=True);backup.write_text('{}')
        with patch.object(runtime,'stage_update',return_value=candidate),patch.object(runtime,'stop_process'),patch.object(runtime,'backup_database',return_value=backup),patch.object(runtime,'run',side_effect=RuntimeError('migration failed')),patch.object(runtime,'restore_database') as restore,patch.object(runtime,'launch',return_value=(Mock(),8001)):
            _,result,message=runtime.update(self.root,Mock(),current)
        self.assertEqual(result,current);self.assertTrue(message.startswith('Update failed'))
        restore.assert_called_once_with(config,backup,current,self.root)

    def test_success_commits_release_before_public_start(self):
        current={'sha':'old','code':str(self.root),'python':'python'}
        code=self.root/'release';(code/'ops').mkdir(parents=True);(code/'ops/windows_runtime.py').write_text('new bootstrap')
        (code/'SET_GPR_PORTAL_ACCOUNT.cmd').write_text('new launcher')
        (code/'ops/set-windows-portal-account.ps1').write_text('new account tool')
        (code/'CHECK_GPR_MSSQL.cmd').write_text('mssql launcher')
        (code/'ops/check-mssql.ps1').write_text('mssql check')
        (code/'PREPARE_GPR_MSSQL_COPY.cmd').write_text('copy launcher')
        (code/'ops/prepare-mssql-copy.ps1').write_text('copy tool')
        (code/'SWITCH_GPR_TO_MSSQL.cmd').write_text('cutover launcher')
        (code/'ops/switch-to-mssql.ps1').write_text('cutover tool')
        candidate={'sha':'b'*40,'code':str(code),'python':'python-new'}
        with patch.object(runtime,'stage_update',return_value=candidate),patch.object(runtime,'stop_process'),patch.object(runtime,'run'),patch.object(runtime,'healthy'),patch.object(runtime,'launch',return_value=(Mock(),8001)):
            _,result,message=runtime.update(self.root,Mock(),current)
        self.assertEqual(result,candidate);self.assertEqual(runtime.read_json(self.state/'current.json'),candidate)
        self.assertEqual((self.state/'bootstrap.py').read_text(),'new bootstrap')
        self.assertEqual((self.root/'SET_GPR_PORTAL_ACCOUNT.cmd').read_text(),'new launcher')
        self.assertEqual((self.root/'ops/set-windows-portal-account.ps1').read_text(),'new account tool')
        self.assertEqual((self.root/'CHECK_GPR_MSSQL.cmd').read_text(),'mssql launcher')
        self.assertEqual((self.root/'ops/check-mssql.ps1').read_text(),'mssql check')
        self.assertEqual((self.root/'PREPARE_GPR_MSSQL_COPY.cmd').read_text(),'copy launcher')
        self.assertEqual((self.root/'ops/prepare-mssql-copy.ps1').read_text(),'copy tool')
        self.assertEqual((self.root/'SWITCH_GPR_TO_MSSQL.cmd').read_text(),'cutover launcher')
        self.assertEqual((self.root/'ops/switch-to-mssql.ps1').read_text(),'cutover tool')

    def test_mssql_bridge_migration_exposes_tool_on_its_first_update(self):
        migration=importlib.import_module('apps.accounts.migrations.0032_publish_mssql_check')
        code=self.root/'immutable-release';(code/'ops').mkdir(parents=True)
        (code/'CHECK_GPR_MSSQL.cmd').write_text('bridge launcher')
        (code/'ops/check-mssql.ps1').write_text('bridge check')
        persistent=self.root/'persistent';persistent.mkdir()
        with override_settings(BASE_DIR=code,CONFIG_DIR=persistent):
            migration.publish_tool(None,None,platform_name='nt')
        self.assertEqual((persistent/'CHECK_GPR_MSSQL.cmd').read_text(),'bridge launcher')
        self.assertEqual((persistent/'ops/check-mssql.ps1').read_text(),'bridge check')

    def test_mssql_copy_bridge_exposes_tool_on_its_first_update(self):
        migration=importlib.import_module('apps.accounts.migrations.0033_publish_mssql_copy_tool')
        code=self.root/'immutable-release';(code/'ops').mkdir(parents=True)
        (code/'PREPARE_GPR_MSSQL_COPY.cmd').write_text('bridge launcher')
        (code/'ops/prepare-mssql-copy.ps1').write_text('bridge copy')
        persistent=self.root/'persistent';persistent.mkdir()
        with override_settings(BASE_DIR=code,CONFIG_DIR=persistent):
            migration.publish_tool(None,None,platform_name='nt')
        self.assertEqual((persistent/'PREPARE_GPR_MSSQL_COPY.cmd').read_text(),'bridge launcher')
        self.assertEqual((persistent/'ops/prepare-mssql-copy.ps1').read_text(),'bridge copy')

    def test_mssql_copy_logging_fix_republishes_tool(self):
        migration=importlib.import_module('apps.accounts.migrations.0034_publish_mssql_copy_logging_fix')
        code=self.root/'immutable-release';(code/'ops').mkdir(parents=True)
        (code/'PREPARE_GPR_MSSQL_COPY.cmd').write_text('fixed launcher')
        (code/'ops/prepare-mssql-copy.ps1').write_text('fixed logging')
        persistent=self.root/'persistent';persistent.mkdir()
        with override_settings(BASE_DIR=code,CONFIG_DIR=persistent):
            migration.publish_tool(None,None,platform_name='nt')
        self.assertEqual((persistent/'PREPARE_GPR_MSSQL_COPY.cmd').read_text(),'fixed launcher')
        self.assertEqual((persistent/'ops/prepare-mssql-copy.ps1').read_text(),'fixed logging')

    def test_mssql_copy_file_sharing_fix_republishes_tool(self):
        migration=importlib.import_module('apps.accounts.migrations.0035_republish_mssql_copy_file_sharing_fix')
        code=self.root/'immutable-release';(code/'ops').mkdir(parents=True)
        (code/'PREPARE_GPR_MSSQL_COPY.cmd').write_text('fixed launcher')
        (code/'ops/prepare-mssql-copy.ps1').write_text('safe reader')
        persistent=self.root/'persistent';persistent.mkdir()
        with override_settings(BASE_DIR=code,CONFIG_DIR=persistent):
            migration.publish_tool(None,None,platform_name='nt')
        self.assertEqual((persistent/'PREPARE_GPR_MSSQL_COPY.cmd').read_text(),'fixed launcher')
        self.assertEqual((persistent/'ops/prepare-mssql-copy.ps1').read_text(),'safe reader')

    def test_mssql_cutover_bridge_exposes_tool_on_its_first_update(self):
        migration=importlib.import_module('apps.accounts.migrations.0036_publish_mssql_cutover_tool')
        code=self.root/'immutable-release';(code/'ops').mkdir(parents=True)
        (code/'SWITCH_GPR_TO_MSSQL.cmd').write_text('cutover launcher')
        (code/'ops/switch-to-mssql.ps1').write_text('cutover tool')
        persistent=self.root/'persistent';persistent.mkdir()
        with override_settings(BASE_DIR=code,CONFIG_DIR=persistent):
            migration.publish_tool(None,None,platform_name='nt')
        self.assertEqual((persistent/'SWITCH_GPR_TO_MSSQL.cmd').read_text(),'cutover launcher')
        self.assertEqual((persistent/'ops/switch-to-mssql.ps1').read_text(),'cutover tool')

    def test_mssql_cutover_sid_fix_republishes_tool(self):
        migration=importlib.import_module('apps.accounts.migrations.0037_republish_mssql_cutover_sid_fix')
        code=self.root/'immutable-release';(code/'ops').mkdir(parents=True)
        (code/'SWITCH_GPR_TO_MSSQL.cmd').write_text('same launcher')
        (code/'ops/switch-to-mssql.ps1').write_text('SID comparison')
        persistent=self.root/'persistent';(persistent/'ops').mkdir(parents=True)
        (persistent/'SWITCH_GPR_TO_MSSQL.cmd').write_text('old launcher')
        (persistent/'ops/switch-to-mssql.ps1').write_text('old text comparison')
        with override_settings(BASE_DIR=code,CONFIG_DIR=persistent):
            migration.publish_tool(None,None,platform_name='nt')
        self.assertEqual((persistent/'SWITCH_GPR_TO_MSSQL.cmd').read_text(),'same launcher')
        self.assertEqual((persistent/'ops/switch-to-mssql.ps1').read_text(),'SID comparison')

    def test_mssql_cutover_argument_fix_republishes_tool(self):
        migration=importlib.import_module('apps.accounts.migrations.0038_republish_mssql_cutover_argument_fix')
        code=self.root/'immutable-release';(code/'ops').mkdir(parents=True)
        (code/'SWITCH_GPR_TO_MSSQL.cmd').write_text('launcher')
        (code/'ops/switch-to-mssql.ps1').write_text('driver default')
        persistent=self.root/'persistent';(persistent/'ops').mkdir(parents=True)
        (persistent/'SWITCH_GPR_TO_MSSQL.cmd').write_text('old launcher')
        (persistent/'ops/switch-to-mssql.ps1').write_text('spaced driver argument')
        with override_settings(BASE_DIR=code,CONFIG_DIR=persistent):
            migration.publish_tool(None,None,platform_name='nt')
        self.assertEqual((persistent/'SWITCH_GPR_TO_MSSQL.cmd').read_text(),'launcher')
        self.assertEqual((persistent/'ops/switch-to-mssql.ps1').read_text(),'driver default')

    def test_mssql_cutover_start_wait_fix_republishes_tool(self):
        migration=importlib.import_module('apps.accounts.migrations.0039_republish_mssql_cutover_start_wait_fix')
        code=self.root/'immutable-release';(code/'ops').mkdir(parents=True)
        (code/'SWITCH_GPR_TO_MSSQL.cmd').write_text('launcher')
        (code/'ops/switch-to-mssql.ps1').write_text('startup grace period')
        persistent=self.root/'persistent';(persistent/'ops').mkdir(parents=True)
        (persistent/'SWITCH_GPR_TO_MSSQL.cmd').write_text('old launcher')
        (persistent/'ops/switch-to-mssql.ps1').write_text('immediate ready failure')
        with override_settings(BASE_DIR=code,CONFIG_DIR=persistent):
            migration.publish_tool(None,None,platform_name='nt')
        self.assertEqual((persistent/'SWITCH_GPR_TO_MSSQL.cmd').read_text(),'launcher')
        self.assertEqual((persistent/'ops/switch-to-mssql.ps1').read_text(),'startup grace period')

    def test_mssql_cutover_task_retry_republishes_tool(self):
        migration=importlib.import_module('apps.accounts.migrations.0040_republish_mssql_cutover_task_retry')
        code=self.root/'immutable-release';(code/'ops').mkdir(parents=True)
        (code/'SWITCH_GPR_TO_MSSQL.cmd').write_text('launcher')
        (code/'ops/switch-to-mssql.ps1').write_text('retry ignored task starts')
        persistent=self.root/'persistent';(persistent/'ops').mkdir(parents=True)
        (persistent/'SWITCH_GPR_TO_MSSQL.cmd').write_text('old launcher')
        (persistent/'ops/switch-to-mssql.ps1').write_text('single task start')
        with override_settings(BASE_DIR=code,CONFIG_DIR=persistent):
            migration.publish_tool(None,None,platform_name='nt')
        self.assertEqual((persistent/'SWITCH_GPR_TO_MSSQL.cmd').read_text(),'launcher')
        self.assertEqual((persistent/'ops/switch-to-mssql.ps1').read_text(),'retry ignored task starts')

    def test_bridge_migration_exposes_tool_during_first_immutable_update(self):
        migration=importlib.import_module('apps.accounts.migrations.0031_publish_portal_account_tool')
        code=self.root/'immutable-release';(code/'ops').mkdir(parents=True)
        (code/'SET_GPR_PORTAL_ACCOUNT.cmd').write_text('bridge launcher')
        (code/'ops/set-windows-portal-account.ps1').write_text('bridge tool')
        persistent=self.root/'persistent';persistent.mkdir()
        with override_settings(BASE_DIR=code,CONFIG_DIR=persistent):
            migration.publish_tool(None,None,platform_name='nt')
        self.assertEqual((persistent/'SET_GPR_PORTAL_ACCOUNT.cmd').read_text(),'bridge launcher')
        self.assertEqual((persistent/'ops/set-windows-portal-account.ps1').read_text(),'bridge tool')

    def test_singleton_rejects_second_owner(self):
        with runtime.singleton(self.state/'test.lock'):
            with self.assertRaises(OSError):
                with runtime.singleton(self.state/'test.lock'):pass

    def test_interrupted_migration_recovers_before_restarting(self):
        old={'sha':'old','code':str(self.root),'python':'python'};candidate={'sha':'c'*40}
        (self.state/'bootstrap.py').write_text('previous runner')
        backup=runtime.backup_database(self.config,self.state)
        runtime.atomic_json(self.state/'current.json',old)
        runtime.atomic_json(self.state/'transaction.json',{'previous':old,'candidate':candidate,'backup':str(backup)})
        with sqlite3.connect(self.database) as db:db.execute("UPDATE marker SET value='partial migration'")
        (self.state/'bootstrap.py').write_text('new runner')
        runtime.recover_update(self.root)
        with sqlite3.connect(self.database) as db:self.assertEqual(db.execute('SELECT value FROM marker').fetchone()[0],'before')
        self.assertEqual((self.state/'bootstrap.py').read_text(),'previous runner')
        self.assertFalse((self.state/'transaction.json').exists())

    def test_activated_version_never_reverts_new_user_data(self):
        backup=runtime.backup_database(self.config,self.state)
        candidate={'sha':'d'*40};runtime.atomic_json(self.state/'current.json',candidate)
        runtime.atomic_json(self.state/'transaction.json',{'previous':{'sha':'old'},'candidate':candidate,'backup':str(backup)})
        with sqlite3.connect(self.database) as db:db.execute("UPDATE marker SET value='new user fact'")
        runtime.recover_update(self.root)
        with sqlite3.connect(self.database) as db:self.assertEqual(db.execute('SELECT value FROM marker').fetchone()[0],'new user fact')

    def test_initial_install_never_silently_prefers_fresh_over_working_database(self):
        (self.root/'data').mkdir();working=self.root/'data/db.sqlite3';fresh=self.root/'data/fresh.sqlite3'
        working.write_bytes(self.database.read_bytes());fresh.write_bytes(self.database.read_bytes())
        with self.assertRaises(RuntimeError):runtime.select_database(self.root,{'DJANGO_DB_PATH':'data/fresh.sqlite3'})
        self.assertEqual(runtime.select_database(self.root,{'DJANGO_DB_PATH':'data/fresh.sqlite3'},Path('data/db.sqlite3')),working.resolve())

    def test_database_switch_backs_up_both_files_and_updates_both_settings(self):
        target=self.root/'working.sqlite3'
        with sqlite3.connect(target) as db:
            for table in ['accounts_user','django_migrations']:db.execute('CREATE TABLE '+table+' (id integer)')
            db.execute('CREATE TABLE projects_project (id integer, name text)');db.execute("INSERT INTO projects_project VALUES (1,'Yamal')")
        (self.root/'.env').write_text('DJANGO_DB_PATH=existing.sqlite3\nLDAP_BIND_PASSWORD="keep-this-value"\n')
        current={'sha':'current','python':__import__('sys').executable,'code':str(self.root)}
        runtime.atomic_json(self.state/'current.json',current)
        with patch.object(runtime,'run') as run:
            runtime.switch_database(self.root,target)
        self.assertEqual(run.call_count,2)
        self.assertEqual(runtime.read_json(self.state/'installed.json')['database'],str(target))
        from dotenv import dotenv_values
        values=dotenv_values(self.root/'.env');self.assertEqual(values['DJANGO_DB_PATH'],target.as_posix());self.assertEqual(values['LDAP_BIND_PASSWORD'],'keep-this-value')
        self.assertEqual(len(list((self.state/'backups').glob('*/database.sqlite3'))),2)
        self.assertFalse((self.state/'database-switch.json').exists())
        with sqlite3.connect(self.database) as db:self.assertEqual(db.execute('SELECT value FROM marker').fetchone()[0],'before')

    def test_failed_switch_restores_target_schema_and_previous_configuration(self):
        target=self.root/'working.sqlite3'
        with sqlite3.connect(target) as db:
            for table in ['accounts_user','django_migrations']:db.execute('CREATE TABLE '+table+' (id integer)')
            db.execute('CREATE TABLE projects_project (id integer,name text)')
        before=dict(self.config);(self.root/'.env').write_text('DJANGO_DB_PATH=existing.sqlite3\nLDAP_BIND_PASSWORD=unchanged\n')
        runtime.atomic_json(self.state/'current.json',{'python':__import__('sys').executable,'code':str(self.root)})
        def fail(*args):
            with sqlite3.connect(target) as db:db.execute('CREATE TABLE unwanted (id integer)')
            raise RuntimeError('migration failed')
        with patch.object(runtime,'run',side_effect=fail):
            with self.assertRaises(RuntimeError):runtime.switch_database(self.root,target)
        self.assertEqual(runtime.read_json(self.state/'installed.json'),before)
        self.assertIn('LDAP_BIND_PASSWORD=unchanged',(self.root/'.env').read_text())
        with sqlite3.connect(target) as db:self.assertIsNone(db.execute("SELECT name FROM sqlite_master WHERE name='unwanted'").fetchone())

    def test_clean_database_creation_failure_never_switches_existing_database(self):
        current={'sha':'current','python':__import__('sys').executable,'code':str(self.root)}
        runtime.atomic_json(self.state/'current.json',current)
        with patch.object(runtime,'run',side_effect=RuntimeError('creation failed')),patch.object(runtime,'switch_database') as switch:
            with self.assertRaises(RuntimeError):runtime.create_clean_database(self.root,self.root/'new.sqlite3',False)
        switch.assert_not_called()
        self.assertEqual(runtime.read_json(self.state/'installed.json'),self.config)
        with sqlite3.connect(self.database) as db:self.assertEqual(db.execute('SELECT value FROM marker').fetchone()[0],'before')
        self.assertEqual(len(list((self.state/'backups').glob('*/database.sqlite3'))),1)
