from types import SimpleNamespace
from contextlib import closing
import hashlib
import sqlite3
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from django.conf import settings
from django.core.management import call_command
from django.core.management.base import CommandError
from django.http import HttpResponse
from django.test import TestCase, override_settings, RequestFactory
from django.urls import reverse
from .models import Company, User, Role
from .staging_access import OwnerBackend, StagingGuard
from django.contrib.auth.hashers import make_password


class StagingAccessTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.company = Company.objects.create(name='Staging tests')
        cls.owner = User.objects.create_user(username='owner-guid', company=cls.company, role=Role.objects.get(code='ADMIN'), password='synthetic-owner-password')
        cls.other = User.objects.create_user(username='other', company=cls.company, role=Role.objects.get(code='DISPATCHER'), password='synthetic-other-password')

    def setUp(self):
        self.state = dict(owner_id=self.owner.pk, company_id=self.company.pk, owner_login='owner', revision='a' * 40,
                          production_url='http://192.168.70.9:8001/')

    def source_admins(self, users):
        temp=TemporaryDirectory();self.addCleanup(temp.cleanup)
        path=Path(temp.name)/'production.sqlite3'
        with closing(sqlite3.connect(path)) as db:
            db.execute('CREATE TABLE accounts_role (id INTEGER,code TEXT)')
            db.execute("INSERT INTO accounts_role VALUES (1,'ADMIN'),(2,'DISPATCHER')")
            db.execute('''CREATE TABLE accounts_user (id INTEGER,company_id INTEGER,username TEXT,auth_source TEXT,
                ldap_login TEXT,ldap_guid TEXT,password TEXT,first_name TEXT,last_name TEXT,email TEXT,
                role_id INTEGER,is_active INTEGER,archived_at TEXT,is_superuser INTEGER)''')
            for user,password in users:
                db.execute('INSERT INTO accounts_user VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)',[
                    user.pk,user.company_id,user.username,user.auth_source,user.ldap_login,
                    user.ldap_guid.hex if user.ldap_guid else None,make_password(password),'','', '',
                    (1 if user.role.code=='ADMIN' else 2) if user.role_id else None,1,None,int(user.is_superuser)])
            db.commit()
        self.state['source']={'database':str(path)}
        return path

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
        for name in ['one_c', 'one_c_jobs', 'invitation_settings', 'user_invite', 'register', 'training_enter', 'training_consume']:
            request = SimpleNamespace(resolver_match=SimpleNamespace(namespaces=['accounts'], namespace='accounts', url_name=name))
            self.assertEqual(guard.process_view(request, None, (), {}).status_code, 403)
        for name in ['portal_updates', 'portal_update_status', 'impersonation_start', 'impersonation_stop', 'login', 'logout', 'settings']:
            request = SimpleNamespace(resolver_match=SimpleNamespace(namespaces=['accounts'], namespace='accounts', url_name=name))
            self.assertIsNone(guard.process_view(request, None, (), {}))

    def test_preparation_is_forbidden_on_normal_portal(self):
        with self.assertRaises(CommandError):call_command('prepare_staging_copy')

    def test_normal_portal_keeps_its_password_policy(self):
        from django.contrib.auth.password_validation import validate_password
        from django.core.exceptions import ValidationError
        self.assertTrue(settings.AUTH_PASSWORD_VALIDATORS)
        with self.assertRaises(ValidationError):validate_password('admin', self.owner)

    def test_second_administrator_uses_working_password_with_read_only_source_and_existing_session(self):
        admin=User.objects.create_user(username='another-admin',company=self.company,role=self.owner.role)
        source=self.source_admins([(admin,'working-password'),(self.other,'not-an-admin')])
        before=hashlib.sha256(source.read_bytes()).hexdigest()
        with override_settings(STAGING_MODE=True,STAGING_CONFIG=self.state):
            backend=OwnerBackend()
            self.assertIsNone(backend.authenticate(None,username=admin.username,password='wrong'))
            self.assertEqual(backend.authenticate(None,username=admin.username,password='working-password').pk,admin.pk)
            self.assertEqual(backend.get_user(admin.pk).pk,admin.pk)
            self.assertIsNone(backend.authenticate(None,username=self.other.username,password='not-an-admin'))
        self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(),before)
        admin.refresh_from_db();self.assertTrue(admin.check_password('working-password'))
        with closing(sqlite3.connect(source)) as db:
            db.execute('UPDATE accounts_user SET password=? WHERE id=?',[make_password('new-working-password'),admin.pk]);db.commit()
        current=source.read_bytes()
        with override_settings(STAGING_MODE=True,STAGING_CONFIG=self.state):
            self.assertIsNone(backend.authenticate(None,username=admin.username,password='working-password'))
            self.assertIsNotNone(backend.authenticate(None,username=admin.username,password='new-working-password'))
        self.assertEqual(source.read_bytes(),current)
        with closing(sqlite3.connect(source)) as db:
            db.execute('UPDATE accounts_user SET role_id=2 WHERE id=?',[admin.pk]);db.commit()
        with override_settings(STAGING_MODE=True,STAGING_CONFIG=self.state):self.assertIsNone(OwnerBackend().get_user(admin.pk))

    def test_new_source_administrator_is_created_only_in_test_and_not_with_a_local_shortcut(self):
        admin=User(pk=1001,username='new-admin',company=self.company,role=self.owner.role,auth_source='LOCAL')
        source=self.source_admins([(admin,'working-password')]);before=source.read_bytes()
        with override_settings(STAGING_MODE=True,STAGING_CONFIG=self.state):
            user=OwnerBackend().authenticate(None,username=admin.username,password='working-password')
            self.assertEqual(user.pk,1001)
            self.assertEqual(user.role.code,'ADMIN')
            user.set_password('test-shortcut');user.save(update_fields=['password'])
            self.assertIsNone(OwnerBackend().authenticate(None,username=admin.username,password='test-shortcut'))
            self.assertIsNotNone(OwnerBackend().authenticate(None,username=admin.username,password='working-password'))
        self.assertEqual(source.read_bytes(),before)

    def test_windows_admin_requires_directory_verification_and_portal_admin_role(self):
        import uuid
        admin=User.objects.create_user(username='ad:synthetic',company=self.company,role=self.owner.role,
            auth_source='LDAP',ldap_login='windows.admin',ldap_guid=uuid.uuid4())
        self.source_admins([(admin,'unused-directory-shadow')])
        request=RequestFactory().post('/',{'auth_source':'ldap'})
        directory={'enabled':True,'domain':'example.local'}
        with override_settings(STAGING_MODE=True,STAGING_CONFIG=self.state,STAGING_DIRECTORY=directory):
            with patch('apps.accounts.staging_directory.verify',return_value=True) as verify:
                self.assertEqual(OwnerBackend().authenticate(request,username=r'EXAMPLE\windows.admin',password='windows-password').pk,admin.pk)
                verify.assert_called_once()
            with patch('apps.accounts.staging_directory.verify',return_value=False):
                self.assertIsNone(OwnerBackend().authenticate(request,username='windows.admin',password='wrong'))
            self.assertIsNone(OwnerBackend().authenticate(None,username='windows.admin',password='unused-directory-shadow'))

    def test_guard_allows_other_administrator_before_role_impersonation(self):
        admin=User.objects.create_user(username='team-admin',company=self.company,role=self.owner.role)
        request=RequestFactory().get('/');request.user=admin
        def act_as(req):req.user=self.other;return HttpResponse('role view')
        with override_settings(STAGING_CONFIG=self.state):
            self.assertEqual(StagingGuard(act_as)(request).status_code,200)

    def test_same_local_and_windows_login_keeps_identity_namespaces_separate(self):
        import uuid
        local=User.objects.create_user(username='shared-name',company=self.company,role=self.owner.role)
        directory=User.objects.create_user(username='ad:different-guid',ldap_login='shared-name',ldap_guid=uuid.uuid4(),
            auth_source='LDAP',company=self.company,role=self.owner.role)
        self.source_admins([(local,'local-work-password'),(directory,'unused-shadow')])
        with override_settings(STAGING_MODE=True,STAGING_CONFIG=self.state,STAGING_DIRECTORY={'enabled':True,'domain':'example.local'}):
            self.assertEqual(OwnerBackend().authenticate(None,username='shared-name',password='local-work-password').pk,local.pk)
            request=RequestFactory().post('/',{'auth_source':'ldap'})
            with patch('apps.accounts.staging_directory.verify',return_value=True):
                self.assertEqual(OwnerBackend().authenticate(request,username='shared-name',password='windows-password').pk,directory.pk)

    def test_source_superuser_receives_portal_admin_role_without_global_django_privileges(self):
        admin=User.objects.create_user(username='super',company=self.company,is_superuser=True)
        source=self.source_admins([(admin,'working-super-password')]);before=source.read_bytes()
        with override_settings(STAGING_MODE=True,STAGING_CONFIG=self.state):
            user=OwnerBackend().authenticate(None,username='super',password='working-super-password')
            self.assertEqual(user.role.code,'ADMIN');self.assertFalse(user.is_superuser);self.assertFalse(user.is_staff)
            self.assertIsNotNone(OwnerBackend().get_user(user.pk))
        self.assertEqual(source.read_bytes(),before)

    def test_regular_test_administrator_cannot_change_the_working_password_in_copy(self):
        self.client.force_login(self.other)
        before=self.other.password
        with override_settings(STAGING_MODE=True,STAGING_CONFIG=self.state):
            response=self.client.post(reverse('accounts:password_change'),{
                'old_password':'synthetic-other-password','new_password1':'different','new_password2':'different'})
        self.assertRedirects(response,reverse('accounts:settings'),fetch_redirect_response=False)
        self.other.refresh_from_db();self.assertEqual(self.other.password,before)
