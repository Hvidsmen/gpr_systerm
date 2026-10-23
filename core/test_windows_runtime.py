import io
import json
import os
from pathlib import Path
import sqlite3
import tempfile
from unittest.mock import patch, Mock
import zipfile
from django.test import SimpleTestCase
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

    def test_success_commits_release_before_public_start(self):
        current={'sha':'old','code':str(self.root),'python':'python'}
        code=self.root/'release';(code/'ops').mkdir(parents=True);(code/'ops/windows_runtime.py').write_text('new bootstrap')
        candidate={'sha':'b'*40,'code':str(code),'python':'python-new'}
        with patch.object(runtime,'stage_update',return_value=candidate),patch.object(runtime,'stop_process'),patch.object(runtime,'run'),patch.object(runtime,'healthy'),patch.object(runtime,'launch',return_value=(Mock(),8001)):
            _,result,message=runtime.update(self.root,Mock(),current)
        self.assertEqual(result,candidate);self.assertEqual(runtime.read_json(self.state/'current.json'),candidate)
        self.assertEqual((self.state/'bootstrap.py').read_text(),'new bootstrap')

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
