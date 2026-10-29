from datetime import timedelta
from uuid import uuid4
from django.test import TestCase
from django.urls import reverse
from django.core.exceptions import ValidationError
from apps.resources.models import Employee
from .test_transport import TransportTests
from . import transport
from .transport_inline import save_cell,ensure_statuses
from .transport_details_models import EquipmentDayDetails,EquipmentServiceStatus
from .transport_detail_forms import DetailsForm
from .transport_detail_values import parse_date,from_source
from .history_models import FactRevision


class TransportInlineTests(TestCase):
    @classmethod
    def setUpTestData(cls):TransportTests.setUpTestData.__func__(cls)
    def setUp(self):
        transport.move(self.operator,self.camp,self.day,[self.machine.pk],'ARRIVAL',signed=transport.snapshot(self.operator,self.camp,self.day),condition='READY')
        self.session=str(uuid4());self.client.force_login(self.operator)
    def save(self,field,value,signed=None):return save_cell(self.operator,self.camp,self.day,self.machine,field,value,signed or transport.snapshot(self.operator,self.camp,self.day),self.session)
    def test_state_use_and_typed_details_save_with_history(self):
        self.save('status','WORKED');self.save('repair_started_on','2026-10-20')
        row=EquipmentDayDetails.objects.get(equipment=self.machine)
        self.assertEqual(str(row.repair_started_on),'2026-10-20')
        self.assertTrue(FactRevision.objects.filter(entity_type='production.equipmentdaydetails',actor=self.operator).exists())
        self.save('condition','REPAIR');self.assertEqual(self.machine.transport_days.get(date=self.day).status,'WORKED')
        self.save('status','WORKED');self.assertEqual(transport.condition_on(self.machine,self.day),'REPAIR')
    def test_stale_version_foreign_options_invalid_dates_and_leases_reject(self):
        stale=transport.snapshot(self.operator,self.camp,self.day);self.save('defects','Уточнение')
        with self.assertRaises(ValidationError):self.save('comment','Потеря',signed=stale)
        with self.assertRaises(ValidationError):self.save('parts_expected_on','2026-02-31')
        with self.assertRaises(ValidationError):self.save('shift_one_employee',999999)
        transport.claim(self.colleague,self.camp,self.day,[self.machine.pk],'acquire',str(uuid4()))
        with self.assertRaises(ValidationError):self.save('comment','Без блокировки')
    def test_fields_are_dates_employees_and_scoped_statuses(self):
        ensure_statuses(self.company);form=DetailsForm(company=self.company)
        self.assertEqual(form.fields['repair_started_on'].widget.input_type,'date')
        self.assertEqual(form.fields['parts_expected_on'].widget.input_type,'date')
        self.assertEqual(form.fields['shift_one_employee'].queryset.model,Employee)
        self.assertEqual(set(form.fields['parts_stage'].queryset.values_list('kind',flat=True)),{'PARTS'})
        self.assertIsNone(parse_date('12.05.26 или 13.05.26'))
        self.assertEqual(str(parse_date('30.09.25 г.')),'2025-09-30')
    def test_inline_api_quick_status_and_board_do_not_navigate(self):
        url=reverse('production:transport_cell');data={'camp':self.camp.pk,'date':str(self.day),'equipment':self.machine.pk,'field':'defects','value':'Поломка','signed':transport.snapshot(self.operator,self.camp,self.day),'session':self.session}
        response=self.client.post(url,data,content_type='application/json');self.assertEqual(response.status_code,200)
        self.assertTrue(response.json()['saved']);self.assertEqual(response.json()['condition_label'],'Исправна')
        response=self.client.post(reverse('production:transport_status_create'),{'kind':'PARTS','name':'На складе участка'},content_type='application/json')
        self.assertEqual(response.status_code,200)
        self.save('parts_stage',response.json()['id'])
        page=self.client.get(reverse('production:transport_board'),{'camp':self.camp.pk,'date':str(self.day),'view':'list'})
        self.assertContains(page,'data-detail-field="parts_stage"');self.assertContains(page,'data-detail-field="repair_started_on"');self.assertNotContains(page,'>Сведения</a>')

    def test_work_and_maintenance_have_separate_fields_and_repair_dialog(self):
        scope={'camp':self.camp.pk,'date':str(self.day),'view':'list'}
        page=self.client.get(reverse('production:transport_board'),scope)
        self.assertEqual([field.name for field in page.context['rows'][0]['visible_details']],['shift_one_employee','shift_two_employee','in_gpr'])
        self.assertContains(page,'data-open-repair');self.assertContains(page,'data-repair-dialog')
        self.assertNotContains(page,'<th>Неисправности и поломки</th>')
        state=self.client.get(reverse('production:transport_board'),{**scope,'tab':'state'})
        self.assertEqual([field.name for field in state.context['rows'][0]['visible_details']],['in_gpr'])
        self.assertNotContains(state,'data-detail-field="status"');self.assertNotContains(state,'data-detail-field="shift_one_employee"')
        self.assertContains(state,'Заявки и запчасти');self.assertContains(state,'ТО и примечания')
        self.assertNotContains(state,'name="use"');self.assertContains(state,'data-transport-count="READY"')
        self.assertNotContains(state,'data-transport-count="WORKED"')
        self.assertContains(state,'data-detail-field="condition"',count=1)
        self.assertNotContains(state,'data-transport-action="CONDITION"')
        self.assertNotContains(state,'data-edit-machine=')
        self.assertContains(page,'data-detail-field="condition"',count=1)
        for name in ['status', 'target', 'day_comment', 'shift_one_employee', 'shift_two_employee', 'in_gpr']:
            self.assertNotContains(page, f'data-detail-field="{name}"')
        self.assertNotContains(page, 'data-edit-machine=')
        self.assertNotContains(page, 'data-transport-action="DAY"')
        self.assertNotContains(page, 'data-copy-previous="equipment"')
        from unittest.mock import patch
        with patch('core.fact_edit_window.timezone.localdate',return_value=self.day+timedelta(days=3)):
            readonly=self.client.get(reverse('production:transport_board'),{**scope,'tab':'state'})
        self.assertContains(readonly,'data-repair-dialog');self.assertFalse(readonly.context['editable'])
        self.assertNotContains(readonly,'data-detail-field="condition"')
        self.assertTrue(all(field.disabled for field in readonly.context['rows'][0]['details_form'].fields.values()))

    def test_list_shows_daily_facts_as_reference_and_condition_change_preserves_them(self):
        driver = Employee.objects.create(company=self.company, last_name='Справочный', first_name='Водитель')
        self.save('status', 'WORKED')
        self.save('shift_one_employee', driver.pk)
        self.save('day_comment', 'Работала утром')
        self.save('in_gpr', 'true')
        self.save('condition', 'REPAIR')
        scope = {'camp': self.camp.pk, 'date': str(self.day), 'view': 'list'}
        page = self.client.get(reverse('production:transport_board'), scope)
        self.assertContains(page, 'Справочный Водитель')
        self.assertContains(page, 'Работала утром')
        self.assertContains(page, '<span data-transport-reference>Да</span>', html=True)
        self.assertContains(page, 'data-detail-field="condition"', count=1)
        self.assertNotContains(page, 'data-detail-field="status"')
        self.assertEqual(self.machine.transport_days.get(date=self.day).status, 'WORKED')
        self.assertEqual(EquipmentDayDetails.objects.get(equipment=self.machine, date=self.day).shift_one_employee, driver)

    def test_entering_repair_sets_editable_date_and_repeated_state_preserves_correction(self):
        self.save('defects', 'Проверить двигатель')
        self.save('condition', 'REPAIR')
        detail = EquipmentDayDetails.objects.get(equipment=self.machine, date=self.day)
        self.assertEqual(detail.repair_started_on, self.day)
        self.assertEqual(detail.defects, 'Проверить двигатель')
        corrected = self.day - timedelta(days=1)
        self.save('repair_started_on', str(corrected))
        self.save('condition', 'REPAIR')
        detail.refresh_from_db()
        self.assertEqual(detail.repair_started_on, corrected)
        self.save('condition', 'READY')
        self.save('condition', 'REPAIR')
        detail.refresh_from_db()
        self.assertEqual(detail.repair_started_on, self.day)
        self.assertFalse(self.machine.transport_days.exists())

    def test_drivers_are_daily_and_technical_details_carry_forward_without_marking_work(self):
        from apps.resources.models import Employee
        driver=Employee.objects.create(company=self.company,last_name='Водитель',first_name='Тест')
        with self.assertRaises(ValidationError):self.save('shift_one_employee',driver.pk)
        self.save('status','WORKED');self.save('shift_one_employee',driver.pk)
        self.save('defects','Контроль датчика');self.save('request_details','Заявка № 12');self.save('condition','REPAIR')
        self.assertEqual(self.machine.transport_days.get(date=self.day).status,'WORKED')
        detail=EquipmentDayDetails.objects.get(equipment=self.machine,date=self.day);self.assertEqual(detail.shift_one_employee,driver)
        yesterday=self.day-timedelta(days=1)
        # Move this detail to yesterday to exercise a new day's carry-forward.
        EquipmentDayDetails.objects.filter(pk=detail.pk).update(date=yesterday,source_details={'shift_one':driver.display_name,'repair_status':'Контроль'})
        self.machine.transport_days.all().delete()
        page=self.client.get(reverse('production:transport_board'),{'camp':self.camp.pk,'date':str(self.day),'view':'list'})
        self.assertIsNone(page.context['rows'][0]['details_form']['shift_one_employee'].value())
        self.assertEqual(page.context['rows'][0]['details_form']['defects'].value(),'Контроль датчика')
        self.save('comment','Проверка механика')
        today=EquipmentDayDetails.objects.get(equipment=self.machine,date=self.day)
        self.assertIsNone(today.shift_one_employee);self.assertEqual(today.request_details,'Заявка № 12')
        self.assertNotIn('shift_one',today.source_details);self.assertFalse(self.machine.transport_days.exists())

    def test_legacy_details_data_migration_preserves_sources_and_types_dates(self):
        from importlib import import_module
        from django.db import connection
        from django.db.migrations.executor import MigrationExecutor
        from datetime import date
        employee=Employee.objects.create(company=self.company,last_name='Миграция',first_name='Водитель')
        row=EquipmentDayDetails.objects.create(company=self.company,equipment=self.machine,camp=self.camp,date=self.day,
            shift_one=employee.display_name,shift_two='Неоднозначное имя',repair_date='21.10.26 г.',
            expected_parts_date='31.02.26',request_details='№ 21 от 20.10.2026',parts_status='Запчасти поступили',repair_status='В ремонте')
        executor=MigrationExecutor(connection)
        historical=executor.loader.project_state([('production','0024_structured_equipment_details')]).apps
        migration=import_module('apps.production.migrations.0024_structured_equipment_details')
        # Exercise the actual historical-model conversion on existing rows, without
        # changing schema or touching the development database.
        migration.seed_and_convert(historical,connection.schema_editor())
        row.refresh_from_db()
        self.assertEqual(row.repair_started_on,date(2026,10,21))
        self.assertIsNone(row.parts_expected_on)
        self.assertEqual(row.request_date,date(2026,10,20))
        self.assertEqual(row.shift_one_employee,employee)
        self.assertIsNone(row.shift_two_employee)
        self.assertEqual(row.parts_stage.name,'Доставлены')
        self.assertEqual(row.source_details['expected_parts_date'],'31.02.26')
        self.assertEqual(row.repair_date,'21.10.26 г.')


    def test_lazy_roster_keeps_inactive_saved_status_and_current_driver(self):
        from .transport_inline import prepare_rows
        from .transport_views import _rows
        driver=Employee.objects.create(company=self.company,last_name='Водитель',first_name='Смены')
        self.save('status','WORKED');self.save('shift_one_employee',driver.pk)
        status=EquipmentServiceStatus.objects.create(company=self.company,kind='REQUEST',name='Прежний статус')
        self.save('request_stage',status.pk);status.is_active=False;status.save()
        rows=_rows(self.operator,self.camp,self.day);prepare_rows(self.operator,self.camp,self.day,rows,True)
        form=rows[0]['details_form']
        self.assertIn((status.pk,status.name),list(form.fields['request_stage'].choices))
        self.assertEqual(list(form.fields['shift_one_employee'].choices)[1][0],driver.pk)
        self.assertEqual(str(form['shift_one_employee'].value()),str(driver.pk))
