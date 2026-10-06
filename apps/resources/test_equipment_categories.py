from django.test import TestCase, TransactionTestCase
from django.urls import reverse
from apps.accounts.models import Company, User, Role
from .models import EquipmentCategory, EquipmentType


class EquipmentCategoryTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(name='Categories')
        self.user = User.objects.create_user(username='categories-planner', company=self.company, role=Role.objects.get(code='PLANNER'))
        self.category = EquipmentCategory.objects.create(company=self.company, name='Землеройная')
        self.other = Company.objects.create(name='Other categories')
        self.foreign = EquipmentCategory.objects.create(company=self.other, name='Foreign')
        self.client.force_login(self.user)

    def test_choice_and_quick_create_preserve_main_form(self):
        page = self.client.get(reverse('resources:equipment_type_create'))
        self.assertContains(page, '<select')
        self.assertContains(page, 'data-catalog-open="category"')
        self.assertContains(page, self.category.name)
        self.assertNotContains(page, self.foreign.name)
        response = self.client.post(reverse('resources:equipment_category_create'), {'name':'Подъёмная', 'company':self.other.pk})
        self.assertEqual(response.status_code, 201)
        category = EquipmentCategory.objects.get(pk=response.json()['value'])
        self.assertEqual(category.company, self.company)
        response = self.client.post(reverse('resources:equipment_type_create'), {'name':'Кран', 'category':category.pk, 'is_active':'on'})
        self.assertEqual(response.status_code, 302)
        row = EquipmentType.objects.get(name='Кран')
        self.assertEqual(row.category, category)
        response = self.client.get(reverse('resources:equipment_type_update', args=[row.pk]))
        self.assertEqual(response.context['form']['category'].value(), category.pk)

    def test_optional_category_and_foreign_choice_rejected(self):
        url = reverse('resources:equipment_type_create')
        response = self.client.post(url, {'name':'Invalid', 'category':self.foreign.pk})
        self.assertEqual(response.status_code, 200)
        self.assertIn('category', response.context['form'].errors)
        self.assertFalse(EquipmentType.objects.exists())
        self.assertEqual(self.client.post(url, {'name':'Без категории'}).status_code, 302)
        self.assertIsNone(EquipmentType.objects.get().category)

    def test_duplicate_blank_and_permissions(self):
        url = reverse('resources:equipment_category_create')
        for name in ('', '   ', 'землеройная'):
            self.assertEqual(self.client.post(url, {'name':name}).status_code, 400)
        self.assertNotContains(self.client.get(reverse('resources:equipment_category_list')), self.foreign.name)
        for code in ('MANAGER', 'FOREMAN'):
            user = User.objects.create_user(username='category-'+code, company=self.company, role=Role.objects.get(code=code))
            self.client.force_login(user)
            self.assertEqual(self.client.post(url, {'name':'Unauthorized'}).status_code, 403)


class EquipmentCategoryMigrationTests(TransactionTestCase):
    def test_legacy_category_conversion_and_reverse(self):
        from django.db import connection
        from django.db.migrations.executor import MigrationExecutor
        previous = [('resources', '0003_alter_brigade_code_alter_fueltype_code_and_more')]
        current = [('resources', '0004_equipmentcategory_alter_equipmenttype_category_and_more')]
        executor = MigrationExecutor(connection)
        executor.migrate(previous)
        try:
            old_apps = executor.loader.project_state(previous).apps
            CompanyModel = old_apps.get_model('accounts', 'Company')
            OldType = old_apps.get_model('resources', 'EquipmentType')
            company = CompanyModel.objects.create(name='Legacy category company')
            foreign = CompanyModel.objects.create(name='Foreign legacy categories')
            first = OldType.objects.create(company=company, name='A', category='Землеройная')
            second = OldType.objects.create(company=company, name='B', category='Землеройная')
            empty = OldType.objects.create(company=company, name='Empty', category='')
            other = OldType.objects.create(company=foreign, name='Other', category='Землеройная')
            executor = MigrationExecutor(connection)
            executor.migrate(current)
            apps = executor.loader.project_state(current).apps
            NewType = apps.get_model('resources', 'EquipmentType')
            self.assertEqual(NewType.objects.get(pk=first.pk).category_id, NewType.objects.get(pk=second.pk).category_id)
            self.assertNotEqual(NewType.objects.get(pk=first.pk).category_id, NewType.objects.get(pk=other.pk).category_id)
            self.assertIsNone(NewType.objects.get(pk=empty.pk).category_id)
            executor = MigrationExecutor(connection)
            executor.migrate(previous)
            OldType = executor.loader.project_state(previous).apps.get_model('resources', 'EquipmentType')
            self.assertEqual(OldType.objects.get(pk=first.pk).category, 'Землеройная')
        finally:
            MigrationExecutor(connection).migrate(current)
