import hashlib
from io import StringIO
import json
import os
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
from unittest.mock import patch
import zipfile
from django.test import SimpleTestCase
from ops import staging
from ops.windows_runtime import atomic_json, singleton


class StagingRuntimeTests(SimpleTestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.parent = Path(self.tmp.name); self.root = self.parent / 'test'; self.root.mkdir()
        self.prod = self.parent / 'production'; (self.prod / '.runtime').mkdir(parents=True)
        self.db = self.prod / 'live.sqlite3'
        with sqlite3.connect(self.db) as db:
            db.execute('CREATE TABLE accounts_user (id INTEGER, company_id INTEGER, username TEXT, ldap_login TEXT, is_active INTEGER, archived_at TEXT)')
            db.execute("INSERT INTO accounts_user VALUES (1,10,'ad:owner','owner',1,NULL)")
            db.execute("CREATE TABLE marker (value TEXT)"); db.execute("INSERT INTO marker VALUES ('production')")
        self.media = self.prod / 'media'; self.media.mkdir(); (self.media / 'original.txt').write_text('live file')
        self.config = {'database': str(self.db), 'media': str(self.media), 'port': 8001, 'hosts': ['192.168.70.9']}
        atomic_json(self.prod / '.runtime/installed.json', self.config)
        self.code = patch.object(staging, 'CODE_ROOT', self.root); self.code.start(); self.addCleanup(self.code.stop)

    def digest(self):return hashlib.sha256(self.db.read_bytes()).hexdigest()

    def fake_manage(self, root, command, **kwargs):
        log = root / '.runtime/prepare.log'; log.parent.mkdir(exist_ok=True); log.write_text('synthetic check')
        if command[0] == 'collectstatic':(root / 'staticfiles').mkdir(exist_ok=True)

    def prepare(self):
        with patch.object(staging, 'run_manage', side_effect=self.fake_manage), patch.object(staging.socket, 'socket'):
            return staging.prepare(self.root, self.prod, 'owner', 'synthetic-test-password', revision='a' * 40)

    def test_snapshot_and_file_copy_never_change_source_and_are_independent(self):
        before = self.digest(); state = self.prepare()
        self.assertEqual(self.digest(), before)
        self.assertEqual((state['owner_id'], state['company_id']), (1, 10))
        with sqlite3.connect(self.root / 'data/db.sqlite3') as db:db.execute("UPDATE marker SET value='test only'")
        (self.root / 'data/media/original.txt').write_text('test changed')
        self.assertEqual(self.digest(), before)
        self.assertEqual((self.media / 'original.txt').read_text(), 'live file')
        self.assertEqual(staging.manifest(self.root)['revision'], 'a' * 40)

    def test_existing_test_is_never_reset_or_overwritten(self):
        self.prepare(); before = (self.root / 'secret.key').read_bytes()
        with self.assertRaisesMessage(ValueError, 'не перезаписываются'):self.prepare()
        self.assertEqual((self.root / 'secret.key').read_bytes(), before)

    def test_port_and_nested_directories_rejected_before_copy(self):
        for root, port in [(self.root, 8001), (self.prod / 'test', 8002), (self.parent, 8002)]:
            with self.assertRaises(ValueError):
                staging.prepare(root, self.prod, 'owner', 'synthetic-test-password', port=port, revision='a' * 40)
        self.assertFalse((self.root / 'data').exists())

    def test_ambiguous_owner_and_failed_migration_do_not_publish_or_touch_source(self):
        before = self.digest()
        with patch.object(staging, 'run_manage', side_effect=RuntimeError('synthetic migration failure')), patch.object(staging.socket, 'socket'):
            with self.assertRaises(RuntimeError):
                staging.prepare(self.root, self.prod, 'owner', 'synthetic-test-password', revision='a' * 40)
        self.assertEqual(self.digest(), before); self.assertFalse((self.root / 'staging.json').exists())
        with sqlite3.connect(self.db) as db:db.execute("INSERT INTO accounts_user VALUES (2,10,'owner','different',1,NULL)")
        with self.assertRaisesMessage(ValueError, 'одного активного'):self.prepare()
        self.assertFalse((self.root / 'staging.json').exists())

    def test_file_links_and_manifest_path_escape_are_rejected(self):
        link = self.media / 'link'; link.symlink_to(self.parent, target_is_directory=True)
        with self.assertRaises(ValueError):self.prepare()
        self.assertFalse((self.root / 'staging.json').exists()); link.unlink()
        self.prepare()
        (self.root / 'data/media').rename(self.root / 'data/copied-media')
        (self.root / 'data/media').symlink_to(self.media, target_is_directory=True)
        with self.assertRaises(ValueError):staging.manifest(self.root)

    def test_live_environment_credentials_and_paths_are_not_inherited(self):
        with patch.dict(os.environ, {'DJANGO_DB_PATH': str(self.db), 'LDAP_BIND_PASSWORD': 'synthetic-secret',
                    'GITHUB_TOKEN': 'synthetic-token', 'EMAIL_HOST_PASSWORD': 'synthetic-mail', 'PATH': '/safe/path'}):
            env = staging.environment(self.root)
        for key in ['DJANGO_DB_PATH', 'LDAP_BIND_PASSWORD', 'GITHUB_TOKEN', 'EMAIL_HOST_PASSWORD']:self.assertNotIn(key, env)
        self.assertEqual(env['PATH'], '/safe/path')
        self.assertEqual(env['DJANGO_SETTINGS_MODULE'], 'config.staging_settings')

    def archive(self, revision='b' * 40):
        archive = self.parent / 'candidate.zip'
        with zipfile.ZipFile(archive, 'w') as zipped:
            for name in ['manage.py', 'requirements.txt', 'requirements.staging.lock', 'ops/windows_runtime.py', 'ops/staging.py', 'config/staging_settings.py']:
                zipped.writestr('gpr_systerm-' + revision + '/' + name, 'synthetic code')
        return archive

    def test_update_requires_explicit_matching_version_and_stopped_test(self):
        self.prepare(); archive = self.archive()
        with self.assertRaises(ValueError):staging.update(self.root, archive, 'main')
        with self.assertRaises(ValueError):staging.update(self.root, archive, 'c' * 40)
        with singleton(self.root / '.runtime/server.lock'), patch.object(staging.subprocess, 'run') as run:
            with self.assertRaises(OSError):staging.update(self.root, archive, 'b' * 40)
            run.assert_not_called()

    def test_failed_candidate_restores_only_stopped_test_database(self):
        self.prepare(); before = self.digest()
        def fail(root, command, **kwargs):
            if command[0] == 'migrate':
                with sqlite3.connect(root / 'data/db.sqlite3') as db:db.execute("UPDATE marker SET value='bad migration'")
                raise RuntimeError('synthetic failure')
        with patch.object(staging.subprocess, 'run'), patch.object(staging, 'run_manage', side_effect=fail):
            with self.assertRaises(RuntimeError):staging.update(self.root, self.archive(), 'b' * 40)
        self.assertEqual(staging.manifest(self.root)['revision'], 'a' * 40)
        with sqlite3.connect(self.root / 'data/db.sqlite3') as db:self.assertEqual(db.execute('SELECT value FROM marker').fetchone()[0], 'production')
        self.assertEqual(self.digest(), before)

    def test_successful_candidate_changes_code_pointer_not_production_or_test_identity(self):
        original = self.prepare(); before = self.digest()
        with patch.object(staging.subprocess, 'run'), patch.object(staging, 'run_manage'):
            staging.update(self.root, self.archive(), 'b' * 40)
        current = staging.manifest(self.root)
        self.assertEqual(current['revision'], 'b' * 40)
        self.assertEqual(current['instance'], original['instance'])
        self.assertEqual(current['owner_id'], original['owner_id'])
        self.assertTrue(Path(current['code']).is_relative_to(self.root / '.runtime/releases'))
        self.assertEqual(self.digest(), before)

    def test_password_reader_allows_admin_and_retries_empty_or_mismatched_input_without_echoing_passwords(self):
        good = 'admin'
        output = StringIO()
        with patch.object(staging.getpass, 'getpass', side_effect=['', good, 'different-confirmation', good, good]) as reader, patch.object(staging.sys, 'stderr', output):
            self.assertEqual(staging.prompt_password(), good)
        self.assertEqual(reader.call_count, 5)
        self.assertIn('пустым', output.getvalue()); self.assertIn('не совпали', output.getvalue())
        for secret in [good, 'different-confirmation']:self.assertNotIn(secret, output.getvalue())
        self.assertFalse((self.root / 'data').exists())

    def test_password_reader_retries_oversized_input_and_handles_cancel(self):
        good = 'admin'
        with patch.object(staging.getpass, 'getpass', side_effect=['x' * 4097, good, good]), patch.object(staging.sys, 'stderr', StringIO()):
            self.assertEqual(staging.prompt_password(), good)
        with patch.object(staging.getpass, 'getpass', side_effect=EOFError):
            with self.assertRaisesMessage(ValueError, 'Ввод пароля отменён'):staging.prompt_password()
        self.assertFalse((self.root / 'data').exists())

    def test_invalid_password_never_starts_copy_and_cli_prepares_once_after_correction(self):
        before = self.digest()
        with patch.object(staging, 'snapshot') as copy:
            for invalid in ['', 'x' * 4097]:
                with self.assertRaises(ValueError):
                    staging.prepare(self.root, self.prod, 'owner', invalid, revision='a' * 40)
            copy.assert_not_called()
        good = 'admin'
        arguments=['staging.py','prepare','--root',str(self.root),'--source-root',str(self.prod),'--owner-login','owner','--revision','a'*40]
        state={'hosts':['127.0.0.1'],'port':8002,'owner_login':'owner'}
        output = StringIO()
        with patch.object(staging.sys, 'argv', arguments), patch.object(staging.getpass, 'getpass', side_effect=['', good, good]), patch.object(staging, 'prepare', return_value=state) as prepare, patch.object(staging.sys, 'stderr', output), patch.object(staging.sys, 'stdout', output):
            staging.main()
        prepare.assert_called_once()
        self.assertEqual(prepare.call_args.args[3], good)
        self.assertNotIn(good, output.getvalue()); self.assertNotIn('short', output.getvalue())
        self.assertEqual(self.digest(), before)
