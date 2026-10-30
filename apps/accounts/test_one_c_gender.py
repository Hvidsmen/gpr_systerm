from django.test import TestCase,SimpleTestCase
from django.core.exceptions import ValidationError
from . import test_one_c_employees as fixtures
from .test_one_c_catalog import binary,header
from .one_c_gender import enum_members,import_gender,reference_candidates
from .one_c_employees import apply_employees
from apps.resources.models import Employee

class GenderMetadataTests(SimpleTestCase):
    def test_named_enum_members_are_resolved_without_enum_order_or_names_of_people(self):
        owner='11111111-1111-1111-1111-111111111111';male='22222222-2222-2222-2222-222222222222';female='33333333-3333-3333-3333-333333333333'
        result=enum_members(binary([header(owner,'ПолФизическогоЛица'),header(male,'Мужской'),header(female,'Женский')]),owner)
        self.assertEqual(result['M'],reference_candidates(male));self.assertEqual(result['F'],reference_candidates(female))
        with self.assertRaises(ValueError):enum_members(binary([header(owner,'ДругойСправочник'),header(male,'Мужской'),header(female,'Женский')]),owner)
        for invalid in ['X',1,[],{}]:
            with self.assertRaises(ValidationError):import_gender({'gender':invalid},{'gender_source':'ФизическоеЛицо.Пол'})
class EmployeeGenderImportTests(TestCase):
    setUp=fixtures.EmployeesImportTests.setUp
    row=fixtures.EmployeesImportTests.row
    run_record=fixtures.EmployeesImportTests.run_record
    def test_gender_from_physical_person_updates_and_unknown_does_not_erase_manual_value(self):
        run=self.run_record();row=self.row(gender='M');apply_employees(run,{'employees':[row],'gender_source':'ФизическоеЛицо.Пол'})
        employee=Employee.objects.get(company=self.company,zup_ref='1'*32);self.assertEqual(employee.gender,'M')
        apply_employees(run,{'employees':[self.row(gender='')],'gender_source':'ФизическоеЛицо.Пол'});employee.refresh_from_db();self.assertEqual(employee.gender,'M')
        apply_employees(run,{'employees':[self.row(gender='F')],'gender_source':'ФизическоеЛицо.Пол'});employee.refresh_from_db();self.assertEqual(employee.gender,'F')
