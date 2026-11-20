"""Prepare/check a TEST release before its HTTP worker or latest data is stopped."""
import json
from contextlib import closing
import os
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
        current=Path(staging.manifest(self.root)['code']).parent
        os.utime(current,(1,1))
        for index in range(4):
            stale=self.root/'.runtime/releases'/('c'*39+str(index));stale.mkdir(parents=True)
            os.utime(stale,(10+index,10+index))
        prepared=staging.prepare_update(self.root,self.archive,self.new)
        staging.update(self.root,self.archive,self.new,prepared=prepared)
        self.assertEqual([c.args[1][0] for c in self.manage.call_args_list],['check','migrate','collectstatic'])
        self.venv.assert_called_once();self.assertEqual(staging.manifest(self.root)['revision'],self.new)
        self.assertEqual(self.value(),'before preparation')
        self.assertTrue(current.exists())

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
        self.assertEqual(stored['failed_detail'],'synthetic stalled check')
        self.assertIn('TEST update: FAILED synthetic stalled check',(self.root/'.runtime/prepare.log').read_text(encoding='utf-8'))
        with singleton(self.root/'.runtime/staging-supervisor.lock'):
            self.assertIn('Причина: synthetic stalled check',staging_updates.state(self.root)['message'])
        self.assertEqual(self.value(),'before preparation');self.assertEqual(staging.manifest(self.root)['revision'],self.old)
        self.assertFalse((self.root/'.runtime/releases'/self.new).exists());self.assertEqual(list((self.root/'.runtime').glob('candidate-*.json')),[])

    def test_successful_windows_update_hands_control_to_new_supervisor(self):
        child=Mock();replacement=Mock();job=self.job()
        archive=self.root/'handoff.zip';archive.write_bytes(self.archive.read_bytes())
        candidate=self.root/'.runtime/handoff-candidate.json';candidate.write_text('{}')
        def apply(*args,**kwargs):
            state=staging.manifest(self.root);state['revision']=self.new
            atomic_json(self.root/'staging.json',state)
        with patch.object(staging_updates,'download',return_value=archive),\
             patch.object(staging,'prepare_update',return_value={'candidate_file':str(candidate)}),\
             patch.object(staging,'update',side_effect=apply),\
             patch.object(staging_updates,'stop_http') as stop,\
             patch.object(staging_updates,'start_http',return_value=replacement),\
             patch.object(staging_updates,'wait_http'),\
             patch.object(staging_updates,'needs_supervisor_handoff',return_value=True):
            result=staging_updates.process_job(self.root,child,job,threading.Event())
        self.assertIs(result,staging_updates.SUPERVISOR_RESTART)
        self.assertEqual([call.args[0] for call in stop.call_args_list],[child,replacement])

    def test_bridge_restarts_selected_release_after_one_unexpected_crash(self):
        crashed=Mock(returncode=3221225477);crashed.poll.return_value=3221225477
        crashed.stdin=Mock(closed=False)
        healthy=Mock(returncode=None);healthy.poll.return_value=None;healthy.stdin=Mock(closed=False)
        parent=Mock();parent.is_set.side_effect=[False,False,True];parent.wait.return_value=False
        with patch.object(staging_updates.subprocess,'Popen',side_effect=[crashed,healthy]) as spawn,\
                patch.object(staging_updates.logging,'error') as logged,patch.object(staging_updates,'stop_http') as stop:
            staging_updates.bridge_supervisor(self.root,parent)
        self.assertEqual(spawn.call_count,2)
        stop.assert_called_once_with(healthy,graceful_timeout=150)
        logged.assert_called_once_with(
            'TEST supervisor exited with code %s; restarting selected release in %ss.',3221225477,1)

    def test_bridge_restarts_after_unexpected_clean_exit(self):
        exited=Mock(returncode=0);exited.poll.return_value=0;exited.stdin=Mock(closed=False)
        running=Mock(returncode=None);running.poll.return_value=None;running.stdin=Mock(closed=False)
        parent=Mock();parent.is_set.side_effect=[False,False,True];parent.wait.return_value=False
        with patch.object(staging_updates.subprocess,'Popen',side_effect=[exited,running]) as spawn,\
                patch.object(staging_updates.logging,'error') as logged,patch.object(staging_updates,'stop_http') as stop:
            staging_updates.bridge_supervisor(self.root,parent)
        self.assertEqual(spawn.call_count,2)
        stop.assert_called_once_with(running,graceful_timeout=150)
        logged.assert_called_once_with(
            'TEST supervisor exited with code %s; restarting selected release in %ss.',0,1)

    def test_mssql_backup_and_restore_use_selected_test_release(self):
        state={**staging.manifest(self.root),'database_engine':'mssql','mssql':{'host':'MSK-DB2','name':'YDS_PORTAL_TEST'}}
        folder=self.root/'.runtime/backups/mssql'
        def management(root,command,**kwargs):
            if command[1]=='create':Path(command[-1]).write_text('{}')
        with patch.object(staging,'run_manage',side_effect=management) as run:
            backup=staging.backup_database(self.root,state,folder)
            staging.restore_database(self.root,state,backup)
        self.assertEqual(backup,folder/'mssql-backup.json');self.assertEqual(run.call_count,2)
        self.assertEqual(run.call_args_list[0].args[1][:2],['mssql_backup','create'])
        self.assertEqual(run.call_args_list[1].args[1][:2],['mssql_backup','restore'])

    def test_detail_is_available_through_normal_status_before_stop(self):
        job=self.job()
        with singleton(self.root/'.runtime/staging-supervisor.lock'):
            staging_updates.record_progress(self.root,job,'preparing')
            staging_updates.record_detail(self.root,job,'Проверка Django')
            status=staging_updates.state(self.root)
        self.assertTrue(status['busy']);self.assertEqual(status['state'],'preparing')
        self.assertEqual(status['stage_number'],3);self.assertEqual(status['stage_total'],6)
        self.assertEqual(status['message'],'Проверка Django')
