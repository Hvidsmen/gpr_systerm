import os
import sqlite3
import threading
from datetime import datetime, timezone as dt_timezone, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch, MagicMock
from django.core.management.base import CommandError
from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone
from .training_runtime import child_environment, snapshot, copy_files, prepare_database, Manager
from .models import TrainingCopy


class TrainingStorageTests(SimpleTestCase):
    def setUp(self):
        self.temp=TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.path=Path(self.temp.name)

    def test_snapshot_copies_wal_consistently_and_training_writes_do_not_touch_source(self):
        source=self.path/'live.sqlite3';target=self.path/'copy.sqlite3'
        with sqlite3.connect(source) as live:
            live.execute('pragma journal_mode=WAL');live.execute('create table sample(value text)')
            live.execute("insert into sample values ('original')");live.commit()
            with override_settings(TRAINING_ROOT=self.path/'isolated'):snapshot(source,target)
        with sqlite3.connect(target) as copy:copy.execute("update sample set value='exercise'")
        with sqlite3.connect(source) as live:self.assertEqual(live.execute('select value from sample').fetchone()[0],'original')

    def test_snapshot_rejects_same_file_and_source_inside_training_root(self):
        source=self.path/'live.sqlite3';source.touch()
        with override_settings(TRAINING_ROOT=self.path):
            with self.assertRaises(RuntimeError):snapshot(source,self.path/'other.sqlite3')
        with override_settings(TRAINING_ROOT=self.path/'isolated'):
            with self.assertRaises(RuntimeError):snapshot(source,source)

    def test_training_files_are_independent_and_symlinks_rejected(self):
        source=self.path/'source';source.mkdir();(source/'act.txt').write_text('original')
        with override_settings(TRAINING_ROOT=self.path/'isolated'):
            copy_files(source,self.path/'copy')
            (self.path/'copy'/'act.txt').write_text('exercise')
            self.assertEqual((source/'act.txt').read_text(),'original')
            (source/'link').symlink_to(source/'act.txt')
            with self.assertRaises(RuntimeError):copy_files(source,self.path/'unsafe')

    def test_child_environment_does_not_inherit_real_credentials(self):
        control=self.path/'group';generation=control/'generation';generation.mkdir(parents=True)
        (generation/'secret.key').write_text('only-training-key')
        with patch.dict(os.environ,{'LDAP_BIND_PASSWORD':'fake-test-secret','EMAIL_HOST_PASSWORD':'fake-test-secret',
                'GPR_UPDATE_TOKEN':'fake-test-secret','DJANGO_SECRET_KEY':'fake-production-key'}):
            env=child_environment(control,generation)
        self.assertNotIn('LDAP_BIND_PASSWORD',env);self.assertNotIn('EMAIL_HOST_PASSWORD',env);self.assertNotIn('GPR_UPDATE_TOKEN',env)
        self.assertEqual(env['DJANGO_SECRET_KEY'],'only-training-key')
        self.assertEqual(env['DJANGO_DB_PATH'],str(generation/'db.sqlite3'))
        self.assertEqual(env['DJANGO_SETTINGS_MODULE'],'config.training_settings')

    def test_training_storage_cannot_be_public_media_or_static(self):
        from .training import root
        from django.core.exceptions import ImproperlyConfigured
        for setting in ['MEDIA_ROOT','STATIC_ROOT']:
            with override_settings(**{setting:self.path, 'TRAINING_ROOT':self.path/'unsafe'}):
                with self.assertRaises(ImproperlyConfigured):root()
        with override_settings(TRAINING_ROOT=Path('relative-training')):
            with self.assertRaises(ImproperlyConfigured):root()

    def test_mssql_source_is_exported_for_isolated_sqlite_builder(self):
        generation=self.path/'generation';generation.mkdir()
        source=MagicMock();source.vendor='microsoft'
        with patch('apps.accounts.training_runtime.connections',{'default':source}),\
                patch('core.management.commands.prepare_mssql_copy.dump_fixture') as dump:
            dump.side_effect=lambda _alias,target:target.write_text('[]')
            command,fixture=prepare_database(self.path,generation)
        self.assertEqual(command,['build_training_copy','--fixture',str(generation/'portal-data.json')])
        self.assertEqual(fixture,generation/'portal-data.json')
        dump.assert_called_once_with('default',generation/'portal-data.json')

    def test_unsupported_source_database_is_rejected(self):
        source=MagicMock();source.vendor='postgresql'
        with patch('apps.accounts.training_runtime.connections',{'default':source}):
            with self.assertRaisesMessage(RuntimeError,'SQLite или Microsoft SQL Server'):
                prepare_database(self.path,self.path/'generation')

    def test_training_fixture_builder_runs_only_inside_its_generation(self):
        from apps.accounts.management.commands.build_training_copy import Command
        fixture=self.path/'portal-data.json';fixture.write_text('[]')
        database=MagicMock();database.vendor='sqlite'
        with override_settings(TRAINING_MODE=True,CONFIG_DIR=self.path),\
                patch('apps.accounts.management.commands.build_training_copy.connection',database),\
                patch('apps.accounts.management.commands.build_training_copy.call_command') as call:
            Command().handle(fixture=str(fixture))
        self.assertEqual([item.args[0] for item in call.call_args_list],
            ['migrate','flush','loaddata','prepare_training_copy'])
        outside=self.path.parent/'outside-training-fixture.json';outside.write_text('[]')
        self.addCleanup(outside.unlink,missing_ok=True)
        with override_settings(TRAINING_MODE=True,CONFIG_DIR=self.path),\
                patch('apps.accounts.management.commands.build_training_copy.connection',database):
            with self.assertRaisesMessage(CommandError,'внутри создаваемой учебной копии'):
                Command().handle(fixture=str(outside))


class TrainingScheduleTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        from .test_training import TrainingTests
        TrainingTests.setUpTestData.__func__(cls)

    def setUp(self):
        self.temp=TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.override=override_settings(TRAINING_ROOT=Path(self.temp.name));self.override.enable();self.addCleanup(self.override.disable)
        # Manager normally owns a long-lived worker-thread connection. TestCase
        # owns an outer transaction instead; closing it would roll back fixtures.
        closer=patch('apps.accounts.training_runtime.close_old_connections');closer.start();self.addCleanup(closer.stop)

    def test_due_slot_replays_once_after_missed_days(self):
        now=datetime(2026,11,1,4,0,tzinfo=dt_timezone.utc)
        TrainingCopy.objects.filter(pk=self.copy.pk).update(last_reset_at=now-timedelta(days=4))
        manager=Manager()
        with patch.object(manager,'reset') as reset:
            manager.tick(now)
        self.assertEqual(reset.call_count,1)

    def test_no_early_reset_and_queued_reset_has_priority(self):
        from . import training
        from .training_runtime import code_schema
        from ops.windows_runtime import atomic_json
        now=datetime(2026,11,1,3,59,tzinfo=dt_timezone.utc)
        TrainingCopy.objects.filter(pk=self.copy.pk).update(last_reset_at=now-timedelta(hours=12))
        manager=Manager();process=MagicMock();process.poll.return_value=None
        manager.workers[self.copy.pk]=(process,Path(self.temp.name))
        atomic_json(training.folder(self.copy)/'current.json',{'generation':'a'*32,'code_schema':code_schema()})
        with patch.object(manager,'reset') as reset:manager.tick(now);reset.assert_not_called()
        TrainingCopy.objects.filter(pk=self.copy.pk).update(requested_at=now)
        with patch.object(manager,'reset') as reset:manager.tick(now);reset.assert_called_once()

    def test_code_or_schema_update_refreshes_before_launching_old_database(self):
        from . import training
        from ops.windows_runtime import atomic_json
        TrainingCopy.objects.filter(pk=self.copy.pk).update(last_reset_at=timezone.now())
        atomic_json(training.folder(self.copy)/'current.json',{'generation':'a'*32,'code_schema':'old-schema'})
        manager=Manager()
        with patch.object(manager,'reset') as reset,patch.object(manager,'start') as start:
            manager.tick()
        reset.assert_called_once();start.assert_not_called()

    def test_failed_preparation_keeps_old_copy_running_and_records_failure(self):
        manager=Manager();process=MagicMock();process.poll.return_value=None
        manager.workers[self.copy.pk]=(process,Path(self.temp.name))
        with patch('apps.accounts.training_runtime.snapshot',side_effect=RuntimeError('test failure')):
            manager.reset(self.copy,timezone.now())
        self.copy.refresh_from_db()
        self.assertEqual(self.copy.state,'READY')
        process.terminate.assert_not_called();process.kill.assert_not_called()
        self.assertEqual(self.copy.runs.get().status,'ERROR')
        self.assertIsNone(self.copy.last_reset_at)

    def test_activation_failure_restores_previous_generation_and_ticket_key(self):
        from . import training
        from ops.windows_runtime import atomic_json,read_json
        control=training.folder(self.copy);control.mkdir()
        previous={'generation':'a'*32,'created_at':timezone.now().isoformat()}
        old=control/previous['generation'];old.mkdir();(old/'db.sqlite3').touch()
        atomic_json(control/'current.json',previous);(control/'ticket.key').write_text('previous-key')
        manager=Manager()
        with patch('apps.accounts.training_runtime.snapshot'),patch('apps.accounts.training_runtime.copy_files'),\
                patch('apps.accounts.training_runtime.subprocess.run',return_value=__import__('types').SimpleNamespace(returncode=0,stdout='',stderr='')),\
                patch.object(manager,'start',side_effect=[RuntimeError('simulated launch failure'),None]) as start:
            manager.reset(self.copy,timezone.now())
        self.assertEqual(start.call_count,2)
        self.assertEqual(read_json(control/'current.json'),previous)
        self.assertEqual((control/'ticket.key').read_text(),'previous-key')
        self.assertTrue(old.exists())
        self.assertEqual(self.copy.runs.get().status,'ERROR')
