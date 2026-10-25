from django.test import TestCase
from django.urls import reverse
from .models import EquipmentType, EquipmentCategory, EquipmentCrewPosition, Brigade
from .position_catalog import position_for


class EquipmentCardTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        from apps.planning.test_workspace import WorkspaceTests
        WorkspaceTests.setUpTestData.__func__(cls)

    def setUp(self):
        self.client.force_login(self.planner)
        self.position = position_for(Brigade.objects.create(company=self.company,name='Экипаж карточки'))
        self.url = reverse('resources:equipment_type_update',args=[self.equipment.pk])

    def data(self, count='2'):
        return {'name':self.equipment.name,'unit':'ед.','is_active':'on',
                'crew-TOTAL_FORMS':'1','crew-INITIAL_FORMS':'0',
                'crew-0-position':str(self.position.pk),'crew-0-count':count}

    def test_edit_card_saves_crew_and_shows_it_in_preview(self):
        response=self.client.post(self.url,self.data())
        self.assertEqual(response.status_code,302)
        row=EquipmentCrewPosition.objects.get(equipment_type=self.equipment)
        self.assertEqual((row.position_id,row.count),(self.position.pk,2))
        response=self.client.get(reverse('resources:equipment_type_list'))
        self.assertContains(response,'Экипаж карточки')
        self.assertContains(response,'data-equipment-category')
        self.assertContains(self.client.get(self.url),'id_crew-TOTAL_FORMS')

    def test_invalid_crew_does_not_save_card(self):
        data=self.data('0');data['name']='Не сохранять'
        response=self.client.post(self.url,data)
        self.assertEqual(response.status_code,200)
        self.equipment.refresh_from_db()
        self.assertNotEqual(self.equipment.name,'Не сохранять')
        self.assertFalse(EquipmentCrewPosition.objects.filter(equipment_type=self.equipment).exists())

    def test_create_with_crew(self):
        data=self.data();data['name']='Новая техника с экипажем'
        response=self.client.post(reverse('resources:equipment_type_create'),data)
        self.assertEqual(response.status_code,302)
        equipment=EquipmentType.objects.get(company=self.company,name=data['name'])
        self.assertEqual(equipment.crew_positions.get().count,2)

    def test_foreign_position_rejected(self):
        from apps.accounts.models import Company
        foreign=position_for(Brigade.objects.create(company=Company.objects.create(name='Чужая'),name='Чужая должность'))
        data=self.data();data['crew-0-position']=foreign.pk
        response=self.client.post(self.url,data)
        self.assertEqual(response.status_code,200)
        self.assertFalse(self.equipment.crew_positions.exists())

    def test_groups_are_complete_and_table_is_paginated(self):
        category=EquipmentCategory.objects.create(company=self.company,name='Группа машин')
        for i in range(23):EquipmentType.objects.create(company=self.company,category=category,name=f'Группа {i:02}')
        response=self.client.get(reverse('resources:equipment_type_list'))
        self.assertContains(response,'Группа 22')
        self.assertFalse(response.context['is_paginated'])
        response=self.client.get(reverse('resources:equipment_type_list'),{'view':'table'})
        self.assertTrue(response.context['is_paginated'])
