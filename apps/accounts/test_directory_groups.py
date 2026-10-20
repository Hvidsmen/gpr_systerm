from unittest.mock import patch, MagicMock
from django.test import TestCase, override_settings
from django.urls import reverse
from .models import Company, User, Role
from .directory_groups import search_groups


class DirectoryGroupTests(TestCase):
    def setUp(self):
        self.company=Company.objects.create(name='Directory group picker')
        self.admin=User.objects.create_user(username='picker-admin',company=self.company,role=Role.objects.get(code='ADMIN'))
        self.client.force_login(self.admin)

    @patch('apps.accounts.directory_groups.search_groups')
    def test_admin_search_and_non_admin_denied(self, search):
        search.return_value={'groups':[{'name':'Planners','dn':'CN=Planners,DC=test,DC=local','description':''}],'more':False}
        response=self.client.get(reverse('accounts:ad_group_search'),{'q':'plan'})
        self.assertEqual(response.json(),search.return_value)
        self.assertEqual(response['Cache-Control'],'no-store')
        search.assert_called_once_with('plan')
        self.assertContains(self.client.get(reverse('accounts:access_groups')),'ad-group-results')
        self.admin.role=Role.objects.get(code='PLANNER');self.admin.save()
        search.reset_mock()
        self.assertEqual(self.client.get(reverse('accounts:ad_group_search')).status_code,403)
        search.assert_not_called()

    @override_settings(LDAP_ENABLED=True,LDAP_BIND_PASSWORD='test',LDAP_DOMAIN='test.local',LDAP_GROUP_BASE_DN='')
    def test_escaped_domain_search_limit_and_cleanup(self):
        entry=MagicMock();entry.entry_dn='CN=Planners,DC=test,DC=local'
        entry.__getitem__.side_effect=lambda key:MagicMock(value='Planners' if key=='cn' else 'Description')
        connection=MagicMock();connection.entries=[entry]
        connection.result={'controls':{'1.2.840.113556.1.4.319':{'value':{'cookie':b'next'}}}}
        with patch('ldap3.Server'),patch('ldap3.Connection',return_value=connection):
            data=search_groups('x*)(cn=*')
        self.assertTrue(data['more']);self.assertEqual(data['groups'][0]['dn'],entry.entry_dn)
        self.assertEqual(connection.search.call_args.args[0],'DC=test,DC=local')
        self.assertIn(r'x\2a\29\28cn=\2a',connection.search.call_args.args[1])
        self.assertEqual(connection.search.call_args.kwargs['paged_size'],100)
        connection.unbind.assert_called_once()

    @override_settings(LDAP_ENABLED=False)
    def test_configuration_error_is_visible_without_secrets(self):
        response=self.client.get(reverse('accounts:ad_group_search'))
        self.assertEqual(response.status_code,400)
        self.assertIn('LDAP',response.json()['error'])
