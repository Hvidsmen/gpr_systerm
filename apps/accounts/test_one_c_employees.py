from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import json
from django.test import TestCase, override_settings
from django.urls import reverse
from django.core.exceptions import ValidationError
from apps.resources.models import Employee, Position
from .models import Company, User, Role, OneCConnection, OneCDiagnosticRun
from .one_c import enqueue, process_queue
from .one_c_employees import EXPECTED, source_confirmed, apply_employees


def diagnostic():
    labels, schema = {}, []
    for table, (name, fields) in EXPECTED.items():
        labels['dbo.' + table] = {'name': name, 'columns': dict(fields)}
        for col in set(fields) | {'_IDRRef', '_Description', '_Marked', '_Code'}:
            schema.append({'schema': 'dbo', 'table': table, 'column': col, 'type': 'binary'})
    return {'metadata_labels': labels, 'schema': schema}


class EmployeesImportTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(name='Employee import')
        role, _ = Role.objects.get_or_create(code='ADMIN', defaults={'name': 'Admin'})
        self.admin = User.objects.create_user(username='employee-admin', company=self.company, role=role)
        self.client.force_login(self.admin)
        self.connection = OneCConnection.objects.create(company=self.company, server='sql', database='zup')
        self.diagnostic = OneCDiagnosticRun.objects.create(connection=self.connection, server='sql', database='zup', status='DONE', report=diagnostic())
        self.url = reverse('resources:employee_zup_import')

    def row(self, ref='1'*32, **kwargs):
        return {'source_id': ref, 'employee_number': '001', 'last_name': 'Иванов', 'first_name': 'Иван',
                'middle_name': 'Иванович', 'organisation': 'Организация', 'department': 'Отдел',
                'position': 'Инженер', 'archived': 0, **kwargs}

    def run_record(self):
        return OneCDiagnosticRun.objects.create(connection=self.connection, server='sql', database='zup', report={'employees_import': True})

    def test_source_confirmation_exact_labels_and_current_connection(self):
        self.assertTrue(source_confirmed(self.connection))
        self.diagnostic.report['metadata_labels']['dbo._Reference297']['columns']['_Fld30276RRef'] = 'Неверно'
        self.diagnostic.save()
        self.assertFalse(source_confirmed(self.connection))
        self.connection.metadata_server = 'sql'; self.connection.metadata_database = 'zup'
        self.connection.metadata_labels = {'dbo._Reference297': {'columns': {'_Fld30276RRef': 'ФизическоеЛицо'}}}
        self.connection.save()
        self.assertTrue(source_confirmed(self.connection))
        self.connection.database = 'other'; self.connection.save()
        self.assertFalse(source_confirmed(self.connection))

    def test_idempotence_updates_source_records_preserves_manual_and_other_company(self):
        manual = Employee.objects.create(company=self.company, first_name='Иван', last_name='Иванов', employee_number='001')
        other = Employee.objects.create(company=Company.objects.create(name='Other'), first_name='Иван', last_name='Иванов', zup_server='sql', zup_database='zup', zup_ref='1'*32)
        position = Position.objects.create(company=self.company, code='position', name='Инженер')
        run = self.run_record()
        result = apply_employees(run, {'employees': [self.row(), self.row('2'*32, archived=1)]})
        self.assertEqual((result['created'], result['archived_skipped']), (1, 1))
        employee = Employee.objects.get(company=self.company, zup_ref='1'*32)
        self.assertEqual(employee.position, position)
        self.assertEqual(employee.zup_department, 'Отдел')
        result = apply_employees(run, {'employees': [self.row()]})
        self.assertEqual((result['created'], result['unchanged']), (0, 1))
        result = apply_employees(run, {'employees': [self.row(last_name='Петров', employee_number='002', archived=1)]})
        self.assertEqual(result['updated'], 1)
        employee.refresh_from_db(); manual.refresh_from_db(); other.refresh_from_db()
        self.assertEqual(employee.last_name, 'Петров'); self.assertFalse(employee.is_active)
        self.assertEqual(employee.employee_number, '002'); self.assertIsNotNone(employee.zup_synced_at)
        self.assertEqual(manual.last_name, 'Иванов'); self.assertFalse(manual.zup_ref)
        self.assertEqual(other.last_name, 'Иванов')
        self.assertEqual(Employee.objects.filter(company=self.company).count(), 2)

    def test_validation_and_audit_failure_roll_back_entire_batch(self):
        run = self.run_record()
        for rows in [[], [self.row(), self.row()], [self.row(), self.row('2'*32, first_name='')], [self.row(archived=None)]]:
            with self.assertRaises(ValidationError):
                apply_employees(run, {'employees': rows})
            self.assertFalse(Employee.objects.filter(company=self.company).exists())
        with patch('apps.audit.models.AuditLog.objects.create', side_effect=RuntimeError('audit')):
            with self.assertRaises(RuntimeError):apply_employees(run, {'employees': [self.row()]})
        self.assertFalse(Employee.objects.filter(company=self.company).exists())

    def test_admin_button_queue_results_and_schema_report_after_import(self):
        self.assertContains(self.client.get(reverse('resources:employee_list')), 'Загрузить из ЗУП')
        self.assertEqual(self.client.get(self.url).status_code, 405)
        self.assertEqual(self.client.post(self.url).status_code, 302)
        self.assertEqual(self.client.post(self.url).status_code, 302)
        self.assertEqual(self.connection.runs.filter(report__employees_import=True).count(), 1)
        run = self.connection.runs.filter(report__employees_import=True).get()
        run.report = {'employees_import': True, 'employees_result': {'created': 1, 'updated': 0, 'unchanged': 0, 'archived_skipped': 0}}
        run.status = 'DONE'; run.save()
        self.assertContains(self.client.get(reverse('accounts:one_c_jobs')), 'Загрузка сотрудников')
        response = self.client.get(reverse('accounts:one_c_report'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['diagnostic']['id'], str(self.diagnostic.pk))
        role, _ = Role.objects.get_or_create(code='PLANNER', defaults={'name': 'Planner'})
        self.admin.role = role; self.admin.save()
        self.assertEqual(self.client.post(self.url).status_code, 403)
        self.assertNotContains(self.client.get(reverse('resources:employee_list')), 'Загрузить из ЗУП')

    def test_worker_reads_employee_result_and_applies_it_without_retaining_raw_rows(self):
        with TemporaryDirectory() as directory:
            run = enqueue(self.connection, self.admin, employees=True)
            path = Path(directory)/'.runtime/1c-diagnostics'/run.pk.hex
            path.mkdir(parents=True)
            (path/'status.json').write_text(json.dumps({'ok': True}))
            (path/'report.json').write_text(json.dumps({'employees_import': True, 'employees': [self.row()]}))
            from pathlib import PosixPath
            with override_settings(CONFIG_DIR=directory), patch('apps.accounts.one_c.os.name', 'nt'), patch('apps.accounts.one_c.Path', PosixPath), patch('apps.accounts.one_c.subprocess.CREATE_NO_WINDOW', 0, create=True), patch('apps.accounts.one_c.subprocess.run') as execute:
                process_queue()
            run.refresh_from_db()
            self.assertEqual(run.status, 'DONE', run.error)
            self.assertEqual(run.report['employees_result']['created'], 1)
            self.assertNotIn('employees', run.report)
            self.assertIn('-EmployeesImport', execute.call_args.args[0])
            self.connection.refresh_from_db(); self.assertIsNone(self.connection.active_until)
