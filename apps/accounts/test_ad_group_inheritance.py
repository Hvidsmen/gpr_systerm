from unittest.mock import patch
from django.test import TestCase
from django.urls import reverse
from . import test_directory_admin as fixtures
from .models import AccessGroup,Role,User
from .directory_admin import AccessForm
from apps.projects.models import Project,ConstructionObject


class ADGroupInheritanceTests(TestCase):
    setUp=fixtures.DirectoryAdminTests.setUp

    @patch('apps.accounts.directory_admin.search_directory')
    def test_group_first_provisions_inherited_rights_and_later_changes_propagate(self,search):
        search.return_value=[self.identity.copy()]
        obj=ConstructionObject.objects.create(company=self.company,project=Project.objects.create(company=self.company,name='P'),name='KTP')
        group=AccessGroup.objects.create(company=self.company,name='Планировщики КТП',role=self.role)
        group.assigned_objects.add(obj)
        page=self.client.get(self.url,{'selected':self.token});self.assertContains(page,'Группа доступа')
        self.assertLess(list(page.context['form'].fields).index('access_group'),list(page.context['form'].fields).index('role'))
        # Browser-supplied role cannot override a selected group, even if forged.
        response=self.client.post(self.url,{'selected':self.token,'access_group':group.pk,'role':Role.objects.get(code='ADMIN').pk})
        self.assertEqual(response.status_code,302)
        user=User.objects.get(ldap_guid=self.identity['guid'])
        self.assertEqual(user.access_group,group);self.assertEqual(user.role,self.role)
        self.assertEqual(list(user.assigned_objects.all()),[obj]);self.assertFalse(user.access_group_from_ad)
        group.assigned_objects.clear();self.assertFalse(user.assigned_objects.exists())
        group.role=Role.objects.get(code='OBSERVER');group.save();user.refresh_from_db();self.assertEqual(user.role,group.role)

    def test_foreign_group_and_missing_individual_role_fail_validation(self):
        foreign=AccessGroup.objects.create(company=self.other,name='Foreign',role=self.role)
        form=AccessForm({'access_group':foreign.pk},company=self.company)
        self.assertFalse(form.is_valid());self.assertIn('access_group',form.errors)
        form=AccessForm({},company=self.company);self.assertFalse(form.is_valid());self.assertIn('role',form.errors)
