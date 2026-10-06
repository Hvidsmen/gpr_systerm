from django.test import TestCase
from django.urls import reverse
from apps.accounts.models import Company, User, Role
from .models import Brigade, BrigadeGroup, BrigadeMacroGroup, EquipmentType, EquipmentCategory


class CatalogFilterTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(name='Filters')
        self.user = User.objects.create_user(username='filter-planner', company=self.company, role=Role.objects.get(code='PLANNER'))
        self.client.force_login(self.user)
        self.group = BrigadeGroup.objects.create(company=self.company,name='Group')
        self.macro = BrigadeMacroGroup.objects.create(company=self.company,name='Macro')
        self.category = EquipmentCategory.objects.create(company=self.company,name='Category')
        self.brigade = Brigade.objects.create(company=self.company,name='Монтаж Север',group=self.group,macro_group=self.macro)
        Brigade.objects.create(company=self.company,name='Монтаж Юг',group=self.group)
        Brigade.objects.create(company=self.company,name='Другие',macro_group=self.macro)
        self.equipment = EquipmentType.objects.create(company=self.company,name='Экскаватор Север',category=self.category)
        EquipmentType.objects.create(company=self.company,name='Экскаватор Юг')

    def test_combined_filters_and_cyrillic_case(self):
        response = self.client.get(reverse('resources:brigade_list'), {'name':'монтаж','group':self.group.pk,'macro_group':self.macro.pk})
        self.assertEqual(response.status_code,200)
        self.assertEqual(list(response.context['brigades']),[self.brigade])
        self.assertEqual(response.context['filter_form']['name'].value(),'монтаж')
        response = self.client.get(reverse('resources:equipment_type_list'), {'name':'экскаватор','category':self.category.pk})
        self.assertEqual(list(response.context['equipment_types']),[self.equipment])
        response = self.client.get(reverse('resources:brigade_list'), {'name':'Nothing'})
        self.assertContains(response,'По выбранным фильтрам бригады не найдены')
        self.assertEqual(response.context['paginator'].count,0)

    def test_foreign_catalogs_and_invalid_ids(self):
        other = Company.objects.create(name='Foreign filters')
        group = BrigadeGroup.objects.create(company=other,name='Foreign group')
        category = EquipmentCategory.objects.create(company=other,name='Foreign category')
        Brigade.objects.create(company=other,name='Foreign brigade')
        self.assertNotContains(self.client.get(reverse('resources:brigade_list')),group.name)
        self.assertNotContains(self.client.get(reverse('resources:equipment_type_list')),category.name)
        for route, param, value in [('brigade_list','group',group.pk),('brigade_list','macro_group','bad'),('equipment_type_list','category',category.pk)]:
            response = self.client.get(reverse('resources:'+route),{param:value})
            self.assertEqual(response.status_code,200)
            self.assertIn(param,response.context['filter_form'].errors)
            self.assertEqual(response.context['paginator'].count,0)

    def test_pagination_keeps_filters(self):
        for i in range(24):
            Brigade.objects.create(company=self.company,name='Монтаж '+str(i),group=self.group,macro_group=self.macro)
            EquipmentType.objects.create(company=self.company,name='Экскаватор '+str(i),category=self.category)
        for route, values, key in [('brigade_list',{'name':'Монтаж','group':self.group.pk,'macro_group':self.macro.pk},'brigades'),('equipment_type_list',{'name':'Экскаватор','category':self.category.pk},'equipment_types')]:
            response = self.client.get(reverse('resources:'+route),values)
            self.assertTrue(response.context['is_paginated'])
            self.assertContains(response,response.context['filter_query'].replace('&','&amp;')+'&amp;page=2')
            response = self.client.get(reverse('resources:'+route),dict(values,page=2))
            self.assertEqual(response.status_code,200)
            self.assertEqual(len(response.context[key]),5)
