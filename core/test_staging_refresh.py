from contextlib import closing
import hashlib
from pathlib import Path
import sqlite3
import sys
from tempfile import TemporaryDirectory
from unittest.mock import patch
import uuid

from django.contrib.auth.hashers import make_password
from django.test import SimpleTestCase

from ops import staging, staging_refresh
from ops.windows_runtime import atomic_json, singleton


class StagingDataRefreshTests(SimpleTestCase):
    def setUp(self):
        temp=TemporaryDirectory();self.addCleanup(temp.cleanup)
        self.parent=Path(temp.name);self.root=self.parent/'test';self.source=self.parent/'production'
        (self.root/'.runtime').mkdir(parents=True);(self.root/'data/media').mkdir(parents=True)
        (self.root/'data/private_uploads/actuation').mkdir(parents=True)
        (self.source/'media').mkdir(parents=True);(self.source/'actuation').mkdir()
        self.hash=make_password('admin')
        for path,owner,company,password,value in [(self.root/'data/db.sqlite3',99,20,self.hash,'test-only'),
                (self.source/'live.sqlite3',1,10,'synthetic-production-hash','production')]:
            with closing(sqlite3.connect(path)) as db:
                db.execute('CREATE TABLE accounts_user (id INTEGER,company_id INTEGER,username TEXT,ldap_login TEXT,is_active INTEGER,archived_at TEXT,password TEXT)')
                db.execute('INSERT INTO accounts_user VALUES (?,?,?,?,?,?,?)',[owner,company,'admin','',1,None,password])
                db.execute('CREATE TABLE marker(value TEXT)');db.execute('INSERT INTO marker VALUES (?)',[value]);db.commit()
        (self.root/'data/media/doc.txt').write_text('test document')
        (self.root/'data/private_uploads/actuation/act.txt').write_text('test act')
        (self.source/'media/doc.txt').write_text('production document')
        (self.source/'actuation/act.txt').write_text('production act')
        (self.root/'secret.key').write_text('synthetic-test-secret')
        self.state=dict(kind=staging.KIND,instance=str(uuid.uuid4()),owner_id=99,company_id=20,owner_login='admin',
            port=8002,hosts=['127.0.0.1'],revision='a'*40,code=str(self.root),python=sys.executable,static='staticfiles',
            source={'database':str(self.source/'live.sqlite3'),'media':str(self.source/'media'),'actuation':str(self.source/'actuation')})
        atomic_json(self.root/'staging.json',self.state)
        self.before=self.source_digest()

    def source_digest(self):
        return {str(p.relative_to(self.source)):hashlib.sha256(p.read_bytes()).hexdigest()
                for p in self.source.rglob('*') if p.is_file()}

    def manage(self,root,command,**kwargs):
        if command[0]=='prepare_staging_copy':
            self.assertEqual(command,['prepare_staging_copy','--owner-password-hash'])
            self.assertEqual(kwargs['password'],self.hash)
            self.assertEqual(kwargs['code'],self.root)
            with closing(sqlite3.connect(root/'data/db.sqlite3')) as db:
                db.execute('UPDATE accounts_user SET password=?',[kwargs['password']]);db.commit()

    def refresh(self,**kwargs):
        with patch.object(staging,'run_manage',side_effect=self.manage):
            return staging_refresh.refresh(self.root,expected_generation=self.state['instance'],
                expected_revision=self.state['revision'],owner_id=99,**kwargs)

    def assert_old_data(self):
        with closing(sqlite3.connect(self.root/'data/db.sqlite3')) as db:
            self.assertEqual(db.execute('SELECT value FROM marker').fetchone()[0],'test-only')
        self.assertEqual((self.root/'data/media/doc.txt').read_text(),'test document')
        self.assertEqual((self.root/'data/private_uploads/actuation/act.txt').read_text(),'test act')
        self.assertEqual(staging.manifest(self.root),self.state)
        self.assertEqual(self.source_digest(),self.before)

    def test_refresh_copies_source_preserves_test_hash_code_and_identity_and_backs_up_all_old_data(self):
        result=self.refresh()
        self.assertEqual((result['owner_id'],result['company_id']),(1,10))
        for key in ['instance','revision','code','python','static']:self.assertEqual(result[key],self.state[key])
        self.assertNotEqual(result['data_generation'],self.state['instance'])
        with closing(sqlite3.connect(self.root/'data/db.sqlite3')) as db:
            self.assertEqual(db.execute('SELECT value FROM marker').fetchone()[0],'production')
            self.assertEqual(db.execute('SELECT password FROM accounts_user').fetchone()[0],self.hash)
        self.assertEqual((self.root/'data/media/doc.txt').read_text(),'production document')
        self.assertEqual((self.root/'data/private_uploads/actuation/act.txt').read_text(),'production act')
        backup=next((self.root/'.runtime/backups').iterdir())
        self.assertTrue((backup/'database.sqlite3').is_file())
        self.assertEqual((backup/'data/media/doc.txt').read_text(),'test document')
        self.assertEqual((backup/'data/private_uploads/actuation/act.txt').read_text(),'test act')
        self.assertEqual((backup/'secret.key').read_text(),(self.root/'secret.key').read_text())
        self.assertEqual(self.source_digest(),self.before)
        self.assertFalse((self.root/'.runtime/staging-refresh-transaction.json').exists())

    def test_unavailable_source_or_copy_failure_keeps_old_database_documents_and_config(self):
        with patch.object(staging,'copy_files',side_effect=OSError('synthetic unreadable source')):
            with self.assertRaises(OSError):self.refresh()
        self.assert_old_data()

    def test_ambiguous_or_missing_source_owner_never_publishes_copy(self):
        with closing(sqlite3.connect(self.source/'live.sqlite3')) as db:
            db.execute('DELETE FROM accounts_user');db.commit()
        self.before=self.source_digest()
        with self.assertRaises(ValueError):self.refresh()
        self.assert_old_data()

    def test_failed_pending_migration_keeps_all_existing_test_data(self):
        with patch.object(staging,'run_manage',side_effect=RuntimeError('synthetic pending migration failure')):
            with self.assertRaises(RuntimeError):
                staging_refresh.refresh(self.root,expected_generation=self.state['instance'],
                    expected_revision='a'*40,owner_id=99)
        self.assert_old_data()

    def test_publish_failure_rolls_back_database_documents_and_manifest_together(self):
        real_atomic=atomic_json
        def publish(path,data):
            if Path(path)==self.root/'staging.json' and data.get('data_generation'):
                raise OSError('synthetic failed config publish')
            return real_atomic(path,data)
        with patch.object(staging_refresh,'atomic_json',side_effect=publish):
            with self.assertRaises(OSError):self.refresh()
        self.assert_old_data()
        self.assertFalse((self.root/'.runtime/staging-refresh-transaction.json').exists())

    def test_failed_restore_marks_recovery_and_preserves_backup_and_pending_files(self):
        real_atomic=atomic_json;rename=Path.rename
        def publish(path,data):
            if Path(path)==self.root/'staging.json' and data.get('data_generation'):raise OSError('synthetic publish failure')
            return real_atomic(path,data)
        def move(path,target):
            if '.runtime/backups' in str(path) and path.name=='data':raise OSError('synthetic restore failure')
            return rename(path,target)
        with patch.object(staging_refresh,'atomic_json',side_effect=publish),patch.object(Path,'rename',move):
            with self.assertRaises(OSError):self.refresh()
        self.assertTrue((self.root/'.runtime/staging-recovery.json').exists())
        self.assertTrue((self.root/'.runtime/staging-refresh-transaction.json').exists())
        self.assertTrue(list((self.root/'.runtime/backups').glob('*/data/media/doc.txt')))
        self.assertEqual(self.source_digest(),self.before)

    def test_live_http_lock_and_stale_confirmation_never_start_copying(self):
        with singleton(self.root/'.runtime/server.lock'),patch.object(staging,'snapshot') as copy:
            with self.assertRaises(OSError):self.refresh()
            copy.assert_not_called()
        with patch.object(staging,'snapshot') as copy:
            with self.assertRaises(ValueError):
                staging_refresh.refresh(self.root,expected_generation='old',expected_revision='a'*40,owner_id=99)
            copy.assert_not_called()
        self.assert_old_data()

    def test_unusable_owner_hash_cannot_be_replaced_with_a_production_password(self):
        with closing(sqlite3.connect(self.root/'data/db.sqlite3')) as db:
            db.execute('UPDATE accounts_user SET password=?',['!unusable']);db.commit()
        with patch.object(staging,'snapshot') as copy:
            with self.assertRaises(ValueError):self.refresh()
            copy.assert_not_called()
        self.assert_old_data()
