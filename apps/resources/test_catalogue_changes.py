from django.test import TestCase
from django.urls import reverse
from apps.accounts.models import Company,Role,User
from .models import Brigade,BrigadeGroup,Equipment,EquipmentCategory,EquipmentType


class PositionCatalogueViewsTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.company=Company.objects.create(name='Updated catalogues')
        cls.user=User.objects.create_user(username='catalogue-editor',company=cls.company,role=Role.objects.get(code='PLANNER'))
        cls.group=BrigadeGroup.objects.create(company=cls.company,name='Рабочие')
        for n in range(53):Brigade.objects.create(company=cls.company,name=f'Должность {n:03}',group=cls.group)
        cls.orphan=Brigade.objects.create(company=cls.company,name='Без назначения')
    def setUp(self):self.client.force_login(self.user)
    def test_tree_does_not_split_a_group_and_table_is_paginated(self):
        url=reverse('resources:brigade_list');tree=self.client.get(url)
        self.assertEqual(tree.status_code,200);self.assertFalse(tree.context['is_paginated'])
        self.assertEqual(len(tree.context['position_groups'][0]['rows']),53)
        self.assertContains(tree,'Без группы');self.assertNotContains(tree,'Макрогрупп');self.assertNotContains(tree,'лист «Сводка»')
        table=self.client.get(url,{'view':'table'});self.assertEqual(len(table.context['brigades']),50);self.assertTrue(table.context['is_paginated'])
        self.assertContains(table,'Импорт групп из Excel');self.assertContains(table,'Экспорт в Excel')
    def test_macro_group_is_not_in_forms_or_exports(self):
        from .forms import BrigadeForm
        self.assertNotIn('macro_group',BrigadeForm().fields)
        from openpyxl import load_workbook
        from io import BytesIO
        response=self.client.get(reverse('resources:brigade_export'));sheet=load_workbook(BytesIO(response.content)).active
        self.assertNotIn('Макрогруппа',next(sheet.values))
    def test_machine_navigation_has_catalogue_links(self):
        response=self.client.get(reverse('resources:equipment_unit_list'))
        self.assertContains(response,reverse('resources:equipment_type_list'));self.assertContains(response,reverse('resources:equipment_category_list'))


class MultipleSummaryUploadTests(TestCase):
    def test_one_or_many_files_are_accepted_and_empty_rejected(self):
        from .transport_import_views import UploadForm
        from .test_transport_import import upload
        from django.utils import timezone
        for count in [1,3]:
            form=UploadForm({'date':str(timezone.localdate())},{'files':[upload([]) for _ in range(count)]})
            self.assertTrue(form.is_valid(),form.errors);self.assertEqual(len(form.cleaned_data['files']),count)
        self.assertFalse(UploadForm({'date':str(timezone.localdate())},{}).is_valid())


class EquipmentGroupImportTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.company=Company.objects.create(name='Equipment classification')
        cls.user=User.objects.create_user(username='classification-editor',company=cls.company,role=Role.objects.get(code='PLANNER'))
        cls.a=EquipmentCategory.objects.create(company=cls.company,name='Группа А');cls.b=EquipmentCategory.objects.create(company=cls.company,name='Группа Б')
        cls.root=EquipmentType.objects.create(company=cls.company,name='АПС',category=cls.a)
        cls.other_root=EquipmentType.objects.create(company=cls.company,name='Другой корень',category=cls.b)
        cls.model=EquipmentType.objects.create(company=cls.company,name='АПС / 416Б',brand_model='416Б',parent=cls.root)
        cls.other_model=EquipmentType.objects.create(company=cls.company,name='Другой корень / 416Б',brand_model='416Б',parent=cls.other_root)
        cls.unit=Equipment.objects.create(company=cls.company,type=cls.model,plate_number='A001',name='Машина 1',vin='VIN1',brand_model='416Б')
        cls.second=Equipment.objects.create(company=cls.company,type=cls.model,plate_number='A002',name='Машина 2')
    def row(self,**values):return {'line':2,'id':str(self.unit.pk),'техника для планирования':self.other_root.name,'группа техники':self.b.name,**values}
    def test_only_selected_machine_changes_and_no_catalogue_entries_created(self):
        from .equipment_group_import import apply
        counts=(Equipment.objects.count(),EquipmentType.objects.count(),EquipmentCategory.objects.count())
        result=apply(self.user,[self.row()]);self.assertEqual(result['updated'],1)
        self.unit.refresh_from_db();self.second.refresh_from_db();self.model.refresh_from_db()
        self.assertEqual(self.unit.type_id,self.other_model.pk);self.assertEqual(self.unit.catalogue_group_id,self.b.pk)
        self.assertEqual(self.second.type_id,self.model.pk);self.assertEqual(self.model.parent_id,self.root.pk)
        self.assertEqual(counts,(Equipment.objects.count(),EquipmentType.objects.count(),EquipmentCategory.objects.count()))
    def test_missing_group_or_model_warns_and_keeps_entire_row(self):
        from .equipment_group_import import apply
        self.other_model.delete()
        result=apply(self.user,[self.row()]);self.assertTrue(result['warnings']);self.unit.refresh_from_db();self.assertEqual(self.unit.type_id,self.model.pk)
        result=apply(self.user,[self.row(**{'техника для планирования':'АПС','группа техники':'Неизвестная'})]);self.assertTrue(result['warnings']);self.unit.refresh_from_db();self.assertIsNone(self.unit.catalogue_group_id)
    def test_group_override_does_not_change_other_machines_or_shared_types(self):
        from .equipment_group_import import apply
        apply(self.user,[self.row(**{'техника для планирования':'АПС'})]);self.root.refresh_from_db();self.second.refresh_from_db();self.unit.refresh_from_db()
        self.assertEqual(self.root.category_id,self.a.pk);self.assertIsNone(self.second.catalogue_group_id);self.assertEqual(self.unit.catalogue_group_id,self.b.pk)
        from .equipment_unit_catalogue import unit_queryset,UnitFilterForm,filter_units
        form=UnitFilterForm({'category':self.b.pk},company=self.company);self.assertTrue(form.is_valid())
        self.assertEqual(list(filter_units(unit_queryset(self.company),form).values_list('pk',flat=True)),[self.unit.pk])
    def test_identifiers_must_agree_and_foreign_id_cannot_fall_back(self):
        from .equipment_group_import import apply
        result=apply(self.user,[self.row(vin='wrong')]);self.assertEqual(result['updated'],0);self.assertTrue(result['warnings'])
    def test_quick_create_is_scoped_and_rejects_duplicate_or_foreign_group(self):
        self.client.force_login(self.user);url=reverse('resources:equipment_type_quick_create')
        response=self.client.post(url,{'name':'Новый корень','category':self.a.pk,'unit':'ед.'});self.assertEqual(response.status_code,201);self.assertIsNone(EquipmentType.objects.get(pk=response.json()['value']).parent_id)
        self.assertEqual(self.client.post(url,{'name':'Новый корень','unit':'ед.'}).status_code,400)
        foreign=EquipmentCategory.objects.create(company=Company.objects.create(name='Foreign groups'),name='Other')
        self.assertEqual(self.client.post(url,{'name':'Other','category':foreign.pk,'unit':'ед.'}).status_code,400)


import os,json,shutil,subprocess
from pathlib import Path
from unittest import skipUnless
from django.contrib.staticfiles.testing import StaticLiveServerTestCase


@skipUnless(os.environ.get('CATALOGUE_CHANGES_BROWSER_TESTS')=='1','Optional catalogue and inline creation browser scenario')
class CatalogueChangesBrowserTests(StaticLiveServerTestCase):
    serialized_rollback=True
    def test_tree_table_and_inline_creation_preserve_form(self):
        EquipmentGroupImportTests.setUpTestData.__func__(type(self))
        group=BrigadeGroup.objects.create(company=self.company,name='ИТР')
        Brigade.objects.create(company=self.company,name='Инженер',group=group)
        Brigade.objects.create(company=self.company,name='Рабочий')
        self.client.force_login(self.user)
        config={'base':self.live_server_url,'cookie':self.client.cookies['sessionid'].value,'chromium':shutil.which('chromium'),
            'positions':reverse('resources:brigade_list'),'unit_create':reverse('resources:equipment_unit_create'),
            'model_edit':reverse('resources:equipment_type_update',args=[self.model.pk])}
        result=subprocess.run(['node',str(Path(__file__).resolve().parents[2]/'tests/browser/catalogue_changes.js')],capture_output=True,text=True,env={**os.environ,'CATALOGUE_CHANGES_CONFIG':json.dumps(config)},timeout=100)
        self.assertEqual(result.returncode,0,result.stdout+'\n'+result.stderr)
        saved=Equipment.objects.get(plate_number='NEW-001');self.assertEqual(saved.name,'Ввод сохранён');self.assertEqual(saved.catalogue_group.name,'Группа из окна')
        print(result.stdout.strip())
