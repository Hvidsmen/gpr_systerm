from django.test import TestCase
from django.urls import reverse
from apps.accounts.models import Company, User, Role
from .models import EquipmentType, EquipmentCategory

class EquipmentCategoryManageTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(name='Equipment categories')
        self.user = User.objects.create_user(username='category-manager', company=self.company, role=Role.objects.get(code='PLANNER'))
        self.client.force_login(self.user)
        self.category = EquipmentCategory.objects.create(company=self.company, name='Основная')

    def test_group_merge_preview_then_moves_positions(self):
        other = EquipmentCategory.objects.create(company=self.company, name='Монтажники')
        brigade = EquipmentType.objects.create(company=self.company, name='Сварщик', category=other)
        url = reverse('resources:equipment_category_merge')
        selected = [self.category.pk, other.pk]
        self.assertContains(self.client.get(reverse('resources:equipment_category_list')), 'Объединить выбранные')
        response = self.client.post(url, {'selected': selected, 'merge_stage': 'select'})
        self.assertContains(response, 'Основная категория')
        brigade.refresh_from_db()
        self.assertEqual(brigade.category, other)
        self.assertRedirects(self.client.post(url, {'selected': selected, 'target': self.category.pk, 'merge_stage': 'confirm'}), reverse('resources:equipment_category_list'))
        brigade.refresh_from_db()
        self.assertEqual(brigade.category, self.category)
        self.assertFalse(EquipmentCategory.objects.filter(pk=other.pk).exists())

    def test_group_merge_rejects_foreign_or_unselected_target(self):
        other = EquipmentCategory.objects.create(company=self.company, name='Other')
        foreign = EquipmentCategory.objects.create(company=Company.objects.create(name='Foreign merge'), name='Foreign')
        url = reverse('resources:equipment_category_merge')
        for selected, target in [([self.category.pk, foreign.pk], self.category.pk), ([self.category.pk, other.pk], foreign.pk)]:
            self.assertEqual(self.client.post(url, {'selected': selected, 'target': target, 'merge_stage': 'confirm'}).status_code, 404)
        self.assertTrue(EquipmentCategory.objects.filter(pk=other.pk).exists())
        self.user.role = Role.objects.get(code='MANAGER')
        self.user.save()
        self.assertEqual(self.client.post(url, {'selected': [self.category.pk, other.pk]}).status_code, 403)

    def test_group_rename_preserves_positions_and_rejects_duplicates(self):
        brigade = EquipmentType.objects.create(company=self.company, name='Worker', category=self.category)
        url = reverse('resources:equipment_category_update', args=[self.category.pk])
        self.assertContains(self.client.get(reverse('resources:equipment_category_list')), 'Изменить название')
        self.assertEqual(self.client.get(url).status_code, 200)
        self.assertRedirects(self.client.post(url, {'name': 'Новая категория'}), reverse('resources:equipment_category_list'))
        self.category.refresh_from_db()
        brigade.refresh_from_db()
        self.assertEqual(self.category.name, 'Новая категория')
        self.assertEqual(brigade.category_id, self.category.pk)
        EquipmentCategory.objects.create(company=self.company, name='Занято')
        self.assertContains(self.client.post(url, {'name': 'занято'}), 'Такая категория уже есть')
        self.category.refresh_from_db()
        self.assertEqual(self.category.name, 'Новая категория')

    def test_group_rename_scoped_and_role_protected(self):
        foreign = EquipmentCategory.objects.create(company=Company.objects.create(name='Foreign rename'), name='Foreign')
        self.assertEqual(self.client.post(reverse('resources:equipment_category_update', args=[foreign.pk]), {'name': 'Changed'}).status_code, 404)
        self.user.role = Role.objects.get(code='MANAGER')
        self.user.save()
        url = reverse('resources:equipment_category_update', args=[self.category.pk])
        self.assertEqual(self.client.get(url).status_code, 403)
        self.assertEqual(self.client.post(url, {'name': 'Changed'}).status_code, 403)

