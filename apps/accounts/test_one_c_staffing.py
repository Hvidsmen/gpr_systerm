"""Use confirmed staffing labels without fabricating a job-catalogue relation."""
import json
import re
import sqlite3
from io import BytesIO
from pathlib import Path
from zipfile import ZipFile
from django.core.exceptions import ValidationError
from django.test import SimpleTestCase, TestCase
from django.urls import reverse
from apps.resources.models import Employee, Position
from .one_c_employees import apply_employees
from . import test_one_c_employees as employee_tests
from .models import Role, Company


SCRIPT = Path(__file__).resolve().parents[2]/'ops/diagnose-1c.ps1'


class StaffingSQLTests(SimpleTestCase):
    def setUp(self):
        self.script = SCRIPT.read_text(encoding='utf-8-sig')
        self.db = sqlite3.connect(':memory:')
        self.addCleanup(self.db.close)
        self.db.row_factory = sqlite3.Row
        self.db.executescript('''
            CREATE TABLE _Reference394 (_IDRRef BLOB, _Description TEXT, _Fld31540RRef BLOB, _Fld31539RRef BLOB, _OwnerIDRRef BLOB);
            CREATE TABLE _Reference92 (_IDRRef BLOB, _Description TEXT);
            CREATE TABLE _Reference216 (_IDRRef BLOB, _Description TEXT);
            CREATE TABLE _Reference166 (_IDRRef BLOB, _Description TEXT);
        ''')
        self.staff, self.job, self.department, self.org = [bytes([n])*16 for n in [1,2,3,4]]
        self.name = 'Аналитик /Управление цифровизации/'
        self.db.execute('INSERT INTO _Reference394 VALUES (?,?,?,?,?)',
            (self.staff,self.name,self.job,self.department,self.org))
        self.db.execute('INSERT INTO _Reference216 VALUES (?,?)',(self.department,'Управление цифровизации'))
        self.db.execute('INSERT INTO _Reference166 VALUES (?,?)',(self.org,'Организация'))

    def joins(self, candidates=1):
        fragment = self.script.split('-- staffing occupancy end\n',1)[1].split('-- Staffing name selection begin:',1)[0]
        query = ('WITH occ(staffing_ref,candidates) AS (VALUES (?,?)) '
            'SELECT so.[_Description] staffing_name,jo.[_IDRRef] job_row,jo.[_Description] job_name,'
            'doo.[_Description] department_name,oo.[_Description] org_name FROM occ\n'+fragment)
        return dict(self.db.execute(query.replace('[dbo].',''),(self.staff,candidates)).fetchone())

    def staffing(self, **overrides):
        fragment = self.script.split('-- Staffing name selection begin:',1)[1].split('-- Staffing name selection end',1)[0]
        query = fragment[fragment.index('OUTER APPLY (')+len('OUTER APPLY ('):].strip()
        query = query.removesuffix(') sf').strip().replace('SELECT TOP (1)','SELECT',1)
        query = re.sub(r"\bN'", "'", query)
        parameters = {}
        def bind(match):
            key = (match.group(1)+'_'+match.group(2)) if match.group(1) else ('occ_'+match.group(3))
            parameters[key] = overrides.get(key)
            return ':'+key
        query = re.sub(r'\b(\w+)\.\[(_\w+)\]|\bocc\.(fte|since)',bind,query)
        row = self.db.execute(query+' LIMIT 1',parameters).fetchone()
        return dict(row) if row else None

    def test_actual_staffing_joins_keep_label_when_job_reference_is_missing(self):
        row = self.joins()
        self.assertEqual(row['staffing_name'],self.name)
        self.assertIsNone(row['job_row']);self.assertIsNone(row['job_name'])
        chosen = self.staffing(so__Description=row['staffing_name'],doo__Description=row['department_name'],
            oo__Description=row['org_name'],occ_fte=0.5,occ_since='2025-02-12 00:00:00')
        self.assertEqual(chosen['staffing_position'],self.name)
        self.assertEqual(chosen['department'],'Управление цифровизации')
        self.assertEqual(chosen['fte'],0.5)
        self.assertIn('наименование',chosen['staffing_source'])

    def test_existing_empty_job_differs_from_missing_catalogue_record(self):
        self.db.execute('INSERT INTO _Reference92 VALUES (?,?)',(self.job,''))
        row = self.joins()
        self.assertEqual(row['job_row'],self.job)
        self.assertEqual(row['job_name'],'')
        self.assertEqual(row['staffing_name'],self.name)

    def test_ambiguous_occupancy_cannot_select_a_staffing_name(self):
        row = self.joins(candidates=2)
        self.assertIsNone(row['staffing_name'])
        self.assertIsNone(self.staffing(so__Description=row['staffing_name']))

    def test_prefer_confirmed_occupancy_then_picker_and_preserve_selected_fields(self):
        result = self.staffing(so__Description='Оператор',occ_fte=0.5,
            ps__Description='Старое место',pc__Fld16842=1)
        self.assertEqual((result['staffing_position'],result['fte']),('Оператор',0.5))
        result = self.staffing(so__Description='   ',ps__Description='Подобранная позиция',
            pc__Fld16842=1,dp__Description='Отдел подбора',s__Description='Историческая позиция')
        self.assertEqual(result['staffing_position'],'Подобранная позиция')
        self.assertEqual(result['department'],'Отдел подбора')
        self.assertIn('Подбор',result['staffing_source'])

    def test_catalogue_counters_distinguish_empty_names_and_empty_table(self):
        query = re.search(r'\$positionCatalogue=@\(Query \$connection "([^"]+)"\)',self.script).group(1)
        query = query.replace('[dbo].','').replace('COUNT_BIG','COUNT').replace("N'", "'")
        self.assertEqual(tuple(self.db.execute(query).fetchone()),(0,0))
        self.db.executemany('INSERT INTO _Reference92 VALUES (?,?)',[(self.job,''),(bytes([5])*16,'Аналитик')])
        self.assertEqual(tuple(self.db.execute(query).fetchone()),(2,1))


class StaffingCardTests(TestCase):
    setUp = employee_tests.EmployeesImportTests.setUp
    row = employee_tests.EmployeesImportTests.row
    run_record = employee_tests.EmployeesImportTests.run_record

    def staffing_row(self, **changes):
        return self.row(position='',department='',staffing_position='Аналитик /Управление цифровизации/',
            staffing_source='Занятость штатных позиций (наименование)',staffing_since='2025-02-12',
            position_state='staffing',trace_staffing_ref='a'*32,trace_position_ref='b'*32,
            trace_department_ref='c'*32,probe_occupancy_position_ref=1,
            probe_occupancy_position_row=0,probe_occupancy_position_join=0,
            probe_occupancy_staffing_named=1,probe_staffing_display_fallback=1,**changes)

    def test_staffing_display_filter_and_card_with_no_invented_job_relation(self):
        Position.objects.create(company=self.company,code='analyst',name='Аналитик /Управление цифровизации/')
        result = apply_employees(self.run_record(),{'employees':[self.staffing_row()],
            'position_catalog':{'position_rows':200,'position_named':195}})
        employee = Employee.objects.get(company=self.company,zup_ref='1'*32)
        self.assertEqual(employee.zup_position_name,'')
        self.assertIsNone(employee.position_id)
        self.assertTrue(employee.position_from_staffing)
        self.assertEqual(employee.display_position,'Аналитик')
        self.assertEqual(employee.display_department,'Управление цифровизации')
        self.assertEqual(employee.zup_card['staffing_position'],'Аналитик /Управление цифровизации/')
        self.assertEqual((result['without_position'],result['staffing_fallback']),(0,1))
        self.assertEqual(result['position_diagnostics']['catalogue'],{'position_rows':200,'position_named':195})
        listing = self.client.get(reverse('resources:employee_list'),{'position':'АНАЛИТИК'})
        self.assertContains(listing,employee.display_position)
        self.assertContains(listing,'По штатной позиции ЗУП')
        self.assertContains(listing,employee.display_department)
        self.assertNotContains(listing,employee.zup_card['staffing_position'])
        self.assertNotContains(self.client.get(reverse('resources:employee_list'),{'position':'цифровизации'}),employee.display_name)
        card = self.client.get(reverse('resources:employee_detail',args=[employee.pk]))
        for text in ['Должность (штатная позиция)',employee.display_position,'12.02.2025','Проверка должности (ZIP)']:
            self.assertContains(card,text)
        self.assertNotContains(card,'Последняя загрузка не вернула')
        self.assertContains(card,employee.display_department)
        self.assertNotContains(card,employee.zup_card['staffing_position'])
        self.assertEqual(apply_employees(self.run_record(),{'employees':[self.staffing_row()]})['unchanged'],1)

    def test_staffing_format_is_explicit_and_structured_department_has_priority(self):
        from apps.resources.employee_staffing import staffing_parts
        for label in ['Слесарь / сварщик','Инженер','Начальник /Участок/Отдел/', '/Отдел/']:
            self.assertEqual(staffing_parts(label),(label,''))
        self.assertEqual(staffing_parts('  Аналитик / Управление цифровизации /  '),('Аналитик','Управление цифровизации'))
        employee = Employee(zup_department='Подразделение из регистра',
            zup_card={'staffing_position':'Аналитик /Управление цифровизации/'})
        self.assertEqual(employee.display_department,'Подразделение из регистра')

    def test_real_job_has_priority_in_display_mapping_and_filter(self):
        position = Position.objects.create(company=self.company,code='real',name='Подтверждённая должность')
        values = self.staffing_row()
        values.update(position='Подтверждённая должность',position_source='Текущие кадровые данные',position_state='job')
        apply_employees(self.run_record(),{'employees':[values]})
        employee = Employee.objects.get(company=self.company,zup_ref='1'*32)
        self.assertEqual(employee.position_id,position.pk)
        self.assertFalse(employee.position_from_staffing)
        self.assertEqual(employee.display_position,'Подтверждённая должность')
        self.assertNotContains(self.client.get(reverse('resources:employee_list'),{'position':'АНАЛИТИК'}),employee.display_name)

    def test_specific_zip_has_counts_and_refs_without_private_data_and_is_scoped(self):
        values = self.staffing_row()
        values.update(inn='123456789012',snils='111-222-333 44',tariff_rate='98765.43')
        apply_employees(self.run_record(),{'employees':[values]})
        employee = Employee.objects.get(company=self.company,zup_ref='1'*32)
        url = reverse('resources:employee_position_report',args=[employee.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code,200)
        self.assertEqual(response['Content-Type'],'application/zip')
        self.assertIn('no-store',response['Cache-Control'])
        with ZipFile(BytesIO(response.content)) as archive:
            payload = archive.read(archive.namelist()[0]).decode()
            report = json.loads(payload)
        self.assertEqual(report['references']['position'],'b'*32)
        self.assertEqual(report['probes']['occupancy_position_row'],0)
        self.assertEqual(report['state'],'staffing')
        for private in ['Иванов','Иванович','123456789012','111-222-333 44','98765.43',values['staffing_position']]:
            self.assertNotIn(private,payload)
        foreign = Employee.objects.create(company=Company.objects.create(name='Other'),first_name='Other',last_name='Employee')
        self.assertEqual(self.client.get(reverse('resources:employee_position_report',args=[foreign.pk])).status_code,404)
        self.assertEqual(self.client.post(url,{}).status_code,405)
        self.admin.role = Role.objects.get(code='HR_HEAD');self.admin.save()
        self.assertEqual(self.client.get(url).status_code,403)
        self.assertNotContains(self.client.get(reverse('resources:employee_detail',args=[employee.pk])),'Проверка должности (ZIP)')

    def test_ambiguous_record_stays_missing_and_card_explains_why(self):
        apply_employees(self.run_record(),{'employees':[self.row(position='',position_state='ambiguous',probe_occupancy_ambiguous=1)]})
        employee = Employee.objects.get(company=self.company,zup_ref='1'*32)
        self.assertEqual(employee.display_position,'')
        self.assertContains(self.client.get(reverse('resources:employee_detail',args=[employee.pk])),'найдено несколько вариантов назначения')

    def test_bad_staffing_trace_state_or_catalogue_rolls_back(self):
        run = self.run_record()
        for changes in [{'staffing_position':'x'*251},{'position_state':'guess'},
            {'trace_position_ref':'not-a-reference'},{'probe_occupancy_position_row':{'bad':1}}]:
            with self.subTest(changes=changes):
                values = self.staffing_row();values.update(changes)
                with self.assertRaises(ValidationError):apply_employees(run,{'employees':[values]})
                self.assertFalse(Employee.objects.filter(company=self.company).exists())
        with self.assertRaises(ValidationError):
            apply_employees(run,{'employees':[self.staffing_row()],'position_catalog':{'position_rows':0,'position_named':2}})
        self.assertFalse(Employee.objects.filter(company=self.company).exists())
