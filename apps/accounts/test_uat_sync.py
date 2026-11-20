from datetime import datetime, time, timezone as dt_timezone
from decimal import Decimal

from django.test import TestCase, override_settings
from django.urls import reverse

from apps.production.models import EquipmentFact
from apps.personnel.models import Camp
from apps.projects.forms import ConstructionObjectForm
from apps.projects.models import ConstructionObject, Project
from apps.resources.models import Employee, Equipment, EquipmentType

from .models import (
    Company, OneCConnection, OneCDiagnosticRun, Role, UatDepartment,
    UatRepairSheet, UatVehicle, UatWaybill, User,
)
from .uat_sync import apply_uat_report, next_scheduled_slot, rebuild_facts, scheduled_slot


@override_settings(UAT_DATABASE='uat01_ivanov', TIME_ZONE='Europe/Moscow')
class UatSynchronizationTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(name='UAT sync')
        self.role, _ = Role.objects.get_or_create(code='ADMIN', defaults={'name': 'Admin'})
        self.admin = User.objects.create_user(
            username='uat-sync-admin', company=self.company, role=self.role,
        )
        self.connection = OneCConnection.objects.create(
            company=self.company, server='msk-db', database='zup',
        )
        self.run = OneCDiagnosticRun.objects.create(
            connection=self.connection, created_by=self.admin, server='msk-db',
            database='uat01_ivanov', status='RUNNING', report={'uat_import': True},
        )
        self.project = Project.objects.create(company=self.company, name='КТП')
        self.obj = ConstructionObject.objects.create(
            company=self.company, project=self.project, name='КТП-1 Нефтепровод',
        )
        self.camp = Camp.objects.create(company=self.company, name='ВЖГ КТП-1')
        self.kind = EquipmentType.objects.create(
            company=self.company, name='Самосвал', brand_model='Камаз',
        )
        self.machine = Equipment.objects.create(
            company=self.company, type=self.kind, name='Камаз 6520',
            plate_number='А090АА00', vin='VIN-EXACT-001', inventory_number='GAR-1',
        )
        self.driver = Employee.objects.create(
            company=self.company, zup_ref='1' * 32, zup_full_name='Иванов Иван Иванович',
            employee_number='000123', is_active=True,
        )

    def report(self):
        return {
            'uat_import': True, 'from_date': '2026-11-13', 'to_date': '2026-11-20',
            'vehicles': [
                {'source_id': 'V1', 'name': 'Камаз УАТ', 'plate': 'A090AA00', 'vin': 'VIN EXACT 001', 'garage_number': 'GAR-1'},
                {'source_id': 'V2', 'name': 'Новая машина', 'plate': 'М777ММ77', 'vin': 'NEW-VIN-2', 'garage_number': 'NEW-2'},
            ],
            'departments': [{'source_id': 'D1', 'code': '001', 'name': 'УМиТ КТП-1'}],
            'waybills': [{
                'source_id': 'W1', 'number': 'PL-1', 'document_date': '2026-11-20', 'posted': 1,
                'vehicle_ref': 'V1', 'vehicle': 'Камаз УАТ', 'plate': 'A090AA00',
                'driver_1_ref': 'DR1', 'driver_1_number': '000123', 'driver_1': 'Иванов Иван Иванович',
                'driver_2_ref': 'DR2', 'driver_2_number': '999', 'driver_2': 'Неизвестный Водитель',
                'department_ref': 'D1', 'department': 'УМиТ КТП-1',
                'object_grandparent': 'Проект Ямал', 'object_parent': 'Участок КТП',
                'construction_object': 'Мостовые переходы',
            }],
            'waybill_tasks': [{
                'parent_id': 'W1', 'line_number': 1, 'department_ref': 'D1',
                'department': 'УМиТ КТП-1', 'object_grandparent': 'Проект Ямал',
                'object_parent': 'Участок КТП', 'construction_object': 'Мостовые переходы',
                'hours': '7.125', 'trips': 2, 'loaded_km': 10, 'empty_km': 4, 'tonnes': 15,
            }],
            'repair_sheets': [{
                'source_id': 'R1', 'number': 'RL-1', 'document_date': '2026-11-20',
                'posted': 1, 'completed': 0, 'vehicle_ref': 'V2', 'vehicle': 'Новая машина',
                'department_ref': 'D1', 'department': 'УМиТ КТП-1', 'service_kind': 'ТО-1',
            }],
            'repair_works': [{'parent_id': 'R1', 'work': 'Замена масла'}],
            'repair_materials': [], 'repair_executors': [],
        }

    def test_snapshot_matches_creates_and_builds_fact_idempotently(self):
        result = apply_uat_report(self.run, self.report())
        matched = UatVehicle.objects.get(source_id='V1')
        created = UatVehicle.objects.get(source_id='V2')
        self.assertEqual((matched.equipment, matched.match_basis), (self.machine, 'VIN'))
        self.assertEqual(created.match_status, 'CREATED')
        self.assertEqual(created.equipment.type.name, 'Не классифицировано — УАТ')
        bill = UatWaybill.objects.get(source_id='W1')
        self.assertEqual((bill.driver_1, bill.driver_1_status), (self.driver, 'MATCHED'))
        self.assertEqual((bill.driver_2, bill.driver_2_status), (None, 'NOT_FOUND'))
        self.assertEqual(bill.fact_status, 'PENDING')
        self.assertEqual(result['pending'], 1)
        department = UatDepartment.objects.get(source_id='D1')
        department.construction_object = self.obj
        department.save(update_fields=['construction_object'])
        rebuild_facts(self.company)
        bill.refresh_from_db()
        self.assertEqual(bill.fact_status, 'PENDING')
        self.assertIn('местонахождением', bill.fact_message)
        self.assertFalse(EquipmentFact.objects.exists())
        department.camp = self.camp
        department.save(update_fields=['camp'])
        rebuild_facts(self.company)
        bill.refresh_from_db()
        fact = EquipmentFact.objects.get()
        self.assertEqual(bill.fact_status, 'CREATED')
        self.assertEqual(fact.machine_hours, Decimal('7.13'))
        self.assertIn('Иванов Иван', fact.comment)
        apply_uat_report(self.run, self.report())
        self.assertEqual(UatWaybill.objects.count(), 1)
        self.assertEqual(UatRepairSheet.objects.count(), 1)
        self.assertEqual(UatVehicle.objects.count(), 2)
        self.assertEqual(EquipmentFact.objects.count(), 1)
        limited = self.report()
        limited.update({'limited': True, 'waybills': [], 'waybill_tasks': [], 'repair_sheets': []})
        apply_uat_report(self.run, limited)
        self.assertTrue(UatWaybill.objects.get(source_id='W1').is_active)
        self.assertTrue(UatRepairSheet.objects.get(source_id='R1').is_active)

    def test_manual_fact_is_not_overwritten(self):
        apply_uat_report(self.run, self.report())
        department = UatDepartment.objects.get(source_id='D1')
        department.construction_object = self.obj
        department.camp = self.camp
        department.save(update_fields=['construction_object', 'camp'])
        EquipmentFact.objects.create(
            company=self.company, construction_object=self.obj, date='2026-11-20',
            equipment_type=self.kind, equipment_number='А090АА00', planned_count=0,
            actual_count=1, machine_hours=Decimal('3.00'), comment='Ручной факт',
        )
        rebuild_facts(self.company)
        bill = UatWaybill.objects.get(source_id='W1')
        self.assertEqual(bill.fact_status, 'CONFLICT')
        self.assertEqual(EquipmentFact.objects.get().comment, 'Ручной факт')

    def test_construction_object_form_assigns_many_departments(self):
        first = UatDepartment.objects.create(company=self.company, source_id='D1', name='Первое')
        second = UatDepartment.objects.create(company=self.company, source_id='D2', name='Второе')
        form = ConstructionObjectForm(
            data={'name': self.obj.name, 'parent': '', 'uat_departments': [first.pk, second.pk]},
            instance=self.obj,
        )
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        self.assertEqual(set(self.obj.uat_departments.values_list('pk', flat=True)), {first.pk, second.pk})

    def test_pages_are_searchable_and_readable_by_mechanization_dispatcher(self):
        apply_uat_report(self.run, self.report())
        self.client.force_login(self.admin)
        response = self.client.get(reverse('accounts:uat_waybills'), {'q': 'PL-1', 'from': '2026-11-01', 'to': '2026-11-30'})
        self.assertContains(response, 'PL-1')
        self.assertContains(response, 'Сотрудник не найден')
        department = UatDepartment.objects.get(source_id='D1')
        mapping_url = reverse('accounts:uat_departments') + f'?focus={department.pk}#uat-department-{department.pk}'
        self.assertContains(response, mapping_url)
        focused = self.client.get(reverse('accounts:uat_departments'), {'focus': department.pk})
        self.assertEqual(focused.context['page'].paginator.count, 1)
        self.assertContains(focused, 'Открыто подразделение из документа УАТ')
        saved = self.client.post(reverse('accounts:uat_departments'), {
            'source': department.pk,
            'construction_object': self.obj.pk,
            'camp': self.camp.pk,
        })
        self.assertRedirects(saved, mapping_url, fetch_redirect_response=False)
        department.refresh_from_db()
        self.assertEqual((department.construction_object, department.camp), (self.obj, self.camp))
        self.assertContains(self.client.get(reverse('accounts:uat_repairs'), {'q': 'RL-1', 'from': '2026-11-01', 'to': '2026-11-30'}), 'RL-1')
        self.assertContains(self.client.get(reverse('accounts:uat_journal')), 'Следующий запуск')
        dispatcher_role, _ = Role.objects.get_or_create(code='MECHANIZATION_DISPATCHER', defaults={'name': 'Механизация'})
        self.admin.role = dispatcher_role
        self.admin.save(update_fields=['role'])
        self.assertEqual(self.client.get(reverse('accounts:uat_waybills')).status_code, 200)
        self.assertEqual(self.client.post(reverse('accounts:uat_departments'), {'source': 1}).status_code, 403)

    def test_waybills_can_be_filtered_by_header_or_task_department(self):
        apply_uat_report(self.run, self.report())
        first = UatDepartment.objects.get(source_id='D1')
        second = UatDepartment.objects.create(
            company=self.company, source_id='D2', code='002', name='УМиТ КТП-2',
        )
        bill = UatWaybill.objects.get(source_id='W1')
        bill.tasks.update(department=second, department_name=second.name)
        UatWaybill.objects.create(
            company=self.company, source_id='W2', number='PL-2',
            document_date='2026-11-20', department=first, department_name=first.name,
        )
        self.client.force_login(self.admin)
        url = reverse('accounts:uat_waybills')
        period = {'from': '2026-11-01', 'to': '2026-11-30'}
        by_task = self.client.get(url, {**period, 'department': second.pk})
        self.assertEqual(by_task.context['page'].paginator.count, 1)
        self.assertContains(by_task, 'PL-1')
        self.assertNotContains(by_task, 'PL-2')
        self.assertContains(by_task, f'value="{second.pk}" selected')
        by_header = self.client.get(url, {**period, 'department': first.pk})
        self.assertEqual(by_header.context['page'].paginator.count, 2)
        self.assertContains(by_header, 'PL-1')
        self.assertContains(by_header, 'PL-2')
        unfiltered = self.client.get(url, period)
        self.assertContains(unfiltered, f'value="{first.pk}"')
        self.assertContains(unfiltered, f'value="{second.pk}"')

    def test_daily_clock_uses_moscow_time(self):
        self.connection.uat_sync_time = time(1, 0)
        now = datetime(2026, 11, 20, 0, 30, tzinfo=dt_timezone.utc)
        self.assertEqual(scheduled_slot(self.connection, now).isoformat(), '2026-11-20T01:00:00+03:00')
        self.assertEqual(next_scheduled_slot(self.connection, now).isoformat(), '2026-11-21T01:00:00+03:00')
