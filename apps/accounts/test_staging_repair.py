import json
from pathlib import Path
from tempfile import TemporaryDirectory
import sys
import threading
from unittest import TestCase
from unittest.mock import Mock, MagicMock, patch
import zipfile
from ops import staging, staging_repair, staging_updates
from ops.windows_runtime import atomic_json, singleton


class StagingRepairTests(TestCase):
    def setUp(self):
        self.temp=TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)/'test';(self.root/'.runtime').mkdir(parents=True);(self.root/'data').mkdir()
        # Windows runner TEMP can use an 8.3 alias; match the real helper's resolved path.
        self.root=self.root.resolve()
        self.old='a'*40;self.new='b'*40
        self.config=dict(kind=staging.KIND,instance='11111111-1111-4111-8111-111111111111',owner_id=1,company_id=1,owner_login='synthetic-owner',port=18002,hosts=['127.0.0.1'],revision=self.old,code=str(self.root),python=sys.executable,static='staticfiles',production_url='http://127.0.0.1:18001/',source={name:str(Path(self.temp.name)/'production'/name) for name in ['database','media','actuation']})
        atomic_json(self.root/'staging.json',self.config)
        (self.root/'data/db.sqlite3').write_bytes(b'synthetic unchanged data')
        (self.root/'secret.key').write_text('synthetic unchanged test key')
        atomic_json(self.root/'.runtime/staging-update.json',{'state':'error'})
        self.source=self.root/'.runtime/repair-code'/self.new
        for relative in ['manage.py','requirements.txt','ops/windows_runtime.py','ops/staging.py','ops/staging_repair.py','config/staging_settings.py']:
            p=self.source/relative;p.parent.mkdir(parents=True,exist_ok=True);p.write_text('synthetic source')

    def archive(self):
        with patch.object(staging_repair,'CODE_ROOT',self.source):return Path(staging_repair.pack(self.root,self.new)['archive'])

    def test_preflight_is_read_only_and_rejects_busy_or_recovery(self):
        before=(self.root/'staging.json').read_bytes();self.assertEqual(staging_repair.check(self.root,self.new)['revision'],self.old)
        self.assertEqual((self.root/'staging.json').read_bytes(),before)
        atomic_json(self.root/'.runtime/staging-update.json',{'state':'stopping'})
        with self.assertRaises(ValueError):staging_repair.check(self.root,self.new)
        atomic_json(self.root/'.runtime/staging-update.json',{'state':'error'})
        for name in ['staging-recovery.json','staging-refresh-transaction.json']:
            path=self.root/'.runtime'/name;path.write_text('{}')
            with self.assertRaises(ValueError):staging_repair.check(self.root,self.new)
            path.unlink()

    def test_production_markers_and_unpinned_version_are_refused(self):
        with self.assertRaises(ValueError):staging_repair.check(self.root,'main')
        for relative in ['.runtime/installed.json','.env']:
            path=self.root/relative;path.write_text('production marker')
            with self.assertRaises(ValueError):staging_repair.check(self.root,self.new)
            path.unlink()

    def test_packing_uses_verified_source_and_preserves_data_and_private_key(self):
        (self.source/'.git').mkdir();(self.source/'.git/config').write_text('do not pack')
        archive=self.archive()
        with zipfile.ZipFile(archive) as content:
            names=content.namelist();self.assertIn('gpr_systerm-'+self.new+'/manage.py',names)
            self.assertFalse(any('.git/' in path or 'secret.key' in path or 'data/db.sqlite3' in path for path in names))
        self.assertEqual((self.root/'data/db.sqlite3').read_bytes(),b'synthetic unchanged data')
        self.assertEqual((self.root/'secret.key').read_text(),'synthetic unchanged test key')
        (self.source/'secret.key').write_text('must not be packed')
        with patch.object(staging_repair,'CODE_ROOT',self.source),self.assertRaises(ValueError):staging_repair.pack(self.root,self.new)

    def test_apply_requires_same_revision_stop_marker_and_private_archive(self):
        archive=self.archive()
        with patch.object(staging,'update') as update:
            with self.assertRaises(ValueError):staging_repair.apply(self.root,self.new,archive,self.old)
            (self.root/'.runtime/server.stop').touch()
            with self.assertRaises(ValueError):staging_repair.apply(self.root,self.new,archive,'c'*40)
            with self.assertRaises(ValueError):staging_repair.apply(self.root,self.new,Path(self.temp.name)/'foreign.zip',self.old)
            update.assert_not_called()

    def test_live_http_lock_blocks_install_before_dependencies_or_database_changes(self):
        archive=self.archive();(self.root/'.runtime/server.stop').touch()
        with singleton(self.root/'.runtime/server.lock'),patch('ops.staging.subprocess.run') as install:
            with self.assertRaises(OSError):staging_repair.apply(self.root,self.new,archive,self.old)
            install.assert_not_called()
        self.assertEqual(staging.manifest(self.root)['revision'],self.old)
        self.assertEqual((self.root/'data/db.sqlite3').read_bytes(),b'synthetic unchanged data')

    def test_success_retains_test_identity_and_marks_done_only_after_correct_http_health(self):
        archive=self.archive();(self.root/'.runtime/server.stop').touch()
        def install(*args):atomic_json(self.root/'staging.json',{**self.config,'revision':self.new})
        with patch.object(staging,'update',side_effect=install):result=staging_repair.apply(self.root,self.new,archive,self.old)
        job=json.loads((self.root/'.runtime/staging-update.json').read_text());self.assertEqual(job['state'],'starting');self.assertTrue(job['manual_repair'])
        response=Mock();response.status=200;response.headers={'X-Portal-Environment':'production','X-Portal-Version':self.new[:12]}
        opener=MagicMock();opener.open.return_value.__enter__.return_value=response
        with patch.object(staging_repair,'build_opener',return_value=opener):
            with self.assertRaises(ValueError):staging_repair.complete(self.root,self.new,result['job_id'])
            self.assertEqual(json.loads((self.root/'.runtime/staging-update.json').read_text())['state'],'starting')
            response.headers['X-Portal-Environment']='staging'
            self.assertEqual(staging_repair.complete(self.root,self.new,result['job_id'])['state'],'done')
        self.assertEqual(staging.manifest(self.root)['instance'],self.config['instance'])
        self.assertEqual((self.root/'data/db.sqlite3').read_bytes(),b'synthetic unchanged data')

    def test_another_job_is_never_overwritten_on_completion(self):
        atomic_json(self.root/'staging.json',{**self.config,'revision':self.new})
        job={'id':'colleague-job','state':'queued','revision':self.new,'manual_repair':True}
        atomic_json(self.root/'.runtime/staging-update.json',job)
        with self.assertRaises(ValueError):staging_repair.complete(self.root,self.new,'old-job')
        self.assertEqual(json.loads((self.root/'.runtime/staging-update.json').read_text()),job)

    def test_failed_stop_cannot_start_migrations_or_change_selected_version(self):
        archive=self.archive();child=Mock();child.stdin=None;child.pid=123;child.poll.return_value=None
        import subprocess
        child.wait.side_effect=subprocess.TimeoutExpired('synthetic HTTP child',115)
        child.terminate.side_effect=PermissionError('synthetic denied')
        job={'id':'failed-stop','state':'queued','revision':self.new,'from_revision':self.old,'owner_id':1,'branch':staging_updates.BRANCH}
        with patch.object(staging,'prepare_update',return_value={'candidate_file':str(self.root/'.runtime/synthetic-candidate.json')}),patch.object(staging_updates,'download',return_value=archive),patch.object(staging,'update') as update,patch.object(staging_updates,'start_http') as start:
            self.assertIs(staging_updates.process_job(self.root,child,job,threading.Event()),child)
            update.assert_not_called();start.assert_not_called()
        stored=json.loads((self.root/'.runtime/staging-update.json').read_text());self.assertEqual(stored['state'],'error');self.assertEqual(stored['failed_stage'],'stopping')
        self.assertEqual(staging.manifest(self.root)['revision'],self.old)

    def test_failed_setup_cleanup_cannot_prevent_old_test_restart(self):
        import subprocess
        archive=self.archive();old=Mock();replacement=Mock()
        release=self.root/'.runtime/releases'/self.new
        def failed_update(*args,**kwargs):
            release.mkdir(parents=True)
            raise subprocess.TimeoutExpired('synthetic venv creation',300)
        job={'id':'failed-setup','state':'queued','revision':self.new,'from_revision':self.old,'owner_id':1,'branch':staging_updates.BRANCH}
        with patch.object(staging,'prepare_update',return_value={'candidate_file':str(self.root/'.runtime/synthetic-candidate.json')}),patch.object(staging_updates,'download',return_value=archive),patch.object(staging_updates,'stop_http'),patch.object(staging,'update',side_effect=failed_update),patch('shutil.rmtree',side_effect=PermissionError('synthetic locked venv')),patch.object(staging_updates,'start_http',return_value=replacement) as start,patch.object(staging_updates,'wait_http'):
            self.assertIs(staging_updates.process_job(self.root,old,job,threading.Event()),replacement)
            start.assert_called_once_with(self.root)
        self.assertEqual(staging.manifest(self.root)['revision'],self.old)
        self.assertEqual((self.root/'data/db.sqlite3').read_bytes(),b'synthetic unchanged data')
        self.assertEqual(json.loads((self.root/'.runtime/staging-update.json').read_text())['failed_stage'],'applying')
