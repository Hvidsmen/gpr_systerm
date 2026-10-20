from uuid import uuid4
from unittest.mock import patch
from django.test import TestCase, override_settings, RequestFactory
from django.core.exceptions import PermissionDenied, ValidationError
from django.urls import reverse
from .models import Company, Role, User, AccessGroup
from .access_groups import map_directory_group
from .backends import DirectoryBackend


class AccessGroupTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(name='Group company')
        self.other = Company.objects.create(name='Other group company')
        self.role = Role.objects.get(code='PLANNER')
        self.admin = User.objects.create_user(username='group-admin', company=self.company, role=Role.objects.get(code='ADMIN'))
        self.user = User.objects.create_user(username='member', company=self.company, role=self.role)
        self.group = AccessGroup.objects.create(name='Planning', company=self.company, role=self.role, ad_group_dn='CN=Planning,OU=Groups,DC=example,DC=org')
        self.url = reverse('accounts:access_groups')
        self.client.force_login(self.admin)

    def test_group_ui_bulk_assignment_and_role_update(self):
        response = self.client.post(reverse('accounts:access_group_edit', args=[self.group.pk]), {'name':'Planning', 'role':self.role.pk, 'members':[self.user.pk], 'ad_group_dn':self.group.ad_group_dn})
        self.assertEqual(response.status_code,302)
        self.user.refresh_from_db()
        self.assertEqual(self.user.access_group,self.group)
        self.group.role=Role.objects.get(code='OBSERVER');self.group.save()
        self.user.refresh_from_db();self.assertEqual(self.user.role,self.group.role)
        self.assertContains(self.client.get(self.url),'CN=Planning')
        self.client.post(reverse('accounts:access_group_edit', args=[self.group.pk]), {'name':'Planning','role':self.role.pk})
        self.user.refresh_from_db();self.assertIsNone(self.user.access_group_id);self.assertIsNone(self.user.role_id)

    def test_company_and_privilege_boundaries(self):
        foreign=User.objects.create_user(username='foreign-group',company=self.other)
        response=self.client.post(reverse('accounts:access_group_edit',args=[self.group.pk]),{'name':'Planning','role':self.role.pk,'members':[foreign.pk]})
        self.assertEqual(response.status_code,200);self.assertTrue(response.context['form'].errors)
        self.client.force_login(self.user);self.assertEqual(self.client.get(self.url).status_code,403)
        self.user.access_group=AccessGroup.objects.create(name='Foreign',company=self.other,role=self.role)
        with self.assertRaises(ValidationError): self.user.save()
        with self.assertRaises(ValidationError): AccessGroup.objects.create(name='Admin',company=self.company,role=self.admin.role)

    @override_settings(LDAP_ENABLED=True,LDAP_BIND_PASSWORD='test')
    @patch('apps.accounts.backends.directory_identity')
    def test_verified_ad_login_provisions_profile_and_revokes_removed_membership(self, lookup):
        guid=uuid4()
        identity={'guid':guid,'first_name':'AD','last_name':'Member','email':'','group_dns':[self.group.ad_group_dn.upper()]}
        lookup.return_value=identity
        request=RequestFactory().post('/',{'auth_source':'ldap'})
        user=DirectoryBackend().authenticate(request,username='member',password='test')
        self.assertEqual(user.company,self.company);self.assertEqual(user.access_group,self.group)
        self.assertFalse(user.has_usable_password());self.assertTrue(user.access_group_from_ad)
        identity['group_dns']=[]
        user=DirectoryBackend().authenticate(request,username='member',password='test')
        self.assertIsNone(user.role_id);self.assertIsNone(user.access_group_id)

    def test_conflicts_and_company_switch_refused(self):
        second=AccessGroup.objects.create(name='Other',company=self.company,role=self.role,ad_group_dn='CN=Other,DC=example,DC=org')
        with self.assertRaises(PermissionDenied):map_directory_group(self.user,{'group_dns':[self.group.ad_group_dn,second.ad_group_dn]})
        self.user.company=self.other;self.user.save()
        with self.assertRaises(PermissionDenied):map_directory_group(self.user,{'group_dns':[self.group.ad_group_dn]})

    def test_objects_inherit_and_refresh_for_all_members(self):
        from apps.projects.models import Project, ConstructionObject
        project=Project.objects.create(company=self.company,name='Project')
        obj=ConstructionObject.objects.create(company=self.company,project=project,name='KTP')
        self.user.access_group=self.group;self.user.save()
        self.group.assigned_objects.add(obj)
        self.assertEqual(list(self.user.assigned_objects.all()),[obj])
        self.group.assigned_objects.clear()
        self.assertFalse(self.user.assigned_objects.exists())
        foreign_project=Project.objects.create(company=self.other,name='Other')
        foreign=ConstructionObject.objects.create(company=self.other,project=foreign_project,name='Other KTP')
        with self.assertRaises(ValidationError):
            self.group.assigned_objects.add(foreign)

    def test_personal_form_inherits_role_and_objects(self):
        from .forms import CompanyUserAccessForm
        form=CompanyUserAccessForm({'access_group':self.group.pk,'is_active':'on'},company=self.company,instance=self.user)
        self.assertTrue(form.is_valid(),form.errors)
        form.save();self.user.refresh_from_db()
        self.assertEqual(self.user.role,self.group.role)
        self.assertEqual(self.user.access_group,self.group)

    @override_settings(LDAP_ENABLED=True,LDAP_BIND_PASSWORD='test')
    def test_directory_checks_nested_group_after_password_verification(self):
        from unittest.mock import MagicMock
        from .backends import directory_identity
        from ldap3 import BASE
        guid=uuid4()
        entry=MagicMock();entry.entry_dn='CN=Member,OU=Users,DC=example,DC=org'
        entry.__getitem__.side_effect=lambda key:MagicMock(raw_values=[guid.bytes_le],value='value')
        service=MagicMock();service.entries=[entry]
        verified=MagicMock()
        with patch('ldap3.Server'),patch('ldap3.Connection',side_effect=[service,verified]):
            identity=directory_identity('member','password')
        self.assertEqual(identity['group_dns'],[self.group.ad_group_dn])
        args=service.search.call_args
        self.assertEqual(args.args[0],self.group.ad_group_dn)
        self.assertIn('member:1.2.840.113556.1.4.1941:=',args.args[1])
        self.assertEqual(args.kwargs['search_scope'],BASE)
        verified.unbind.assert_called_once();service.unbind.assert_called_once()
