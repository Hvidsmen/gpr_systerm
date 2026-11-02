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
        self.current={'owner_id':1,'company_id':10,'revision':'a'*40,'port':8002,'code':str(self.root),'python':'python'}
        self.config=patch.object(staging,'manifest',return_value=self.current);self.config.start();self.addCleanup(self.config.stop)
        self.event=threading.Event()

    def job(self):
        return {'id':'synthetic','owner_id':1,'revision':'b'*40,'from_revision':'a'*40,
                'state':'queued','branch':updates.BRANCH}

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
        with patch.object(updates,'download',return_value=archive), patch.object(updates,'stop_http',side_effect=subprocess.TimeoutExpired('http',115)), patch.object(staging,'update') as apply, patch.object(updates,'start_http') as start:
            self.assertIs(updates.process_job(self.root,child,self.job(),self.event),child)
        apply.assert_not_called();start.assert_not_called()

    def test_job_drains_http_before_applying_pinned_release_and_checks_restart(self):
        archive=self.root/'download.zip';archive.write_bytes(b'synthetic archive')
        old,new=Mock(),Mock();events=[]
        def apply(root,path,revision):
            self.assertEqual(events,['stopped'])
            self.assertEqual((root,path,revision),(self.root,archive,'b'*40))
            events.append('applied')
        with patch.object(updates,'download',return_value=archive), patch.object(updates,'stop_http',side_effect=lambda child:events.append('stopped')), patch.object(staging,'update',side_effect=apply), patch.object(updates,'start_http',return_value=new), patch.object(updates,'wait_http') as verify:
            self.assertIs(updates.process_job(self.root,old,self.job(),self.event),new)
        verify.assert_called_once_with(self.root,new,self.event)
        self.assertEqual(json.loads((self.root/'.runtime/staging-update.json').read_text())['state'],'done')
        self.assertFalse(archive.exists())

    def test_failed_migration_restarts_restored_version_and_cleans_only_new_candidate(self):
        archive=self.root/'download.zip';archive.touch();release=self.root/'.runtime/releases'/('b'*40)
        old,new=Mock(),Mock()
        def fail(*args):
            release.mkdir(parents=True)
            raise RuntimeError('synthetic restored migration failure')
        with patch.object(updates,'download',return_value=archive), patch.object(updates,'stop_http'), patch.object(staging,'update',side_effect=fail), patch.object(updates,'start_http',return_value=new), patch.object(updates,'wait_http'):
            self.assertIs(updates.process_job(self.root,old,self.job(),self.event),new)
        self.assertFalse(release.exists())
        self.assertEqual(json.loads((self.root/'.runtime/staging-update.json').read_text())['state'],'error')

    def test_restore_failure_never_restarts_http(self):
        archive=self.root/'download.zip';archive.touch()
        def fail(*args):
            atomic_json(self.root/'.runtime/staging-recovery.json',{'backup':'synthetic'})
            raise RuntimeError('synthetic restore failure')
        with patch.object(updates,'download',return_value=archive), patch.object(updates,'stop_http'), patch.object(staging,'update',side_effect=fail), patch.object(updates,'start_http') as start:
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
        with patch.object(updates,'download',return_value=archive), patch.object(updates,'stop_http') as stop, patch.object(staging,'update') as apply:
            updates.process_job(self.root,Mock(),self.job(),self.event)
        stop.assert_not_called();apply.assert_not_called();self.assertFalse(archive.exists())

    def test_interrupted_migration_requires_manual_recovery_before_http_starts(self):
        atomic_json(self.root/'.runtime/staging-update.json',{**self.job(),'state':'applying'})
        with patch.object(updates,'start_http') as start:
            with self.assertRaises(RuntimeError):updates.supervise(self.root,self.event)
        start.assert_not_called()
        self.assertTrue((self.root/'.runtime/staging-recovery.json').exists())
