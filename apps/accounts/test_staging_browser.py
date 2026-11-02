import json
import os
from pathlib import Path
import shutil
import socket
import sqlite3
import subprocess
import sys
from tempfile import TemporaryDirectory
import time
import zipfile
from unittest import skipUnless
from unittest.mock import patch
from urllib.request import build_opener, ProxyHandler
from django.conf import settings
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.db import connection
from django.urls import reverse
from .models import Company, User, Role, OneCConnection, CompanyMailSettings, TrainingCopy
from .invitation_mail import encrypt_password
from ops import staging
from ops.windows_runtime import atomic_json


@skipUnless(os.environ.get('STAGING_BROWSER_TESTS') == '1', 'Optional two independent portal processes and Chromium')
class StagingBrowserTests(StaticLiveServerTestCase):
    serialized_rollback = True

    def test_real_snapshot_login_cookie_isolation_impersonation_and_source_preservation(self):
        if connection.settings_dict['NAME'] == ':memory:' or 'mode=memory' in str(connection.settings_dict['NAME']):
            self.fail('Use a dedicated disposable file-backed TEST database for this browser scenario.')
        company = Company.objects.create(name='Acceptance source')
        owner = User.objects.create_user(username='acceptance.owner', company=company, role=Role.objects.get(code='ADMIN'), password='synthetic-production-password')
        other = User.objects.create_user(username='acceptance.other', company=company, role=Role.objects.get(code='DISPATCHER'), password='synthetic-other-password')
        OneCConnection.objects.create(company=company, server='synthetic-source', database='synthetic-db')
        credential = encrypt_password(company.pk, 'synthetic-source-mail-password')
        CompanyMailSettings.objects.create(company=company, host='synthetic-smtp', password_encrypted=credential)
        TrainingCopy.objects.create(company=company, name='Synthetic existing training', port=8101)
        self.client.force_login(owner)
        production_hash = owner.password
        with TemporaryDirectory() as temp:
            root = Path(temp); prod = root / 'source'; (prod / '.runtime').mkdir(parents=True)
            media = prod / 'media'; media.mkdir(); (media / 'fixture.txt').write_text('original document')
            code = root / 'test'
            shutil.copytree(settings.BASE_DIR, code, ignore=shutil.ignore_patterns('.git', '.venv', '__pycache__', '.runtime', 'staticfiles', 'db.sqlite3', '.env', '.dev-secret-key'))
            with socket.socket() as sock:sock.bind(('127.0.0.1', 0)); port = sock.getsockname()[1]
            source_port = int(self.live_server_url.rsplit(':', 1)[1])
            atomic_json(prod / '.runtime/installed.json', {'database': str(connection.settings_dict['NAME']), 'media': str(media), 'port': source_port, 'hosts': ['127.0.0.1']})
            with patch.object(staging, 'CODE_ROOT', code):
                state = staging.prepare(code, prod, owner.username, 'synthetic-acceptance-password', port=port, hosts=['127.0.0.1'], revision='a' * 40)
            with sqlite3.connect(code / 'data/db.sqlite3') as db:
                self.assertEqual(db.execute('SELECT COUNT(*) FROM accounts_onecconnection').fetchone()[0], 0)
                self.assertEqual(db.execute('SELECT COUNT(*) FROM django_session').fetchone()[0], 0)
                self.assertEqual(db.execute('SELECT password_encrypted FROM accounts_companymailsettings WHERE company_id=?', [company.pk]).fetchone()[0], '')
                self.assertEqual(db.execute('SELECT enabled FROM accounts_trainingcopy').fetchone()[0], 0)
            server_log = open(root / 'test-process.log', 'wb')
            process = subprocess.Popen([sys.executable, str(code / 'ops/staging.py'), 'serve', '--root', str(code)],
                                       env=staging.environment(code), stdout=server_log, stderr=subprocess.STDOUT)
            try:
                base = 'http://127.0.0.1:' + str(port)
                opener = build_opener(ProxyHandler({}))
                for _ in range(100):
                    if process.poll() is not None:self.fail((root / 'test-process.log').read_text())
                    try:
                        with opener.open(base + reverse('accounts:login'), timeout=1) as response:
                            if response.status == 200:break
                    except Exception:time.sleep(.2)
                else:self.fail('Staging login did not become available')
                config = dict(base=base, source=self.live_server_url, source_cookie=self.client.cookies['sessionid'].value,
                    chromium=shutil.which('chromium'), login=owner.username, other=other.username,
                    password='synthetic-acceptance-password', other_password='synthetic-other-password',
                    settings=reverse('accounts:settings'), login_path=reverse('accounts:login'),
                    impersonate=reverse('accounts:impersonation_start', args=[other.pk]), stop=reverse('accounts:impersonation_stop'),
                    blocked=[reverse('accounts:invitation_settings'), reverse('accounts:portal_updates'), reverse('accounts:training_list')])
                result = subprocess.run(['node', str(Path(settings.BASE_DIR) / 'tests/browser/staging_portal.js')],
                    capture_output=True, text=True, env=dict(os.environ, STAGING_CONFIG=json.dumps(config)), timeout=100)
                self.assertEqual(result.returncode, 0, result.stdout + '\n' + result.stderr)
                print(result.stdout.strip())
            finally:
                (code / '.runtime/server.stop').touch()
                try:process.wait(timeout=115)
                except subprocess.TimeoutExpired:process.kill();process.wait(timeout=10)
                server_log.close()
            if os.environ.get('STAGING_RELEASE_TESTS') == '1':
                # An actual update on the disposable test installation: own venv,
                # migrations, static files, pinned pointer and delegated HTTP child.
                with sqlite3.connect(code / 'data/db.sqlite3') as db:
                    db.execute('UPDATE accounts_company SET name=? WHERE id=?', ['Only acceptance changed', company.pk])
                    test_hash = db.execute('SELECT password FROM accounts_user WHERE id=?', [owner.pk]).fetchone()[0]
                archive = root / 'candidate.zip'
                with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as zipped:
                    for item in Path(settings.BASE_DIR).rglob('*'):
                        relative = item.relative_to(settings.BASE_DIR)
                        if any(part in {'.git', '.venv', '__pycache__', '.runtime', 'staticfiles', 'data'} for part in relative.parts):continue
                        if item.is_file() and item.name not in {'.env', '.dev-secret-key', 'secret.key', 'staging.json'}:
                            zipped.write(item, 'gpr_systerm-' + 'b' * 40 + '/' + relative.as_posix())
                staging.update(code, archive, 'b' * 40)
                current = staging.manifest(code)
                self.assertEqual(current['revision'], 'b' * 40)
                self.assertEqual(current['instance'], state['instance'])
                with sqlite3.connect(code / 'data/db.sqlite3') as db:
                    self.assertEqual(db.execute('SELECT name FROM accounts_company WHERE id=?', [company.pk]).fetchone()[0], 'Only acceptance changed')
                    self.assertEqual(db.execute('SELECT password FROM accounts_user WHERE id=?', [owner.pk]).fetchone()[0], test_hash)
                with open(root / 'updated-test-process.log', 'wb') as output:
                    process = subprocess.Popen([sys.executable, str(code / 'ops/staging.py'), 'serve', '--root', str(code)], env=staging.environment(code), stdout=output, stderr=subprocess.STDOUT)
                    try:
                        for _ in range(100):
                            if process.poll() is not None:self.fail((root / 'updated-test-process.log').read_text())
                            try:
                                with opener.open(base + reverse('accounts:login'), timeout=1) as response:
                                    if response.headers.get('X-Portal-Version') == 'b' * 12:break
                            except Exception:time.sleep(.2)
                        else:self.fail('Updated staging release did not start')
                        # Loss of the launcher must not leave its HTTP child orphaned.
                        process.terminate();process.wait(timeout=10)
                        for _ in range(100):
                            try:
                                with opener.open(base + reverse('accounts:login'), timeout=.5):pass
                            except Exception:break
                            time.sleep(.1)
                        else:self.fail('Delegated staging HTTP worker survived its launcher')
                    finally:
                        (code / '.runtime/server.stop').touch()
                        if process.poll() is None:process.kill();process.wait(timeout=10)
                print('PASS: actual pinned test release, fresh dependencies, migrations, data/password/cookie identity retained, delegated worker and parent-loss shutdown')
            owner.refresh_from_db(); other.refresh_from_db()
            self.assertEqual(owner.password, production_hash)
            self.assertTrue(other.check_password('synthetic-other-password'))
            self.assertEqual(CompanyMailSettings.objects.get(company=company).password_encrypted, credential)
            self.assertEqual(OneCConnection.objects.get(company=company).server, 'synthetic-source')
            self.assertTrue(TrainingCopy.objects.get(company=company).enabled)
            self.assertEqual((media / 'fixture.txt').read_text(), 'original document')
            self.assertFalse((prod / 'staging.json').exists())
