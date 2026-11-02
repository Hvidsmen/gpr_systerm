from django.template.loader import render_to_string
from django.test import TestCase, RequestFactory, override_settings
from apps.accounts.models import Company, User, Role


class StagingPortalLinkTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.company = Company.objects.create(name='Staging link tests')
        cls.owner = User.objects.create_user(username='owner-guid', company=cls.company, role=Role.objects.get(code='ADMIN'))
        cls.other = User.objects.create_user(username='other', company=cls.company, role=Role.objects.get(code='DISPATCHER'))

    @override_settings(STAGING_PORTAL_URL='http://192.168.70.9:8002/', STAGING_PORTAL_OWNER_LOGIN='owner-guid', STAGING_MODE=False, TRAINING_MODE=False)
    def test_production_owner_has_new_tab_link_and_other_users_modes_and_impersonation_do_not(self):
        from core.context_processors import staging_portal
        request=RequestFactory().get('/accounts/settings/');request.user=self.owner
        context=staging_portal(request)
        html=render_to_string('core/sidebar.html',{'request':request,'user':self.owner,**context})
        self.assertIn('href="http://192.168.70.9:8002/" target="_blank" rel="noopener noreferrer"',html)
        self.assertIn('Тестовый портал</a>',html)
        request.user=self.other
        self.assertEqual(staging_portal(request),{})
        self.other.role=self.owner.role;self.other.save(update_fields=['role'])
        self.assertEqual(staging_portal(request),{})
        request.user=self.owner;request.acting_admin=self.owner
        self.assertEqual(staging_portal(request),{})
        del request.acting_admin
        for mode in ['STAGING_MODE','TRAINING_MODE']:
            with override_settings(**{mode:True}):self.assertEqual(staging_portal(request),{})
        with override_settings(STAGING_PORTAL_URL=''):self.assertEqual(staging_portal(request),{})

    @override_settings(STAGING_PORTAL_URL='http://192.168.70.9:8002/', STAGING_PORTAL_OWNER_LOGIN='ADMIN', STAGING_MODE=False, TRAINING_MODE=False)
    def test_windows_login_resolves_guid_identity_and_ambiguous_owner_is_hidden(self):
        from core.context_processors import staging_portal
        self.owner.auth_source='LDAP';self.owner.ldap_login='admin'
        self.owner.save(update_fields=['auth_source','ldap_login'])
        request=RequestFactory().get('/');request.user=self.owner
        self.assertEqual(staging_portal(request),{'staging_portal_url':'http://192.168.70.9:8002/'})
        User.objects.create_user(username='admin',company=self.company,role=self.owner.role)
        self.assertEqual(staging_portal(request),{})
