"""Prepare/check a TEST release before its HTTP worker or latest data is stopped."""
import json
from contextlib import closing
from pathlib import Path
import sqlite3
import sys
import threading
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import Mock,patch
import uuid
import zipfile
from ops import staging,staging_updates
from ops.windows_runtime import atomic_json,singleton


class StagingPreflightTests(TestCase):
    def setUp(self):
        folder=TemporaryDirectory();self.addCleanup(folder.cleanup)
        base=Path(folder.name).resolve();self.root=base/'test';self.prod=base/'production'
        self.old='a'*40;self.new='b'*40
        for path in [self.root/'data',self.root/'.runtime',self.root/'staticfiles',self.prod]:path.mkdir(parents=True,exist_ok=True)
        self.db=self.root/'data/db.sqlite3'
        with closing(sqlite3.connect(self.db)) as db,db:db.execute('CREATE TABLE marker(value TEXT)');db.execute("INSERT INTO marker VALUES ('before preparation')")
        code=self.root/'.runtime/releases'/self.old/'code';(code/'ops').mkdir(parents=True)
        cfg={'kind':staging.KIND,'instance':str(uuid.uuid4()),'revision':self.old,'owner_id':1,'company_id':10,'owner_login':'admin','port':18002,'hosts':['127.0.0.1'],
             'code':str(code),'python':sys.executable,'static':'staticfiles','source':{'database':str(self.prod/'db.sqlite3'),'media':str(self.prod/'media'),'actuation':str(self.prod/'private')}}
        atomic_json(self.root/'staging.json',cfg)
        self.archive=self.root/'candidate.zip'
        with zipfile.ZipFile(self.archive,'w') as archive:
            for path in ['manage.py','ops/staging.py','ops/windows_runtime.py','config/staging_settings.py','requirements.txt','requirements.staging.lock']:
                archive.writestr('gpr_systerm-'+self.new+'/'+path,'synthetic source')
        from ops.windows_runtime import python_at
        self.venv=self.enterContext(patch('ops.staging_venv.create_test_venv',side_effect=lambda path,**kwargs:python_at(path)))
        self.setup=self.enterContext(patch('ops.staging_venv.run_setup'))
        self.manage=self.enterContext(patch.object(staging,'run_manage'))

    def value(self):
        with closing(sqlite3.connect(self.db)) as db:return db.execute('SELECT value FROM marker').fetchone()[0]

    def job(self):return {'id':'preflight-job','state':'queued','revision':self.new,'from_revision':self.old,'owner_id':1,'branch':staging_updates.BRANCH,'created_at':staging_updates.now()}

    def test_preparation_works_while_http_lock_is_held_and_does_not_switch_manifest_or_data(self):
        with singleton(self.root/'.runtime/server.lock'):
            prepared=staging.prepare_update(self.root,self.archive,self.new)
            self.assertEqual(staging.manifest(self.root)['revision'],self.old)
            self.assertEqual(self.value(),'before preparation')
            with self.assertRaises(OSError):staging.update(self.root,self.archive,self.new,prepared=prepared)
        self.assertEqual([call.args[1] for call in self.manage.call_args_list],[['check']])
        self.assertTrue(Path(prepared['candidate_file']).is_file())

    def test_snapshot_includes_writes_after_preparation_and_rollback_preserves_them(self):
        prepared=staging.prepare_update(self.root,self.archive,self.new)
        with closing(sqlite3.connect(self.db)) as db,db:db.execute("UPDATE marker SET value='saved before actual stop'")
        def fail(root,command,**kwargs):
            if command[0]=='migrate':
                with closing(sqlite3.connect(self.db)) as db,db:db.execute("UPDATE marker SET value='failed migration'")
                raise RuntimeError('synthetic migration failure')
        self.manage.side_effect=fail
        with self.assertRaisesRegex(RuntimeError,'synthetic migration failure'):staging.update(self.root,self.archive,self.new,prepared=prepared)
        self.assertEqual(self.value(),'saved before actual stop')
        self.assertEqual(staging.manifest(self.root)['revision'],self.old)
        self.assertFalse(Path(prepared['candidate_file']).exists())

    def test_success_applies_once_without_recreating_environment_or_rechecking(self):
        prepared=staging.prepare_update(self.root,self.archive,self.new)
        staging.update(self.root,self.archive,self.new,prepared=prepared)
        self.assertEqual([c.args[1][0] for c in self.manage.call_args_list],['check','migrate','collectstatic'])
        self.venv.assert_called_once();self.assertEqual(staging.manifest(self.root)['revision'],self.new)
        self.assertEqual(self.value(),'before preparation')

    def test_changed_or_forged_prepared_candidate_is_rejected_before_backup(self):
        prepared=staging.prepare_update(self.root,self.archive,self.new)
        for change in ({'root':str(self.prod)},{'previous':{}},{'candidate':{**prepared['candidate'],'revision':'c'*40}}):
            with self.assertRaises(ValueError):staging.update(self.root,self.archive,self.new,prepared={**prepared,**change})
        self.assertFalse((self.root/'.runtime/backups').exists());self.assertEqual(self.value(),'before preparation')

    def test_check_failure_keeps_original_http_alive_and_can_be_retried(self):
        child=Mock();job=self.job();self.manage.side_effect=RuntimeError('synthetic stalled check')
        with patch.object(staging_updates,'download',return_value=self.archive),patch.object(staging_updates,'stop_http') as stop,patch.object(staging_updates,'start_http') as start:
            self.assertIs(staging_updates.process_job(self.root,child,job,threading.Event()),child)
        stop.assert_not_called();start.assert_not_called()
        self.manage.assert_called_once()
        stored=json.loads((self.root/'.runtime/staging-update.json').read_text(encoding='utf-8'))
        self.assertEqual(stored['state'],'error');self.assertEqual(stored['failed_stage'],'preparing')
        self.assertEqual(self.value(),'before preparation');self.assertEqual(staging.manifest(self.root)['revision'],self.old)
        self.assertFalse((self.root/'.runtime/releases'/self.new).exists());self.assertEqual(list((self.root/'.runtime').glob('candidate-*.json')),[])

    def test_detail_is_available_through_normal_status_before_stop(self):
        job=self.job()
        with singleton(self.root/'.runtime/staging-supervisor.lock'):
            staging_updates.record_progress(self.root,job,'preparing')
            staging_updates.record_detail(self.root,job,'Проверка Django')
            status=staging_updates.state(self.root)
        self.assertTrue(status['busy']);self.assertEqual(status['state'],'preparing')
        self.assertEqual(status['stage_number'],3);self.assertEqual(status['stage_total'],6)
        self.assertEqual(status['message'],'Проверка Django')
