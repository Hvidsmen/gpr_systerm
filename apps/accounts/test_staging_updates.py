import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from unittest.mock import patch
import uuid

from django.conf import settings
from django.core import signing
from django.test import TestCase, Client, override_settings
from django.urls import reverse

from apps.accounts.models import Company, Role, User
from apps.accounts.staging_updates import SALT, REFRESH_SALT
from ops import staging, staging_updates
from ops.windows_runtime import atomic_json, singleton


class StagingUpdateViewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.company = Company.objects.create(name='Test updater')
        cls.owner = User.objects.create_user(username='admin', company=cls.company, role=Role.objects.get(code='ADMIN'))
        cls.other = User.objects.create_user(username='other-admin', company=cls.company, role=Role.objects.get(code='ADMIN'))

    def setUp(self):
        temp = TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.root = Path(temp.name) / 'test'; (self.root / '.runtime').mkdir(parents=True)
        self.config = dict(kind=staging.KIND, instance=str(uuid.uuid4()), owner_id=self.owner.pk,
            company_id=self.company.pk, owner_login='admin', port=8002, hosts=['127.0.0.1'], revision='a'*40,
            code=str(self.root), python=sys.executable, static='staticfiles', production_url='http://127.0.0.1:8001/',
            source={name: str(Path(temp.name)/'production'/name) for name in ['database','media','actuation']})
        atomic_json(self.root / 'staging.json', self.config)
        middleware=list(settings.MIDDLEWARE)
        middleware.insert(middleware.index('core.impersonation.ActAsMiddleware'),'apps.accounts.staging_access.StagingGuard')
        self.override = override_settings(STAGING_MODE=True, STAGING_CONFIG=self.config, CONFIG_DIR=self.root, MIDDLEWARE=middleware)
        self.override.enable(); self.addCleanup(self.override.disable)
        self.client.force_login(self.owner)
        self.url = reverse('accounts:portal_updates')
        self.status_url = reverse('accounts:portal_update_status')

    def latest(self):
        atomic_json(self.root / '.runtime/staging-latest.json', dict(revision='b'*40,
            from_revision='a'*40, branch=staging_updates.BRANCH, checked_at=staging_updates.now()))

    def refresh_ready(self):
        atomic_json(self.root / '.runtime/staging-supervisor.json', {'features':['refresh-data']})

    def test_refresh_requires_confirmation_and_uses_same_queue_as_code_updates(self):
        self.refresh_ready();self.latest()
        with singleton(self.root / '.runtime/staging-supervisor.lock'):
            response=self.client.get(self.url)
            self.assertContains(response,'Тестовая база, загруженные документы и акты будут заменены')
            token=response.context['refresh_token'];self.assertTrue(token)
            self.client.post(self.url,{'action':'refresh','refresh_token':token})
            self.assertFalse((self.root/'.runtime/staging-update.json').exists())
            self.client.post(self.url,{'action':'refresh','refresh_token':token,'confirm_replace':'yes'})
            job=json.loads((self.root/'.runtime/staging-update.json').read_text())
            self.assertEqual(job['kind'],'refresh')
            self.assertEqual(job['data_generation'],self.config['instance'])
            self.client.post(self.url,{'action':'refresh','refresh_token':token,'confirm_replace':'yes'})
            self.client.post(self.url,{'action':'update','version_token':response.context['version_token']})
            self.assertEqual(json.loads((self.root/'.runtime/staging-update.json').read_text())['id'],job['id'])
        self.assertFalse((self.root/'.runtime/update.request').exists())

    def test_refresh_rejects_tampered_wrong_instance_and_replayed_generation(self):
        self.refresh_ready()
        with singleton(self.root / '.runtime/staging-supervisor.lock'):
            token=self.client.get(self.url).context['refresh_token']
            foreign=signing.dumps({'instance':str(uuid.uuid4()),'revision':'a'*40,'data_generation':self.config['instance']},salt=REFRESH_SALT)
            with patch('django.core.signing.time.time',return_value=0):
                expired=signing.dumps({'instance':self.config['instance'],'revision':'a'*40,'data_generation':self.config['instance']},salt=REFRESH_SALT)
            for bad in [token+'broken','',foreign,expired]:
                self.client.post(self.url,{'action':'refresh','refresh_token':bad,'confirm_replace':'yes'})
                self.assertFalse((self.root/'.runtime/staging-update.json').exists())
            self.config['data_generation']='new-data';atomic_json(self.root/'staging.json',self.config)
            self.client.post(self.url,{'action':'refresh','refresh_token':token,'confirm_replace':'yes'})
            self.assertFalse((self.root/'.runtime/staging-update.json').exists())

    def test_old_supervisor_needs_restart_before_accepting_refresh(self):
        with singleton(self.root / '.runtime/staging-supervisor.lock'):
            response=self.client.get(self.url)
            self.assertFalse(response.context['update']['can_refresh'])
            self.assertContains(response,'один раз перезапустите задание')

    def test_page_and_status_use_only_test_state_and_never_production_enqueue(self):
        self.latest()
        with singleton(self.root / '.runtime/staging-supervisor.lock'), patch('apps.accounts.portal_updates.enqueue') as production:
            response = self.client.get(self.url)
            self.assertContains(response, 'Обновление тестового портала')
            self.assertNotContains(response, 'из ветки main')
            self.assertNotContains(response, 'portal-update-notice')
            self.assertTrue(response.context['update']['can_update'])
            status = self.client.get(self.status_url).json()
            self.assertEqual(status['version'], 'a'*40)
            self.assertEqual(status['available'], 'b'*40)
            self.assertEqual(status['branch'], staging_updates.BRANCH)
            token = response.context['version_token']
            response = self.client.post(self.url, {'action':'update', 'version_token':token})
            self.assertEqual(response.status_code, 302)
            job = json.loads((self.root / '.runtime/staging-update.json').read_text())
            self.assertEqual(job['revision'], 'b'*40)
            self.assertEqual(job['owner_id'], self.owner.pk)
            self.client.post(self.url, {'action':'update', 'version_token':token})
            self.assertEqual(json.loads((self.root / '.runtime/staging-update.json').read_text())['id'], job['id'])
            production.assert_not_called()
        self.assertFalse((self.root / '.runtime/update.request').exists())

    def test_nonowner_and_unauthenticated_requests_cannot_read_or_start_update(self):
        self.client.force_login(self.other)
        for url in [self.url,self.status_url]:
            self.assertEqual(self.client.get(url).status_code, 403)
        self.assertEqual(self.client.post(self.url, {'action':'check'}).status_code, 403)
        self.client.logout()
        self.assertEqual(self.client.get(self.url).status_code, 302)
        self.assertFalse((self.root / '.runtime/staging-update.json').exists())

    def test_tampered_expired_cross_instance_and_stale_tokens_are_rejected(self):
        self.latest()
        with singleton(self.root / '.runtime/staging-supervisor.lock'):
            token = self.client.get(self.url).context['version_token']
            other = signing.dumps(dict(instance=str(uuid.uuid4()), revision='b'*40,from_revision='a'*40),salt=SALT)
            for bad in [token+'broken',other,'']:
                self.client.post(self.url, {'action':'update','version_token':bad})
                self.assertFalse((self.root / '.runtime/staging-update.json').exists())
            with patch('django.core.signing.time.time', return_value=0):
                expired = signing.dumps(dict(instance=self.config['instance'], revision='b'*40,from_revision='a'*40),salt=SALT)
            self.client.post(self.url, {'action':'update','version_token':expired})
            self.assertFalse((self.root / '.runtime/staging-update.json').exists())
            self.config['revision']='c'*40; atomic_json(self.root / 'staging.json',self.config)
            self.client.post(self.url, {'action':'update','version_token':token})
            self.assertFalse((self.root / '.runtime/staging-update.json').exists())

    def test_check_failure_is_visible_and_never_queues_an_update(self):
        with patch.object(staging_updates, 'check_release', side_effect=OSError('synthetic offline')):
            response=self.client.post(self.url, {'action':'check'},follow=True)
        self.assertContains(response,'Не удалось связаться с GitHub')
        self.assertFalse((self.root / '.runtime/staging-update.json').exists())

    def test_csrf_is_required_and_get_never_checks_github(self):
        client=Client(enforce_csrf_checks=True);client.force_login(self.owner)
        self.assertEqual(client.post(self.url, {'action':'check'}).status_code,403)
        self.assertEqual(client.post(self.url, {'action':'refresh','confirm_replace':'yes'}).status_code,403)
        with patch.object(staging_updates,'check_release') as check:
            self.assertEqual(self.client.get(self.url).status_code,200)
            check.assert_not_called()

    def test_production_dispatch_keeps_existing_page_and_update_path(self):
        middleware=[name for name in settings.MIDDLEWARE if name!='apps.accounts.staging_access.StagingGuard']
        with override_settings(STAGING_MODE=False,MIDDLEWARE=middleware), patch('apps.accounts.portal_updates.state', return_value={}), patch('apps.accounts.portal_updates.enqueue') as enqueue:
            response=self.client.get(self.url)
            self.assertContains(response,'из ветки main')
            self.client.post(self.url)
            enqueue.assert_called_once_with(self.owner)
