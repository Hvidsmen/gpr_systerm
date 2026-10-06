from django.test import TestCase
from django.urls import reverse
from apps.accounts.models import Company, User, Role
from .models import Brigade, BrigadeGroup, BrigadeMacroGroup


class BrigadeCatalogTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(name='Brigade catalogs')
        self.user = User.objects.create_user(username='brigade-catalog-planner', company=self.company, role=Role.objects.get(code='PLANNER'))
        self.client.force_login(self.user)
        self.group = BrigadeGroup.objects.create(company=self.company, name='Монтаж')
        self.macro = BrigadeMacroGroup.objects.create(company=self.company, name='Производство')

    def test_optional_fields_create_edit_and_list(self):
        url = reverse('resources:brigade_create')
        self.assertContains(self.client.get(url), 'data-catalog-open="brigade-group"')
        self.assertEqual(self.client.post(url, {'name':'Assigned','group':self.group.pk,'macro_group':self.macro.pk,'is_active':'on'}).status_code, 302)
        row = Brigade.objects.get(name='Assigned')
        self.assertEqual(row.group, self.group)
        self.assertEqual(row.macro_group, self.macro)
        self.assertContains(self.client.get(reverse('resources:brigade_list')), self.group.name)
        self.assertEqual(self.client.post(reverse('resources:brigade_update', args=[row.pk]), {'name':'Assigned','group':'','macro_group':'','is_active':'on'}).status_code, 302)
        row.refresh_from_db()
        self.assertIsNone(row.group)
        self.assertIsNone(row.macro_group)
        self.assertEqual(self.client.post(url, {'name':'Optional'}).status_code, 302)

    def test_foreign_choices_and_catalogs(self):
        other = Company.objects.create(name='Foreign brigade catalogs')
        group = BrigadeGroup.objects.create(company=other, name='Foreign group')
        macro = BrigadeMacroGroup.objects.create(company=other, name='Foreign macro')
        response = self.client.post(reverse('resources:brigade_create'), {'name':'Invalid','group':group.pk,'macro_group':macro.pk})
        self.assertEqual(response.status_code, 200)
        self.assertIn('group', response.context['form'].errors)
        self.assertIn('macro_group', response.context['form'].errors)
        self.assertFalse(Brigade.objects.exists())
        self.assertNotContains(self.client.get(reverse('resources:brigade_group_list')), group.name)
        self.assertNotContains(self.client.get(reverse('resources:brigade_macro_group_list')), macro.name)

    def test_quick_creation_duplicates_and_roles(self):
        for kind, model, name in [('brigade_group',BrigadeGroup,'Новая группа'),('brigade_macro_group',BrigadeMacroGroup,'Новая макрогруппа')]:
            url = reverse('resources:'+kind+'_create')
            response = self.client.post(url, {'name':name,'company':999})
            self.assertEqual(response.status_code, 201)
            self.assertEqual(model.objects.get(pk=response.json()['value']).company, self.company)
            self.assertEqual(self.client.post(url, {'name':name.lower()}).status_code, 400)
            self.assertEqual(self.client.post(url, {'name':''}).status_code, 400)
        for code in ('MANAGER','FOREMAN'):
            user = User.objects.create_user(username='brigade-catalog-'+code, company=self.company, role=Role.objects.get(code=code))
            self.client.force_login(user)
            for kind in ('brigade_group','brigade_macro_group'):
                self.assertEqual(self.client.post(reverse('resources:'+kind+'_create'), {'name':'Unauthorized'}).status_code, 403)
