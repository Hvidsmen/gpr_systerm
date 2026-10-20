from unittest.mock import patch
from uuid import uuid4
from django.core import signing
from django.test import TestCase
from django.urls import reverse
from .models import Company,Role,User


class DirectoryAdminTests(TestCase):
    def setUp(self):
        self.company=Company.objects.create(name='AD admin')
        self.other=Company.objects.create(name='Other AD')
        self.admin=User.objects.create_user(username='ad-admin',company=self.company,role=Role.objects.get(code='ADMIN'))
        self.client.force_login(self.admin)
        self.url=reverse('accounts:add_from_ad')
        self.identity=dict(guid=str(uuid4()),login='ivan',name='Иван Иванов',first_name='Иван',last_name='Иванов',email='ivan@example.org')
        self.token=signing.dumps(dict(guid=self.identity['guid'],actor=self.admin.pk,company=self.company.pk),salt='ad-add-user')
        self.role=Role.objects.get(code='PLANNER')

    @patch('apps.accounts.directory_admin.search_directory')
    def test_admin_search_select_and_provision_without_user_password(self,search):
        search.return_value=[self.identity.copy()]
        self.assertContains(self.client.get(self.url,{'q':'ivan'}),'Выбрать')
        self.assertContains(self.client.get(self.url,{'selected':self.token}),'Добавить в ГПР')
        response=self.client.post(self.url,{'selected':self.token,'role':self.role.pk})
        user=User.objects.get(ldap_guid=self.identity['guid'])
        self.assertRedirects(response,reverse('accounts:user_access',args=[user.pk]))
        self.assertEqual(user.company_id,self.company.pk);self.assertEqual(user.role_id,self.role.pk)
        self.assertEqual(user.auth_source,'LDAP');self.assertFalse(user.has_usable_password())
        self.assertFalse(user.is_staff);self.assertFalse(user.is_superuser)
        self.client.post(self.url,{'selected':self.token,'role':Role.objects.get(code='ADMIN').pk})
        user.refresh_from_db();self.assertEqual(user.role_id,self.role.pk)

    @patch('apps.accounts.directory_admin.search_directory')
    def test_other_company_identity_and_forged_selection_cannot_be_claimed(self,search):
        search.return_value=[self.identity.copy()]
        user=User.objects.create_user(username='external',auth_source='LDAP',ldap_guid=self.identity['guid'],company=self.other)
        self.client.post(self.url,{'selected':self.token,'role':self.role.pk})
        user.refresh_from_db();self.assertEqual(user.company_id,self.other.pk)
        search.reset_mock()
        self.client.post(self.url,{'selected':self.token+'bad','role':self.role.pk})
        search.assert_not_called()
        self.admin.role=Role.objects.get(code='OBSERVER');self.admin.save()
        self.assertEqual(self.client.get(self.url).status_code,403)

    @patch('apps.accounts.directory_admin.search_directory')
    def test_existing_pending_identity_is_assigned_without_duplicate(self,search):
        search.return_value=[self.identity.copy()]
        user=User.objects.create_user(username='pending',auth_source='LDAP',ldap_guid=self.identity['guid'])
        self.client.post(self.url,{'selected':self.token,'role':self.role.pk})
        user.refresh_from_db();self.assertEqual(user.company_id,self.company.pk)
        self.assertEqual(User.objects.filter(ldap_guid=self.identity['guid']).count(),1)

    def test_directory_search_escapes_query_and_uses_guid_bytes(self):
        from django.test import override_settings
        from unittest.mock import MagicMock
        from .directory_admin import search_directory
        from uuid import UUID
        entry=MagicMock();guid=UUID(self.identity['guid'])
        entry.__getitem__.side_effect=lambda key:MagicMock(raw_values=[guid.bytes_le],value=self.identity.get({'sAMAccountName':'login','displayName':'name','givenName':'first_name','sn':'last_name','mail':'email'}.get(key,''),''))
        connection=MagicMock();connection.entries=[entry]
        with override_settings(LDAP_ENABLED=True,LDAP_BIND_PASSWORD='test-secret'),patch('ldap3.Server'),patch('ldap3.Connection',return_value=connection):
            self.assertEqual(search_directory(query='a*)(x')[0]['guid'],str(guid))
            self.assertIn(r'a\2a\29\28x',connection.search.call_args.args[1])
            search_directory(guid=guid)
            self.assertIn(''.join('\\'+format(b,'02x') for b in guid.bytes_le),connection.search.call_args.args[1])
        self.assertEqual(connection.unbind.call_count,2)
