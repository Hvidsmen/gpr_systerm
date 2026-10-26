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
        for col in set(fields) | {'_IDRRef', '_Description', '_Marked', '_Code', '_RecorderRRef', '_LineNo'}:
            schema.append({'schema': 'dbo', 'table': table, 'column': col, 'type': 'binary'})
    schema.append({'schema': 'dbo', 'table': '_YearOffset', 'column': 'Offset', 'type': 'int'})
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
        self.assertEqual((result['created'], result['archived']), (2, 1))
        self.assertFalse(Employee.objects.get(company=self.company,zup_ref='2'*32).is_active)
        employee = Employee.objects.get(company=self.company, zup_ref='1'*32)
        self.assertEqual(employee.position, position)
        self.assertEqual(employee.zup_department, 'Отдел')
        self.assertContains(self.client.get(reverse('resources:employee_list'), {'include_archived': '1'}), 'Уволен / в архиве ЗУП')
        result = apply_employees(run, {'employees': [self.row()]})
        self.assertEqual((result['created'], result['unchanged']), (0, 1))
        result = apply_employees(run, {'employees': [self.row(last_name='Петров', employee_number='002', archived=1)]})
        self.assertEqual(result['updated'], 1)
        employee.refresh_from_db(); manual.refresh_from_db(); other.refresh_from_db()
        self.assertEqual(employee.last_name, 'Петров'); self.assertFalse(employee.is_active)
        self.assertEqual(employee.employee_number, '002'); self.assertIsNotNone(employee.zup_synced_at)
        self.assertEqual(manual.last_name, 'Иванов'); self.assertFalse(manual.zup_ref)
        self.assertEqual(other.last_name, 'Иванов')
        self.assertEqual(Employee.objects.filter(company=self.company).count(), 3)

    def test_full_name_only_and_archived_records_do_not_abort_import(self):
        run=self.run_record()
        rows=[self.row(),self.row('2'*32,first_name='',last_name='',middle_name='',full_name='Полное ФИО из ЗУП',archived=1),
              self.row('3'*32,first_name='',last_name='',middle_name='',full_name='')]
        result=apply_employees(run,{'employees':rows})
        self.assertEqual(result['created'],3)
        self.assertEqual(result['archived'],1)
        self.assertEqual(result['incomplete_names'],2)
        self.assertEqual(result['unnamed'],1)
        employee=Employee.objects.get(company=self.company,zup_ref='2'*32)
        self.assertEqual(employee.zup_full_name,'Полное ФИО из ЗУП')
        self.assertEqual(str(employee),'Полное ФИО из ЗУП')
        self.assertEqual(employee.name_note,'ФИО без разбивки')
        self.assertFalse(employee.is_active)
        employee.full_clean()
        missing=Employee.objects.get(company=self.company,zup_ref='3'*32)
        self.assertIn('Сотрудник ЗУП',str(missing));self.assertEqual(missing.zup_full_name,'')
        self.assertEqual(missing.name_note,'ФИО не заполнено в ЗУП')
        again=apply_employees(run,{'employees':rows})
        self.assertEqual((again['created'],again['unchanged']),(0,3))
        response=self.client.get(reverse('resources:employee_list'), {'include_archived': '1'})
        self.assertContains(response,'Полное ФИО из ЗУП')
        self.assertContains(response,'Уволен / в архиве ЗУП')
        self.assertContains(response,'ФИО не заполнено в ЗУП')

    def test_manual_employee_still_requires_name_parts(self):
        from apps.resources.forms import EmployeeForm
        form=EmployeeForm({'first_name':'','last_name':'','middle_name':'','is_active':'on'})
        form.instance.company=self.company
        self.assertFalse(form.is_valid())
        self.assertIn('first_name',form.errors);self.assertIn('last_name',form.errors)
        with self.assertRaises(ValidationError):Employee(company=self.company).full_clean()

    def test_picker_position_links_full_canonical_job_name_and_preserves_source(self):
        from apps.resources.models import Brigade
        from apps.resources.position_catalog import position_for
        name='Инженер '+('А'*100)
        brigade=Brigade.objects.create(company=self.company,name=name)
        position=position_for(brigade)
        self.assertNotEqual(position.name,name)
        result=apply_employees(self.run_record(),{'employees':[self.row(position=name,department='Отдел подбора',
            position_source='Данные для подбора сотрудников',fte='0.5',probe_picker_candidate=1,probe_picker_position_join=1)]})
        employee=Employee.objects.get(company=self.company,zup_ref='1'*32)
        self.assertEqual(employee.position_id,position.pk)
        self.assertEqual(employee.display_position,name)
        self.assertEqual(employee.zup_department,'Отдел подбора')
        self.assertEqual(employee.zup_card['fte'],'0.5')
        self.assertEqual(result['position_diagnostics']['coverage']['active']['picker_position_join'],1)

    def test_occupancy_import_fills_existing_card_list_and_position_filter(self):
        run = self.run_record()
        apply_employees(run, {'employees': [self.row(position='', department='', fte=0)]})
        employee = Employee.objects.get(company=self.company, zup_ref='1'*32)
        pk = employee.pk
        source = 'Занятость позиций штатного расписания'
        result = apply_employees(run, {'employees': [self.row(position='Механик', department='Автоколонна',
            fte='0.5', position_source=source, position_since='2026-01-01',
            probe_occupancy_candidate=1, probe_occupancy_staffing_join=1,
            probe_occupancy_position_ref=1, probe_occupancy_position_join=1),
            self.row('2'*32, position='Слесарь', archived=1, position_source=source,
                     probe_occupancy_candidate=1, probe_occupancy_position_join=1)]})
        employee.refresh_from_db()
        self.assertEqual(employee.pk, pk)
        self.assertEqual(employee.display_position, 'Механик')
        self.assertEqual(employee.zup_card['fte'], '0.5')
        response = self.client.get(reverse('resources:employee_detail', args=[pk]))
        for text in ['Механик', 'Автоколонна', source, '01.01.2026']:
            self.assertContains(response, text)
        response = self.client.get(reverse('resources:employee_list'), {'position': 'механик'})
        self.assertContains(response, 'Механик')
        self.assertNotContains(response, 'Слесарь')
        coverage = result['position_diagnostics']['coverage']
        self.assertEqual(coverage['all']['occupancy_position_join'], 2)
        self.assertEqual(coverage['active']['occupancy_position_join'], 1)
        run.report = {'employees_import': True, 'employees_result': result}
        run.status = 'DONE'; run.save()
        from io import BytesIO
        from zipfile import ZipFile
        response = self.client.get(reverse('accounts:one_c_report'), {'employee_run': str(run.pk)})
        with ZipFile(BytesIO(response.content)) as package:
            report = json.loads(package.read(package.namelist()[0]))
        self.assertEqual(report['summary']['coverage'], coverage)
        self.assertNotIn('Механик', json.dumps(report, ensure_ascii=False))

    def test_occupancy_schema_requires_custom_fields_but_owner_is_a_system_column(self):
        labels = self.diagnostic.report['metadata_labels']['dbo._Reference394']['columns']
        del labels['_OwnerIDRRef']
        self.diagnostic.save()
        self.assertTrue(source_confirmed(self.connection))
        labels = self.diagnostic.report['metadata_labels']['dbo._InfoRg40095']['columns']
        del labels['_Fld40108']
        self.diagnostic.save()
        self.assertFalse(source_confirmed(self.connection))

    def test_position_diagnostics_download_has_only_coverage_and_is_company_scoped(self):
        run = self.run_record()
        result = apply_employees(run, {'employees': [self.row(probe_current_row=1,
            probe_current_position_ref=0, probe_order_position=1, probe_corrected_history_position_ref=1)]})
        run.report = {'employees_import': True, 'employees_result': result}
        run.status = 'DONE'; run.save()
        response = self.client.get(reverse('accounts:one_c_report'), {'employee_run': str(run.pk)})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'application/zip')
        self.assertIn(f'zup-positions-{run.pk.hex}.zip', response['Content-Disposition'])
        self.assertIn('no-store', response['Cache-Control'])
        from io import BytesIO
        from zipfile import ZipFile, ZIP_DEFLATED
        with ZipFile(BytesIO(response.content)) as archive:
            self.assertIsNone(archive.testzip())
            self.assertEqual(archive.namelist(), [f'zup-positions-{run.pk.hex}.json'])
            self.assertEqual(archive.infolist()[0].compress_type, ZIP_DEFLATED)
            payload = archive.read(archive.namelist()[0]).decode('utf-8')
        data = json.loads(payload)
        self.assertEqual(data['summary']['coverage']['active']['order_position'], 1)
        plain = self.client.get(reverse('accounts:one_c_report'), {'employee_run': str(run.pk), 'format': 'json'})
        self.assertEqual(plain.json(), data)
        self.assertContains(self.client.get(reverse('accounts:one_c_jobs')), 'Скачать проверку кадровых источников (ZIP)')
        for secret in ['Иванов', 'Иванович', 'Организация', 'Инженер', 'source_id', 'first_name']:
            self.assertNotIn(secret, payload)
        with self.assertRaises(ValidationError):
            apply_employees(run, {'employees': [self.row(probe_current_row='bad')]})
        other = Company.objects.create(name='Diagnostic other')
        self.admin.company = other; self.admin.save()
        self.assertEqual(self.client.get(reverse('accounts:one_c_report'), {'employee_run': str(run.pk)}).status_code, 404)

    def test_validation_and_audit_failure_roll_back_entire_batch(self):
        run = self.run_record()
        for rows in [[], [self.row(), self.row()], [self.row(), self.row('2'*32, first_name={'bad':'type'})], [self.row(archived=None)]]:
            with self.assertRaises(ValidationError):
                apply_employees(run, {'employees': rows})
            self.assertFalse(Employee.objects.filter(company=self.company).exists())
        with patch('apps.audit.models.AuditLog.objects.create', side_effect=RuntimeError('audit')):
            with self.assertRaises(RuntimeError):apply_employees(run, {'employees': [self.row()]})
        self.assertFalse(Employee.objects.filter(company=self.company).exists())

    def test_employee_sql_runs_after_connection_open_not_in_launcher(self):
        script=(Path(__file__).resolve().parents[2]/'ops'/'diagnose-1c.ps1').read_text(encoding='utf-8-sig')
        open_at=script.index('$connection.Open()')
        query_at=script.index('$employees=@(Query $connection')
        self.assertGreater(query_at,open_at)
        self.assertIn("if($EmployeesImport){$arguments+=' -EmployeesImport'}",script[:open_at])
        self.assertNotIn('SELECT TOP (200001)',script[:open_at])
        self.assertIn('Write-Json $reportPath @{employees_import=$true;employees_card_version=5;position_catalog=$positionCatalogue[0];employees=@($employees)}',script[query_at:])
        self.assertIn("NULLIF(LTRIM(RTRIM(p.[_Fld31265])),N'')",script[query_at:])
        self.assertIn("NULLIF(LTRIM(RTRIM(p.[_Description])),N'')",script[query_at:])
        self.assertIn("NULLIF(LTRIM(RTRIM(e.[_Description])),N'')",script[query_at:])

    def test_missing_empty_and_oversized_reports_explain_distinct_causes(self):
        run=self.run_record()
        for report,message in [({},'без списка'),({'employees':{}},'формат'),({'employees':[]},'0 сотрудников'),({'employees':[{}]*200001},'200001 строк')]:
            with self.assertRaisesMessage(ValidationError,message):apply_employees(run,report)
        self.assertFalse(Employee.objects.filter(company=self.company).exists())

    def test_admin_button_queue_results_and_schema_report_after_import(self):
        self.assertContains(self.client.get(reverse('resources:employee_list'), {'include_archived': '1'}), 'Загрузить из ЗУП')
        self.assertEqual(self.client.get(self.url).status_code, 405)
        self.assertEqual(self.client.post(self.url).status_code, 302)
        self.assertEqual(self.client.post(self.url).status_code, 302)
        self.assertEqual(self.connection.runs.filter(report__employees_import=True).count(), 1)
        run = self.connection.runs.filter(report__employees_import=True).get()
        run.report = {'employees_import': True, 'employees_result': {'created': 1, 'updated': 0, 'unchanged': 0, 'archived': 0}}
        run.status = 'DONE'; run.save()
        self.assertContains(self.client.get(reverse('accounts:one_c_jobs')), 'Загрузка сотрудников')
        response = self.client.get(reverse('accounts:one_c_report'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['diagnostic']['id'], str(self.diagnostic.pk))
        role, _ = Role.objects.get_or_create(code='PLANNER', defaults={'name': 'Planner'})
        self.admin.role = role; self.admin.save()
        self.assertEqual(self.client.post(self.url).status_code, 403)
        self.assertNotContains(self.client.get(reverse('resources:employee_list'), {'include_archived': '1'}), 'Загрузить из ЗУП')

    def test_worker_rejects_diagnostic_instead_of_employee_result(self):
        with TemporaryDirectory() as directory:
            run=enqueue(self.connection,self.admin,employees=True)
            path=Path(directory)/'.runtime/1c-diagnostics'/run.pk.hex
            path.mkdir(parents=True)
            (path/'status.json').write_text(json.dumps({'ok':True}))
            (path/'report.json').write_text(json.dumps({'schema':[]}))
            from pathlib import PosixPath
            with override_settings(CONFIG_DIR=directory),patch('apps.accounts.one_c.os.name','nt'),patch('apps.accounts.one_c.Path',PosixPath),patch('apps.accounts.one_c.subprocess.CREATE_NO_WINDOW',0,create=True),patch('apps.accounts.one_c.subprocess.run'):
                process_queue()
            run.refresh_from_db();self.connection.refresh_from_db()
            self.assertEqual(run.status,'ERROR')
            self.assertIn('другое задание',run.error)
            self.assertEqual(run.report,{'employees_import':True})
            self.assertIsNone(self.connection.active_until)
            self.assertFalse(Employee.objects.filter(company=self.company).exists())

    def test_worker_rejects_legacy_report_without_erasing_existing_card(self):
        employee = Employee.objects.create(company=self.company, zup_server='sql', zup_database='zup',
            zup_ref='1'*32, first_name='Иван', last_name='Иванов', zup_card={'inn': '123456789012'})
        with TemporaryDirectory() as directory:
            run = enqueue(self.connection, self.admin, employees=True)
            path = Path(directory)/'.runtime/1c-diagnostics'/run.pk.hex
            path.mkdir(parents=True)
            (path/'status.json').write_text(json.dumps({'ok': True}))
            (path/'report.json').write_text(json.dumps({'employees_import': True, 'employees_card_version': 4, 'employees': [self.row()]}))
            from pathlib import PosixPath
            with override_settings(CONFIG_DIR=directory), patch('apps.accounts.one_c.os.name', 'nt'), patch('apps.accounts.one_c.Path', PosixPath), patch('apps.accounts.one_c.subprocess.CREATE_NO_WINDOW', 0, create=True), patch('apps.accounts.one_c.subprocess.run'):
                process_queue()
            run.refresh_from_db(); employee.refresh_from_db(); self.connection.refresh_from_db()
            self.assertEqual(run.status, 'ERROR')
            self.assertIn('прежний формат', run.error)
            self.assertEqual(employee.zup_card, {'inn': '123456789012'})
            self.assertEqual(run.report, {'employees_import': True})
            self.assertIsNone(self.connection.active_until)

    def test_worker_reads_employee_result_and_applies_it_without_retaining_raw_rows(self):
        with TemporaryDirectory() as directory:
            run = enqueue(self.connection, self.admin, employees=True)
            path = Path(directory)/'.runtime/1c-diagnostics'/run.pk.hex
            path.mkdir(parents=True)
            (path/'status.json').write_text(json.dumps({'ok': True}))
            (path/'report.json').write_text(json.dumps({'employees_import': True, 'employees_card_version': 5, 'employees': [self.row()]}))
            from pathlib import PosixPath
            with override_settings(CONFIG_DIR=directory), patch('apps.accounts.one_c.os.name', 'nt'), patch('apps.accounts.one_c.Path', PosixPath), patch('apps.accounts.one_c.subprocess.CREATE_NO_WINDOW', 0, create=True), patch('apps.accounts.one_c.subprocess.run') as execute:
                process_queue()
            run.refresh_from_db()
            self.assertEqual(run.status, 'DONE', run.error)
            self.assertEqual(run.report['employees_result']['created'], 1)
            self.assertNotIn('employees', run.report)
            self.assertIn('-EmployeesImport', execute.call_args.args[0])
            self.connection.refresh_from_db(); self.assertIsNone(self.connection.active_until)
