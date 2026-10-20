from django.test import TestCase
from django.urls import reverse
from .models import User, Company, Role, AccessGroup


class UserFilterTests(TestCase):
    def setUp(self):
        self.company=Company.objects.create(name='Filter company')
        self.other=Company.objects.create(name='Other filter company')
        self.admin=User.objects.create_user(username='filter-admin',company=self.company,role=Role.objects.get(code='ADMIN'))
        self.client.force_login(self.admin)
        self.role=Role.objects.get(code='PLANNER')
        self.group=AccessGroup.objects.create(name='Planning filters',company=self.company,role=self.role)
        self.user=User.objects.create_user(username='ivan-filter',company=self.company,first_name='Иван',last_name='Иванов',email='ivan@example.org',auth_source='LDAP',access_group=self.group)
        self.url=reverse('accounts:user_list')

    def test_combined_filters_and_name_tokens(self):
        response=self.client.get(self.url,{'q':'Иванов Иван','role':self.role.pk,'access_group':self.group.pk,'auth_source':'LDAP','active':'yes'})
        self.assertEqual(list(response.context['users']),[self.user])
        self.assertContains(response,'Найдено: 1 из 2')
        self.assertEqual(list(self.client.get(self.url,{'q':'ivan@example.org'}).context['users']),[self.user])
        self.assertFalse(self.client.get(self.url,{'active':'no'}).context['users'])

    def test_foreign_company_and_invalid_filters_do_not_leak(self):
        foreign=AccessGroup.objects.create(name='Hidden group',company=self.other,role=self.role)
        User.objects.create_user(username='foreign-filter',company=self.other)
        response=self.client.get(self.url,{'access_group':foreign.pk})
        self.assertEqual(response.status_code,200);self.assertFalse(response.context['users'])
        self.assertNotContains(response,'Hidden group')
        self.assertFalse(self.client.get(self.url,{'role':'invalid'}).context['users'])

    def test_missing_role_and_pagination_preserve_filters(self):
        pending=User.objects.create_user(username='pending-filter',company=self.company)
        self.assertEqual(list(self.client.get(self.url,{'unassigned':'on'}).context['users']),[pending])
        User.objects.bulk_create([User(username=f'page-filter-{i}',company=self.company,role=self.role) for i in range(23)])
        response=self.client.get(self.url,{'q':'page-filter','active':'yes'})
        self.assertContains(response,'q=page-filter&amp;active=yes&amp;page=2')
        second=self.client.get(self.url,{'q':'page-filter','active':'yes','page':2})
        self.assertEqual(len(second.context['users']),3)
