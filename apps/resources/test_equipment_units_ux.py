from datetime import date
from io import BytesIO
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from openpyxl import load_workbook
from apps.accounts.models import Company, User, Role
from apps.projects.models import Project, ConstructionObject
from apps.production.equipment_operations_models import EquipmentAssignment, EquipmentStateChange
from .models import Equipment, EquipmentType, EquipmentCategory
from .equipment_units import UnitForm


class EquipmentUnitsUXTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.company=Company.objects.create(name='Machine UX')
        cls.foreign=Company.objects.create(name='Foreign machines')
        cls.user=User.objects.create_user(username='machine-ux-planner',company=cls.company,role=Role.objects.get(code='PLANNER'))
        cls.group=EquipmentCategory.objects.create(company=cls.company,name='Специализированная техника')
        cls.root=EquipmentType.objects.create(company=cls.company,name='Экскаватор',category=cls.group)
        cls.model=EquipmentType.objects.create(company=cls.company,name='Экскаватор / CAT 320',brand_model='CAT 320',parent=cls.root)
        cls.other=EquipmentType.objects.create(company=cls.company,name='Кран')
        cls.other_model=EquipmentType.objects.create(company=cls.company,name='Кран / X',parent=cls.other)
        cls.machine=Equipment.objects.create(company=cls.company,type=cls.model,name='Экскаватор первый',plate_number='А001АА',inventory_number='0001',vin='VIN-001',site_name='Дудинка')
        cls.plain=Equipment.objects.create(company=cls.company,type=cls.root,name='Экскаватор второй',plate_number='Б002ББ',inventory_number='0002',vin='VIN-002',is_active=False)
        project=Project.objects.create(company=cls.company,name='Project')
        cls.obj=ConstructionObject.objects.create(company=cls.company,project=project,name='КТП')
        cls.user.assigned_objects.add(cls.obj)
        cls.assignment=EquipmentAssignment.objects.create(company=cls.company,equipment=cls.machine,construction_object=cls.obj,start_date=date(2026,1,1),shift=1,activity='Работа',general=True)
        EquipmentStateChange.objects.create(company=cls.company,equipment=cls.machine,assignment=cls.assignment,effective_date=date(2026,1,1),state='WORKING',shift=1)
        EquipmentStateChange.objects.create(company=cls.company,equipment=cls.machine,assignment=cls.assignment,effective_date=date(2026,1,5),state='REPAIR',reason='Ремонт',shift=1)
        EquipmentStateChange.objects.create(company=cls.company,equipment=cls.machine,assignment=cls.assignment,effective_date=date(2026,1,9),state='IDLE',reason='Будущее',shift=1)
        EquipmentStateChange.objects.create(company=cls.company,equipment=cls.machine,assignment=cls.assignment,effective_date=date(2026,1,6),state='WORKING',shift=1,is_cancelled=True)
        foreign_type=EquipmentType.objects.create(company=cls.foreign,name='Чужая техника')
        cls.foreign_unit=Equipment.objects.create(company=cls.foreign,type=foreign_type,name='Чужая машина',plate_number='FOREIGN')

    def setUp(self):
        self.client.force_login(self.user)
        self.url=reverse('resources:equipment_unit_list')

    def response(self, **params):
        return self.client.get(self.url, {'as_of':'2026-01-07', **params})

    def test_identifier_search_case_and_words_and_company_scope(self):
        for q in ['а001аа','0001','vin-001','ЭКСКАВАТОР первый','CAT 320']:
            response=self.response(q=q)
            self.assertEqual([unit.pk for unit in response.context['units']],[self.machine.pk],q)
        response=self.response()
        self.assertEqual(response.context['counts']['total'],2)
        self.assertNotContains(response,'FOREIGN')
        self.assertNotContains(response,'data-machine-form')

    def test_recorded_state_as_of_date_ignores_future_and_cancelled_and_is_separate_from_availability(self):
        response=self.response()
        by_id={row.pk:row for row in response.context['units']}
        self.assertEqual(by_id[self.machine.pk].unit_state,'REPAIR')
        self.assertTrue(by_id[self.machine.pk].is_active)
        self.assertEqual(by_id[self.plain.pk].unit_state,'UNKNOWN')
        self.assertFalse(by_id[self.plain.pk].is_active)
        self.assertEqual(self.response(as_of='2026-01-03').context['units'][0].unit_state,'WORKING')
        self.assertEqual([row.pk for row in self.response(state='UNKNOWN').context['units']],[self.plain.pk])
        self.assertEqual(self.response(state='REPAIR',is_active='False').context['counts']['total'],0)
        self.assignment.cancelled_at=timezone.now();self.assignment.save(update_fields=['cancelled_at'])
        self.assertEqual(self.response(state='REPAIR').context['counts']['total'],0)

    def test_parent_model_group_and_site_filters_and_invalid_inputs(self):
        self.assertEqual(self.response(parent=self.root.pk).context['counts']['total'],2)
        self.assertEqual(self.response(model=self.model.pk,category=self.group.pk,site='ДУДИНКА').context['counts']['total'],1)
        self.assertEqual(self.response(parent=self.other.pk,model=self.model.pk).context['counts']['total'],0)
        response=self.response(parent=self.foreign_unit.type_id)
        self.assertIn('parent',response.context['filter_form'].errors)
        self.assertEqual(response.context['counts']['total'],0)
        self.assertEqual(self.response(state='invalid',export='xlsx').status_code,400)
        self.assertEqual(self.response(as_of='invalid',export='xlsx').status_code,400)

    def test_filtered_excel_includes_all_pages_and_native_dates_and_identifiers(self):
        Equipment.objects.bulk_create([Equipment(company=self.company,type=self.root,name='Экскаватор',inventory_number=f'EX-{i:03}') for i in range(27)])
        response=self.response(parent=self.root.pk,page=2)
        self.assertEqual(len(response.context['units']),4)
        self.assertContains(response,'parent='+str(self.root.pk))
        exported=self.response(parent=self.root.pk,export='xlsx',page=2)
        workbook=load_workbook(BytesIO(exported.content));rows=list(workbook.active.values)
        self.assertEqual(len(rows),30)
        values=[dict(zip(rows[0],row)) for row in rows[1:]]
        machine=next(row for row in values if row['Госномер']=='А001АА')
        self.assertEqual(machine['Инвентарный номер'],'0001')
        self.assertEqual(machine['Последнее состояние'],'В ремонте')
        self.assertEqual(machine['Дата состояния'].date(),date(2026,1,5))
        self.assertTrue(all(row['Техника для планирования']=='Экскаватор' for row in values))
        workbook.close()

    def test_create_prefills_model_and_saves_explicit_model_identity(self):
        url=reverse('resources:equipment_unit_create')
        response=self.client.get(url,{'model':self.model.pk})
        form=response.context['form']
        self.assertEqual(form['type'].value(),self.root.pk)
        self.assertEqual(form['model'].value(),self.model.pk)
        self.assertEqual(form['brand_model'].value(),'CAT 320')
        response=self.client.post(url,{'type':self.root.pk,'model':self.model.pk,'name':'Новая машина','vin':'NEW-VIN','is_active':'on'})
        self.assertEqual(response.status_code,302)
        unit=Equipment.objects.get(vin='NEW-VIN')
        self.assertEqual(unit.type_id,self.model.pk)
        self.assertIn(reverse('resources:equipment_unit_detail',args=[unit.pk]),response.url)

    def test_cross_model_company_duplicates_and_missing_identifiers_rejected_atomically(self):
        url=reverse('resources:equipment_unit_create')
        base={'type':self.root.pk,'name':'Не сохранять','vin':'NEW-VIN','model':''}
        for extra in [{'model':self.other_model.pk},{'type':self.foreign_unit.type_id},{'vin':''},{'plate_number':self.machine.plate_number}]:
            response=self.client.post(url,{**base,**extra})
            self.assertEqual(response.status_code,400,response.content[:100])
            self.assertFalse(Equipment.objects.filter(name='Не сохранять').exists())

    def test_existing_model_and_operating_history_preserved_on_edits(self):
        data={'type':self.root.pk,'name':self.machine.name,'plate_number':self.machine.plate_number,'inventory_number':self.machine.inventory_number,'vin':self.machine.vin,'is_active':'on'}
        legacy=UnitForm(data,instance=self.machine,company=self.company)
        self.assertTrue(legacy.is_valid(),legacy.errors)
        self.assertEqual(legacy.save().type_id,self.model.pk)
        self.machine.refresh_from_db()
        bad=UnitForm({**data,'model':''},instance=self.machine,company=self.company)
        self.assertFalse(bad.is_valid())
        self.machine.refresh_from_db();self.assertEqual(self.machine.type_id,self.model.pk)
        changed=UnitForm({**data,'model':self.model.pk,'plate_number':'OTHER'},instance=self.machine,company=self.company)
        self.assertFalse(changed.is_valid())

    def test_catalogue_machine_counts_and_contextual_add_actions(self):
        response=self.client.get(reverse('resources:equipment_type_list'),{'parent':self.root.pk})
        rows={row.pk:row for row in response.context['equipment_types']}
        self.assertEqual(rows[self.root.pk].machine_count,2)
        self.assertEqual(rows[self.model.pk].machine_count,1)
        self.assertContains(response,reverse('resources:equipment_unit_create')+'?model='+str(self.model.pk))
        self.assertContains(response,'Машины с номерами')
        response=self.client.get(reverse('resources:equipment_type_update',args=[self.model.pk]))
        self.assertEqual(response.context['linked_machine_count'],1)

    def test_model_creation_requires_parent_and_main_menu_has_all_three_choices(self):
        url=reverse('resources:equipment_type_create')+'?kind=model'
        response=self.client.get(url)
        self.assertTrue(response.context['form'].fields['parent'].required)
        response=self.client.post(url,{'name':'Новая модель','is_active':'on','crew-TOTAL_FORMS':0,'crew-INITIAL_FORMS':0})
        self.assertEqual(response.status_code,200)
        self.assertIn('parent',response.context['form'].errors)
        self.assertFalse(EquipmentType.objects.filter(name='Новая модель').exists())
        self.assertContains(self.response(),'Машину с номерами')
        self.assertContains(self.response(),'Технику для планирования')

    def test_detail_read_only_permissions_foreign_access_and_safe_return_url(self):
        url=reverse('resources:equipment_unit_detail',args=[self.machine.pk])
        response=self.client.get(url,{'return_query':'q=VIN-001&next=https://example.invalid&page=2'})
        self.assertEqual(response.context['unit_list_url'],self.url+'?q=VIN-001&page=2')
        self.assertContains(response,'Открыть назначения и историю состояний')
        self.assertEqual(self.client.get(reverse('resources:equipment_unit_detail',args=[self.foreign_unit.pk])).status_code,404)
        self.user.role=Role.objects.get(code='OBSERVER');self.user.save(update_fields=['role'])
        self.assertEqual(self.client.get(url).status_code,200)
        self.assertNotContains(self.response(),'Изменить машину')
        self.assertEqual(self.client.get(reverse('resources:equipment_unit_create')).status_code,403)
        self.assertEqual(self.client.post(reverse('resources:equipment_unit_update',args=[self.machine.pk]),{}).status_code,403)
