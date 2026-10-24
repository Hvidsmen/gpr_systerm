from contextlib import ExitStack
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from django.test import TestCase,Client,override_settings
from django.urls import reverse
from django.core.exceptions import ValidationError,PermissionDenied
from ops.windows_runtime import atomic_json,singleton
from .models import Company,User,Role
from .portal_updates import enqueue,state


class PortalUpdateTests(TestCase):
    def setUp(self):
        self.company=Company.objects.create(name='Portal updates')
        role,_=Role.objects.get_or_create(code='ADMIN',defaults={'name':'Admin'})
        self.user=User.objects.create_user(username='update-admin',company=self.company,role=role)
        self.client.force_login(self.user)
        self.stack=ExitStack();self.addCleanup(self.stack.close)
        root=self.stack.enter_context(TemporaryDirectory())
        self.runtime=Path(root)/'.runtime';self.runtime.mkdir()
        self.stack.enter_context(override_settings(CONFIG_DIR=Path(root)))
        self.stack.enter_context(patch('apps.accounts.portal_updates.is_windows',return_value=True))
        atomic_json(self.runtime/'installed.json',{'port':8001})
        atomic_json(self.runtime/'current.json',{'sha':'a'*40})
        atomic_json(self.runtime/'status.json',{'state':'running','sha':'a'*40})
        self.stack.enter_context(singleton(self.runtime/'supervisor.lock'))
        self.url=reverse('accounts:portal_updates')
        self.status_url=reverse('accounts:portal_update_status')

    def test_browser_enqueues_without_waiting_and_records_actor(self):
        response=self.client.get(self.url)
        self.assertContains(response,'Обновить приложение')
        self.assertContains(response,'data-update-progress')
        self.assertContains(response,'portal-update-notice')
        response=self.client.post(self.url)
        self.assertRedirects(response,self.url)
        import json
        metadata=json.loads((self.runtime/'web-update.json').read_text())
        request=json.loads((self.runtime/'update.request').read_text())
        self.assertEqual(request,{'id':metadata['id']})
        self.assertEqual(metadata['user_id'],self.user.pk)
        self.assertEqual(metadata['company_id'],self.company.pk)
        self.assertEqual(state(self.company.pk)['state'],'queued')
        with self.assertRaisesMessage(ValueError,'уже запущено'):enqueue(self.user)

    def test_status_tracks_stages_then_success_without_exposing_paths(self):
        identifier=enqueue(self.user)
        (self.runtime/'update.request').unlink()
        atomic_json(self.runtime/'status.json',{'state':'updating','stage':'migrating'})
        response=self.client.get(self.status_url)
        self.assertEqual(response.json()['stage'],'Обновление структуры базы')
        self.assertEqual(response.json()['stage_number'],7)
        self.assertEqual(response.json()['stage_total'],10)
        self.assertEqual(response.json()['progress'],60)
        atomic_json(self.runtime/'results'/identifier,{'ok':True,'message':'Updated to bbbb. Database backup: C:/private/secret/path'})
        atomic_json(self.runtime/'current.json',{'sha':'b'*40})
        atomic_json(self.runtime/'status.json',{'state':'running'})
        response=self.client.get(self.status_url)
        self.assertEqual(response.json()['state'],'done')
        self.assertEqual(response.json()['progress'],100)
        self.assertIsNotNone(response.json()['finished_at'])
        self.assertEqual(response.json()['version'],'b'*40)
        self.assertNotContains(response,'private')
        self.assertEqual(response['Cache-Control'],'no-store')

    def test_update_failure_and_recovery_never_report_ready(self):
        identifier=enqueue(self.user);(self.runtime/'update.request').unlink()
        atomic_json(self.runtime/'results'/identifier,{'ok':False,'message':'Update failed. Previous version restored; see protected logs.'})
        self.assertEqual(state(self.company.pk)['state'],'error')
        atomic_json(self.runtime/'status.json',{'state':'recovery_required'})
        self.assertFalse(state(self.company.pk)['ready'])
        with self.assertRaises(ValueError):enqueue(self.user)

    def test_external_update_lock_and_audit_failure_do_not_create_request(self):
        with singleton(self.runtime/'update.lock'):
            with self.assertRaises(ValueError):enqueue(self.user)
        self.assertFalse((self.runtime/'update.request').exists())
        with patch('apps.audit.models.AuditLog.objects.create',side_effect=ValidationError('Audit failed')):
            with self.assertRaises(ValidationError):enqueue(self.user)
        self.assertFalse((self.runtime/'update.request').exists())

    def test_admin_only_and_csrf(self):
        role,_=Role.objects.get_or_create(code='PLANNER',defaults={'name':'Planner'})
        planner=User.objects.create_user(username='update-planner',company=self.company,role=role)
        self.client.force_login(planner)
        for url in [self.url,self.status_url]:
            self.assertEqual(self.client.get(url).status_code,403)
            self.assertEqual(self.client.post(url).status_code,403)
        with self.assertRaises(PermissionDenied):enqueue(planner)
        client=Client(enforce_csrf_checks=True);client.force_login(self.user)
        self.assertEqual(client.post(self.url).status_code,403)
        self.assertFalse((self.runtime/'update.request').exists())

    def test_non_windows_is_readonly_and_other_company_cannot_see_result(self):
        identifier=enqueue(self.user);(self.runtime/'update.request').unlink()
        atomic_json(self.runtime/'results'/identifier,{'ok':True,'message':'Already up to date.'})
        self.assertEqual(state(99999)['state'],'idle')
        with patch('apps.accounts.portal_updates.is_windows',return_value=False):
            self.assertFalse(state(self.company.pk)['ready'])
            with self.assertRaises(ValueError):enqueue(self.user)

    def test_legacy_command_cannot_queue_second_update_during_browser_update(self):
        from ops.windows_runtime import request_update
        atomic_json(self.runtime/'status.json',{'state':'updating'})
        with self.assertRaisesMessage(RuntimeError,'already queued or running'):
            request_update(self.runtime.parent)
        self.assertFalse((self.runtime/'update.request').exists())
