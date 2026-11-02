from types import SimpleNamespace
from unittest.mock import patch
from django.conf import settings
from django.core.management import call_command
from django.core.management.base import CommandError
from django.http import HttpResponse
from django.test import TestCase, override_settings, RequestFactory
from django.urls import reverse
from .models import Company, User, Role
from .staging_access import OwnerBackend, StagingGuard


class StagingAccessTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.company = Company.objects.create(name='Staging tests')
        cls.owner = User.objects.create_user(username='owner-guid', company=cls.company, role=Role.objects.get(code='ADMIN'), password='synthetic-owner-password')
        cls.other = User.objects.create_user(username='other', company=cls.company, role=Role.objects.get(code='DISPATCHER'), password='synthetic-other-password')

    def setUp(self):
        self.state = dict(owner_id=self.owner.pk, company_id=self.company.pk, owner_login='owner', revision='a' * 40,
                          production_url='http://192.168.70.9:8001/')

    def test_even_other_valid_passwords_cannot_login_or_restore_a_session(self):
        with override_settings(STAGING_MODE=True, STAGING_CONFIG=self.state):
            backend = OwnerBackend()
            self.assertEqual(backend.authenticate(None, username='OWNER', password='synthetic-owner-password'), self.owner)
            self.assertIsNone(backend.authenticate(None, username='other', password='synthetic-other-password'))
            self.assertIsNone(backend.get_user(self.other.pk))
        self.assertIsNone(OwnerBackend().get_user(self.owner.pk))

    def test_guard_checks_real_owner_before_allowing_role_impersonation(self):
        request = RequestFactory().get('/'); request.user = self.owner
        def act_as(req):
            req.acting_admin = req.user; req.user = self.other
            return HttpResponse('synthetic role response')
        with override_settings(STAGING_CONFIG=self.state):
            response = StagingGuard(act_as)(request)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['X-Portal-Environment'], 'staging')
        request.user = self.other
        with override_settings(STAGING_CONFIG=self.state):self.assertEqual(StagingGuard(act_as)(request).status_code, 403)

    def test_real_updates_integrations_registration_and_training_are_blocked(self):
        guard = StagingGuard(lambda request: HttpResponse())
        for name in ['portal_updates', 'one_c', 'one_c_jobs', 'invitation_settings', 'user_invite', 'register', 'training_enter', 'training_consume']:
            request = SimpleNamespace(resolver_match=SimpleNamespace(namespaces=['accounts'], namespace='accounts', url_name=name))
            self.assertEqual(guard.process_view(request, None, (), {}).status_code, 403)
        for name in ['impersonation_start', 'impersonation_stop', 'login', 'logout', 'settings']:
            request = SimpleNamespace(resolver_match=SimpleNamespace(namespaces=['accounts'], namespace='accounts', url_name=name))
            self.assertIsNone(guard.process_view(request, None, (), {}))

    def test_preparation_is_forbidden_on_normal_portal(self):
        with self.assertRaises(CommandError):call_command('prepare_staging_copy')
