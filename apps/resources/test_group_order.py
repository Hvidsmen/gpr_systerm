from django.test import TestCase
from django.urls import reverse
from apps.accounts.models import Company,Role,User
from .models import BrigadeGroup,Brigade,EquipmentCategory,EquipmentType
from .group_order import labor_group_order,equipment_group_order,ordered


class CommonGroupOrderTests(TestCase):
    def setUp(self):
        self.company=Company.objects.create(name='Порядок групп');self.user=User.objects.create_user(username='group-order-planner',company=self.company,role=Role.objects.get(code='PLANNER'))
        self.names=['ИТР Состав','Водители спец.техники','Водители вспомогательной техники','Основной рабочий состав','Вспомогательный персонал','Другая группа','Без группы']
        self.groups={name:BrigadeGroup.objects.create(company=self.company,name=name) for name in reversed(self.names[:-1])}
        for index,name in enumerate(self.names):Brigade.objects.create(staffing_current=True,company=self.company,name=f'Должность {index}',group=self.groups.get(name))
        self.client.force_login(self.user)
    def test_normalized_order_is_shared_by_querysets_and_catalogue_hierarchy(self):
        self.assertEqual(sorted(reversed(self.names),key=labor_group_order),self.names)
        self.assertEqual(labor_group_order('Водители спец. техники')[0],1)
        result=ordered(Brigade.objects.filter(company=self.company),group_path='group')
        self.assertEqual([row.group.name if row.group_id else 'Без группы' for row in result],self.names)
        page=self.client.get(reverse('resources:brigade_list'));self.assertEqual([row['name'] for row in page.context['position_groups']],self.names)
    def test_equipment_uses_the_same_priority_before_its_names(self):
        names=['Специализированная техника','Вспомогательная техника','Другая техника','Без группы']
        self.assertEqual(sorted(reversed(names),key=equipment_group_order),names)
        categories={name:EquipmentCategory.objects.create(company=self.company,name=name) for name in names[:-1]}
        for index,name in enumerate(names):EquipmentType.objects.create(company=self.company,name=f'Техника {index}',category=categories.get(name))
        page=self.client.get(reverse('resources:equipment_type_list'));self.assertEqual([row['label'] for row in page.context['equipment_category_groups']],names)
