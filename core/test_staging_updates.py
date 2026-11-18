from io import BytesIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import subprocess
import threading
from unittest.mock import Mock, patch

from django.test import SimpleTestCase

from ops import staging, staging_updates as updates
from ops.windows_runtime import atomic_json, singleton


class StagingWebRuntimeTests(SimpleTestCase):
    def setUp(self):
        temp=TemporaryDirectory();self.addCleanup(temp.cleanup)
        self.root=Path(temp.name);(self.root/'.runtime').mkdir()
        self.current={'owner_id':1,'company_id':10,'revision':'a'*40,'port':8002,'code':str(self.root),'python':'python','instance':'synthetic-instance'}
        self.config=patch.object(staging,'manifest',return_value=self.current);self.config.start();self.addCleanup(self.config.stop)
        self.event=threading.Event()

    def job(self):
        return {'id':'synthetic','owner_id':1,'revision':'b'*40,'from_revision':'a'*40,
                'state':'queued','branch':updates.BRANCH}

    def prepared(self):
        candidate=self.root/'.runtime/candidate.json';candidate.touch()
        return {'candidate_file':str(candidate)}

    def test_check_pins_only_fixed_test_branch_and_validates_github_response(self):
        body=json.dumps({'ref':'refs/heads/'+updates.BRANCH,'object':{'sha':'b'*40}}).encode()
        with patch.object(updates,'urlopen',return_value=BytesIO(body)) as fetch:
            self.assertEqual(updates.check_release(self.root)['revision'],'b'*40)
        self.assertIn('/git/ref/heads/codex/staging-portal-',fetch.call_args.args[0].full_url)
        for data in [{'ref':'refs/heads/main','object':{'sha':'b'*40}}, {'ref':'refs/heads/'+updates.BRANCH,'object':{'sha':'main'}}]:
            with patch.object(updates,'urlopen',return_value=BytesIO(json.dumps(data).encode())):
                with self.assertRaises(ValueError):updates.check_release(self.root)

    def test_download_failure_keeps_http_running_and_database_untouched(self):
        child=Mock();child.poll.return_value=None
        with patch.object(updates,'download',side_effect=OSError('synthetic network failure')), patch.object(staging,'update') as apply, patch.object(updates,'stop_http') as stop:
            self.assertIs(updates.process_job(self.root,child,self.job(),self.event),child)
        stop.assert_not_called();apply.assert_not_called()
        self.assertEqual(json.loads((self.root/'.runtime/staging-update.json').read_text())['state'],'error')

    def test_incomplete_download_is_removed_and_timeout_cannot_leave_a_candidate(self):
        response=Mock()
        response.__enter__=Mock(return_value=response);response.__exit__=Mock(return_value=False)
        response.read.return_value=b'synthetic bytes'
        with patch.object(updates,'urlopen',return_value=response), patch.object(updates.time,'monotonic',side_effect=[0,301]):
            with self.assertRaises(ValueError):updates.download(self.root,'b'*40)
        self.assertFalse(list((self.root/'.runtime').glob('staging-download-*.zip')))

    def test_http_stop_timeout_never_applies_or_starts_a_second_http_worker(self):
        archive=self.root/'download.zip';archive.touch();child=Mock()
        with patch.object(updates,'download',return_value=archive), patch.object(staging,'prepare_update',return_value=self.prepared()), patch.object(updates,'stop_http',side_effect=subprocess.TimeoutExpired('http',115)), patch.object(staging,'update') as apply, patch.object(updates,'start_http') as start:
            self.assertIs(updates.process_job(self.root,child,self.job(),self.event),child)
        apply.assert_not_called();start.assert_not_called()

    def test_job_drains_http_before_applying_pinned_release_and_checks_restart(self):
        archive=self.root/'download.zip';archive.write_bytes(b'synthetic archive')
        old,new=Mock(),Mock();events=[]
        def apply(root,path,revision,**kwargs):
            self.assertEqual(events,['stopped'])
            self.assertEqual((root,path,revision),(self.root,archive,'b'*40))
            events.append('applied')
        with patch.object(updates,'download',return_value=archive), patch.object(staging,'prepare_update',return_value=self.prepared()), patch.object(updates,'stop_http',side_effect=lambda child:events.append('stopped')), patch.object(staging,'update',side_effect=apply), patch.object(updates,'start_http',return_value=new), patch.object(updates,'wait_http') as verify:
            self.assertIs(updates.process_job(self.root,old,self.job(),self.event),new)
        verify.assert_called_once_with(self.root,new,self.event)
        self.assertEqual(json.loads((self.root/'.runtime/staging-update.json').read_text())['state'],'done')
        self.assertFalse(archive.exists())

    def test_windows_update_keeps_verified_http_child_under_existing_supervisor(self):
        archive=self.root/'download.zip';archive.write_bytes(b'synthetic archive')
        old,new=Mock(),Mock()
        with patch.object(updates,'download',return_value=archive),\
                patch.object(staging,'prepare_update',return_value=self.prepared()),\
                patch.object(updates,'stop_http') as stop,patch.object(staging,'update'),\
                patch.object(updates,'start_http',return_value=new),patch.object(updates,'wait_http'),\
                patch.object(updates,'needs_supervisor_handoff',return_value=True):
            result=updates.process_job(self.root,old,self.job(),self.event)
        self.assertIs(result,new)
        self.assertEqual([call.args[0] for call in stop.call_args_list],[old])

    def test_healthy_selected_worker_completes_only_interrupted_final_update(self):
        self.current.update(revision='b'*40)
        response=Mock(status=200,headers={'X-Portal-Environment':'staging','X-Portal-Version':('b'*12)})
        response.__enter__=Mock(return_value=response);response.__exit__=Mock(return_value=False)
        opener=Mock();opener.open.return_value=response
        job={**self.job(),'state':'error','failed_stage':'applying'}
        atomic_json(self.root/'.runtime/staging-update.json',job)
        with patch.object(updates,'build_opener',return_value=opener):
            self.assertTrue(updates.reconcile_installed_update(self.root))
        stored=json.loads((self.root/'.runtime/staging-update.json').read_text())
        self.assertEqual(stored['state'],'done');self.assertNotIn('failed_stage',stored)

    def test_worker_reconciliation_never_hides_unsafe_or_unrelated_failure(self):
        self.current.update(revision='b'*40)
        for job in [
                {**self.job(),'state':'error','failed_stage':'stopping'},
                {**self.job(),'state':'error','failed_stage':'applying','revision':'c'*40},
                {**self.job(),'state':'error','failed_stage':'applying','kind':'refresh'}]:
            atomic_json(self.root/'.runtime/staging-update.json',job)
            with patch.object(updates,'build_opener') as probe:
                self.assertFalse(updates.reconcile_installed_update(self.root))
            probe.assert_not_called()
        atomic_json(self.root/'.runtime/staging-recovery.json',{'reason':'synthetic'})
        atomic_json(self.root/'.runtime/staging-update.json',{**self.job(),'state':'starting'})
        self.assertFalse(updates.reconcile_installed_update(self.root))

    def test_supervisor_bridge_respawns_current_release_on_handoff_exit(self):
        first=Mock();first.poll.return_value=updates.SUPERVISOR_RESTART_EXIT
        first.returncode=updates.SUPERVISOR_RESTART_EXIT;first.stdin=BytesIO()
        second=Mock();second.poll.return_value=0;second.returncode=0;second.stdin=BytesIO()
        environment={'PATH':'synthetic'}
        with patch.object(staging,'environment',return_value=environment),\
                patch.object(updates.subprocess,'Popen',side_effect=[first,second]) as launch:
            updates.bridge_supervisor(self.root,self.event)
        self.assertEqual(launch.call_count,2)
        self.assertEqual(environment['GPR_STAGING_PARENT_PIPE'],'1')
        self.assertEqual(environment['GPR_STAGING_HANDOFF_PARENT'],'1')

    def test_failed_migration_restarts_restored_version_and_cleans_only_new_candidate(self):
        archive=self.root/'download.zip';archive.touch();release=self.root/'.runtime/releases'/('b'*40)
        old,new=Mock(),Mock()
        def fail(*args,**kwargs):
            release.mkdir(parents=True)
            raise RuntimeError('synthetic restored migration failure')
        with patch.object(updates,'download',return_value=archive), patch.object(staging,'prepare_update',return_value=self.prepared()), patch.object(updates,'stop_http'), patch.object(staging,'update',side_effect=fail), patch.object(updates,'start_http',return_value=new), patch.object(updates,'wait_http'):
            self.assertIs(updates.process_job(self.root,old,self.job(),self.event),new)
        self.assertFalse(release.exists())
        self.assertEqual(json.loads((self.root/'.runtime/staging-update.json').read_text())['state'],'error')

    def test_restore_failure_never_restarts_http(self):
        archive=self.root/'download.zip';archive.touch()
        def fail(*args,**kwargs):
            atomic_json(self.root/'.runtime/staging-recovery.json',{'backup':'synthetic'})
            raise RuntimeError('synthetic restore failure')
        with patch.object(updates,'download',return_value=archive), patch.object(staging,'prepare_update',return_value=self.prepared()), patch.object(updates,'stop_http'), patch.object(staging,'update',side_effect=fail), patch.object(updates,'start_http') as start:
            with self.assertRaises(RuntimeError):updates.process_job(self.root,Mock(),self.job(),self.event)
        start.assert_not_called()
        self.assertEqual(json.loads((self.root/'.runtime/staging-update.json').read_text())['state'],'recovery')

    def test_stale_or_foreign_job_never_downloads_or_stops_test(self):
        for job in [{**self.job(),'owner_id':2},{**self.job(),'from_revision':'c'*40},{**self.job(),'branch':'main'},{**self.job(),'revision':'../production'}]:
            with patch.object(updates,'download') as download, patch.object(updates,'stop_http') as stop:
                updates.process_job(self.root,Mock(),job,self.event)
                download.assert_not_called();stop.assert_not_called()

    def test_shutdown_cancels_downloaded_job_before_stopping_http(self):
        archive=self.root/'download.zip';archive.touch();self.event.set()
        with patch.object(updates,'download',return_value=archive), patch.object(staging,'prepare_update',return_value=self.prepared()), patch.object(updates,'stop_http') as stop, patch.object(staging,'update') as apply:
            updates.process_job(self.root,Mock(),self.job(),self.event)
        stop.assert_not_called();apply.assert_not_called();self.assertFalse(archive.exists())

    def test_interrupted_migration_requires_manual_recovery_before_http_starts(self):
        atomic_json(self.root/'.runtime/staging-update.json',{**self.job(),'state':'applying'})
        with patch.object(updates,'start_http') as start:
            with self.assertRaises(RuntimeError):updates.supervise(self.root,self.event)
        start.assert_not_called()
        self.assertTrue((self.root/'.runtime/staging-recovery.json').exists())

    def test_refresh_failure_restarts_existing_test_only_after_helper_returns_safely(self):
        from ops import staging_refresh
        job={'kind':'refresh','owner_id':1,'from_revision':'a'*40,'data_generation':self.current['instance']}
        old,new=Mock(),Mock()
        with patch.object(updates,'stop_http') as stop,patch.object(staging_refresh,'refresh',side_effect=OSError('synthetic source error')),patch.object(updates,'start_http',return_value=new),patch.object(updates,'wait_http'):
            self.assertIs(updates.process_refresh(self.root,old,job,self.event),new)
        stop.assert_called_once_with(old)
        self.assertEqual(json.loads((self.root/'.runtime/staging-update.json').read_text())['state'],'error')

    def test_interrupted_data_swap_never_starts_http_over_mixed_data(self):
        atomic_json(self.root/'.runtime/staging-refresh-transaction.json',{'backup':'synthetic'})
        with patch.object(updates,'start_http') as start:
            with self.assertRaises(RuntimeError):updates.supervise(self.root,self.event)
        start.assert_not_called()
        self.assertTrue((self.root/'.runtime/staging-recovery.json').exists())
