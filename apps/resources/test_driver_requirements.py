from io import BytesIO
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from openpyxl import load_workbook
from apps.accounts.models import Company, User, Role
from .models import EquipmentType

class DriverRequirementsTests(TestCase):
    def setUp(self):
        self.company=Company.objects.create(name='Driver requirements')
        self.user=User.objects.create_user(username='driver-planner',company=self.company,role=Role.objects.get(code='PLANNER'))
        self.client.force_login(self.user)

    def test_create_edit_and_export_keep_licences_and_classes_separate(self):
        response=self.client.post(reverse('resources:equipment_type_create'),{'name':'Вездеход','driver_categories':['road_C','tractor_C','road_CE'],'driver_classes':['1','2'],'is_active':'on'})
        self.assertEqual(response.status_code,302)
        machine=EquipmentType.objects.get(company=self.company,name='Вездеход')
        self.assertEqual(machine.driver_categories,['road_C','tractor_C','road_CE'])
        self.assertEqual(machine.driver_classes,['1','2'])
        response=self.client.get(reverse('resources:equipment_type_update',args=[machine.pk]))
        self.assertEqual(response.context['form']['driver_categories'].value(),machine.driver_categories)
        self.assertEqual(response.context['form']['driver_classes'].value(),['1','2'])
        response=self.client.get(reverse('resources:equipment_type_list'))
        self.assertContains(response,'ВУ · C');self.assertContains(response,'Трактор · C');self.assertContains(response,'1-й класс')
        response=self.client.get(reverse('resources:equipment_type_export'))
        book=load_workbook(BytesIO(response.content));text=' '.join(str(c.value) for row in book.active for c in row)
        self.assertIn('ВУ · CE',text);self.assertIn('2-й класс',text)
        self.client.post(reverse('resources:equipment_type_update',args=[machine.pk]),{'name':'Вездеход','is_active':'on'})
        machine.refresh_from_db();self.assertEqual(machine.driver_classes,[]);self.assertEqual(machine.driver_categories,[])

    def test_fields_optional_and_unknown_codes_rejected(self):
        url=reverse('resources:equipment_type_create')
        self.assertEqual(self.client.post(url,{'name':'Существующая техника'}).status_code,302)
        machine=EquipmentType.objects.get(name='Существующая техника');self.assertEqual(machine.driver_categories,[]);self.assertEqual(machine.driver_classes,[])
        response=self.client.post(url,{'name':'Invalid','driver_categories':['CE'],'driver_classes':['4']})
        self.assertEqual(response.status_code,200)
        self.assertIn('driver_categories',response.context['form'].errors);self.assertIn('driver_classes',response.context['form'].errors)
        self.assertFalse(EquipmentType.objects.filter(name='Invalid').exists())
        for field,value in [('driver_categories','road_C'),('driver_categories',['unknown']),('driver_classes',['1','1'])]:
            setattr(machine,field,value)
            with self.assertRaises(ValidationError):machine.full_clean()
            setattr(machine,field,[])

    def test_edit_remains_company_scoped(self):
        other=Company.objects.create(name='Foreign equipment')
        machine=EquipmentType.objects.create(company=other,name='Чужая техника')
        self.assertEqual(self.client.get(reverse('resources:equipment_type_update',args=[machine.pk])).status_code,404)
