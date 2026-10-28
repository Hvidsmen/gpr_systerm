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
        self.save('condition','REPAIR');self.assertFalse(self.machine.transport_days.exists())
        with self.assertRaises(ValidationError):self.save('status','WORKED')
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
        self.assertTrue(response.json()['saved'])
        response=self.client.post(reverse('production:transport_status_create'),{'kind':'PARTS','name':'На складе участка'},content_type='application/json')
        self.assertEqual(response.status_code,200)
        self.save('parts_stage',response.json()['id'])
        page=self.client.get(reverse('production:transport_board'),{'camp':self.camp.pk,'date':str(self.day)})
        self.assertContains(page,'data-detail-field="parts_stage"');self.assertContains(page,'data-detail-field="repair_started_on"');self.assertNotContains(page,'>Сведения</a>')

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
