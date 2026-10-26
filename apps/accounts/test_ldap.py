from uuid import uuid4
from unittest.mock import patch,MagicMock
from django.test import TestCase,RequestFactory,override_settings
from django.contrib.auth import authenticate
from django.urls import reverse
from .models import User,Company,Role
from .backends import normalize_login,directory_identity


@override_settings(LDAP_ENABLED=True,LDAP_BIND_PASSWORD='test-service-secret')
class DirectoryTests(TestCase):
    def setUp(self):
        self.request=RequestFactory().post('/accounts/login/',{'auth_source':'ldap'})
        self.identity=dict(guid=uuid4(),first_name='Иван',last_name='Иванов',email='ivan@example.org')
        self.local=User.objects.create_user(username='ivan',password='Local-secret-123!')

    def test_login_formats_and_foreign_domain(self):
        for value in ['ivan','ivan@yamalgazprom.local','YAMALGAZPROM\\ivan']:
            self.assertEqual(normalize_login(value),'ivan')
        for value in ['other\\ivan','ivan@foreign.local','', 'a\x00b']:
            self.assertIsNone(normalize_login(value))

    @patch('apps.accounts.backends.directory_identity')
    def test_new_identity_has_no_privileges_and_never_links_local_names(self,lookup):
        lookup.return_value=self.identity
        user=authenticate(self.request,username='ivan',password='AD-password')
        self.assertEqual(user.auth_source,'LDAP');self.assertNotEqual(user.pk,self.local.pk)
        self.assertIsNone(user.company_id);self.assertIsNone(user.role_id)
        self.assertFalse(user.is_superuser);self.assertFalse(user.is_staff)
        self.assertFalse(user.has_usable_password())
        again=authenticate(self.request,username='renamed',password='AD-password')
        self.assertEqual(again.pk,user.pk)
        self.local.refresh_from_db();self.assertTrue(self.local.check_password('Local-secret-123!'))

    @patch('apps.accounts.backends.directory_identity',return_value=None)
    def test_wrong_domain_password_does_not_fall_back_to_local(self,lookup):
        self.assertIsNone(authenticate(self.request,username='ivan',password='Local-secret-123!'))
        self.assertEqual(User.objects.count(),1)
        self.assertEqual(authenticate(None,username='ivan',password='Local-secret-123!').pk,self.local.pk)

    @patch('apps.accounts.backends.directory_identity')
    def test_local_assignments_and_disabled_account_preserved(self,lookup):
        lookup.return_value=self.identity
        user=authenticate(self.request,username='ivan',password='AD-password')
        company=Company.objects.create(name='Assigned')
        user.company=company;user.role=Role.objects.get(code='PLANNER');user.save()
        again=authenticate(self.request,username='ivan',password='AD-password')
        self.assertEqual(again.company_id,company.pk);self.assertEqual(again.role.code,'PLANNER')
        user.is_active=False;user.save()
        self.assertIsNone(authenticate(self.request,username='ivan',password='AD-password'))

    @patch('apps.accounts.backends.directory_identity')
    def test_pending_page_and_domain_password_change(self,lookup):
        lookup.return_value=self.identity
        response=self.client.post(reverse('accounts:login'),{'username':'ivan','password':'AD-password','auth_source':'ldap'})
        self.assertRedirects(response,reverse('accounts:access_pending'))
        self.assertRedirects(self.client.get('/planning/'),reverse('accounts:access_pending'))
        user=User.objects.get(auth_source='LDAP')
        self.client.post(reverse('accounts:password_change'),{'old_password':'AD-password','new_password1':'Local-new-123!','new_password2':'Local-new-123!'})
        user.refresh_from_db();self.assertFalse(user.has_usable_password())

    @patch('apps.accounts.backends.directory_identity')
    def test_empty_or_disabled_configuration_never_binds(self,lookup):
        self.assertIsNone(authenticate(self.request,username='ivan',password=''))
        with override_settings(LDAP_ENABLED=False):self.assertIsNone(authenticate(self.request,username='ivan',password='AD-password'))
        with override_settings(LDAP_BIND_PASSWORD=''):self.assertIsNone(authenticate(self.request,username='ivan',password='AD-password'))
        lookup.assert_not_called()

    @override_settings(LDAP_SERVERS=['192.168.70.12','192.168.70.13'])
    def test_server_failover_filter_escaping_and_bind_cleanup(self):
        import ldap3
        from ldap3.core.exceptions import LDAPSocketOpenError
        service=MagicMock();entry=MagicMock();entry.entry_dn='CN=Ivan,OU=Yamaldorstroy,DC=yamalgazprom,DC=local'
        entry.__getitem__.side_effect=lambda key:MagicMock(raw_values=[self.identity['guid'].bytes_le],value=self.identity.get({'givenName':'first_name','sn':'last_name','mail':'email'}.get(key,''),''))
        service.entries=[entry];user_connection=MagicMock()
        with patch('ldap3.Server') as server,patch('ldap3.Connection',side_effect=[LDAPSocketOpenError(),service,user_connection]) as conn:
            identity=directory_identity('a*)(x','AD-password')
        self.assertEqual(identity['guid'],self.identity['guid'])
        self.assertEqual(server.call_count,2)
        self.assertFalse(server.call_args.kwargs['use_ssl'])
        search=service.search.call_args.args[1]
        self.assertIn(r'a\2a\29\28x',search)
        self.assertFalse(conn.call_args.kwargs['auto_referrals'])
        service.unbind.assert_called_once();user_connection.unbind.assert_called_once()

    def test_invalid_directory_password_is_rejected_and_service_unbound(self):
        from ldap3.core.exceptions import LDAPInvalidCredentialsResult
        service=MagicMock();entry=MagicMock();entry.entry_dn='CN=Ivan,DC=yamalgazprom,DC=local';service.entries=[entry]
        with patch('ldap3.Server') as server,patch('ldap3.Connection',side_effect=[service,LDAPInvalidCredentialsResult()]):
            self.assertIsNone(directory_identity('ivan','wrong-password'))
        self.assertEqual(server.call_count,1)
        service.unbind.assert_called_once()


class LoginChoiceTests(TestCase):
    @override_settings(LDAP_ENABLED=True)
    def test_ad_option_is_rendered_and_valid_in_each_form(self):
        from .forms import UserLoginForm
        for _ in range(2):
            form = UserLoginForm()
            self.assertIn('value="ldap" selected', str(form['auth_source']))
            self.assertEqual(form['auth_source'].value(), 'ldap')
            self.assertEqual(list(form.fields['auth_source'].choices), list(form.fields['auth_source'].widget.choices))
        self.assertContains(self.client.get(reverse('accounts:login')), 'value="ldap"')

    @override_settings(LDAP_ENABLED=False)
    def test_disabled_directory_remains_hidden(self):
        from .forms import UserLoginForm
        self.assertTrue(UserLoginForm().fields['auth_source'].widget.is_hidden)
        self.assertEqual(UserLoginForm()['auth_source'].value(), 'local')
        self.assertNotContains(self.client.get(reverse('accounts:login')), 'value="ldap"')

    @override_settings(LDAP_ENABLED=True)
    def test_explicit_local_choice_is_preserved_when_login_fails(self):
        response = self.client.post(reverse('accounts:login'), {
            'username':'missing-local-user', 'password':'not-a-real-password', 'auth_source':'local'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['form']['auth_source'].value(), 'local')
        self.assertNotContains(response, 'not-a-real-password')
