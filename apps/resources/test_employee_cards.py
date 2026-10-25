from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse

from apps.accounts.models import Company, Role, User
from apps.accounts.one_c_employees import apply_employees, source_confirmed
from apps.accounts.test_one_c_employees import diagnostic
from apps.accounts.models import OneCConnection, OneCDiagnosticRun
from .models import Employee


class EmployeeCardTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(name='Cards')
        role, _ = Role.objects.get_or_create(code='ADMIN', defaults={'name': 'Admin'})
        self.user = User.objects.create_user(username='cards', company=self.company, role=role)
        self.client.force_login(self.user)
        self.connection = OneCConnection.objects.create(company=self.company, server='sql', database='zup')
        self.diagnostic = OneCDiagnosticRun.objects.create(connection=self.connection, server='sql', database='zup', status='DONE', report=diagnostic())
        self.run = OneCDiagnosticRun.objects.create(connection=self.connection, server='sql', database='zup', report={'employees_import': True})
        self.row = {'source_id': '1'*32, 'employee_number': '002', 'full_name': 'Иванов Иван Иванович',
                    'position': 'Должность из кадровой истории', 'department': 'Отдел', 'organisation': 'Организация',
                    'archived': 1, 'birth_date': '1990-02-03', 'hire_date': '2020-01-15', 'dismissal_date': '2025-04-30',
                    'inn': '123456789012', 'snils': '111-222-333 44', 'birth_place': 'Город', 'fte': 0.5,
                    'schedule': 'График 0,5 ставки', 'tariff_rate': '123456.78', 'payroll_fund': 0,
                    'position_source': 'Кадровая история', 'position_since': '2024-01-01'}

    def import_card(self):
        apply_employees(self.run, {'employees': [self.row]})
        return Employee.objects.get(company=self.company, zup_ref='1'*32)

    def role(self, code):
        self.user.role, _ = Role.objects.get_or_create(code=code, defaults={'name': code})
        self.user.save()

    def test_list_links_to_real_card_with_source_position_and_archive(self):
        employee = self.import_card()
        url = reverse('resources:employee_detail', args=[employee.pk])
        self.assertContains(self.client.get(reverse('resources:employee_list'), {'include_archived': '1'}), 'href="'+url+'"')
        response = self.client.get(url)
        for text in ['Последнее место работы', 'Уволен / в архиве ЗУП', 'Должность из кадровой истории',
                     'Кадровая история', '03.02.1990', '15.01.2020', '30.04.2025',
                     '123456789012', '111-222-333 44', '123 456,78', '0,00 ₽', 'График 0,5 ставки']:
            self.assertContains(response, text)
        self.assertEqual(employee.zup_card['fte'], '0.5')
        self.assertEqual(apply_employees(self.run, {'employees': [self.row]})['unchanged'], 1)

    def test_private_data_is_not_rendered_for_other_read_roles_or_in_list(self):
        employee = self.import_card()
        for code in ['PLANNER', 'MANAGER', 'OBSERVER', 'ESTIMATOR', 'PRODUCTION_HEAD', 'TECH_HEAD', 'SECURITY_HEAD', 'CEO']:
            self.role(code)
            response = self.client.get(reverse('resources:employee_detail', args=[employee.pk]))
            self.assertContains(response, employee.display_position)
            for secret in ['123456789012', '111-222-333 44', '123 456,78', '03.02.1990']:
                self.assertNotContains(response, secret)
        self.role('HR_HEAD')
        self.assertContains(self.client.get(reverse('resources:employee_detail', args=[employee.pk])), '123456789012')
        self.assertNotContains(self.client.get(reverse('resources:employee_list'), {'include_archived': '1'}), '123456789012')

    def test_company_scope_get_and_post_and_source_card_cannot_be_overwritten(self):
        employee = self.import_card()
        update = reverse('resources:employee_update', args=[employee.pk])
        self.assertEqual(self.client.get(update).status_code, 403)
        self.assertEqual(self.client.post(update, {'first_name': 'Changed', 'last_name': 'Changed'}).status_code, 403)
        employee.refresh_from_db()
        self.assertEqual(employee.display_name, self.row['full_name'])
        other = Employee.objects.create(company=Company.objects.create(name='Other'), first_name='Other', last_name='Other')
        self.assertEqual(self.client.get(reverse('resources:employee_detail', args=[other.pk])).status_code, 404)
        self.assertEqual(self.client.post(reverse('resources:employee_update', args=[other.pk]), {}).status_code, 404)

    def test_manual_create_opens_card_and_hr_can_edit_without_spoofing_source(self):
        self.role('HR_HEAD')
        data = {'first_name': 'Иван', 'last_name': 'Ручной', 'employee_number': '77', 'is_active': 'on',
                'zup_ref': 'f'*32, 'zup_card': {'inn': 'spoof'}}
        response = self.client.post(reverse('resources:employee_create'), data)
        employee = Employee.objects.get(company=self.company)
        self.assertRedirects(response, reverse('resources:employee_detail', args=[employee.pk]))
        self.assertEqual(employee.zup_ref, '')
        self.assertEqual(employee.zup_card, {})
        data['last_name'] = 'Обновлённый'
        self.assertRedirects(self.client.post(reverse('resources:employee_update', args=[employee.pk]), data),
                             reverse('resources:employee_detail', args=[employee.pk]))
        employee.refresh_from_db(); self.assertEqual(employee.last_name, 'Обновлённый')
        self.role('OBSERVER')
        self.assertEqual(self.client.post(reverse('resources:employee_update', args=[employee.pk]), data).status_code, 403)

    def test_card_validation_is_atomic_and_whitelisted(self):
        employee = self.import_card()
        for key, value in [('birth_date', '3990-01-01'), ('hire_date', {'bad': 1}), ('fte', 'NaN'),
                           ('tariff_rate', True), ('fte', '1e-99999'), ('inn', ['private']), ('schedule', 'a'*251)]:
            with self.assertRaises(ValidationError):
                apply_employees(self.run, {'employees': [dict(self.row, position='Changed', **{key: value})]})
            employee.refresh_from_db(); self.assertEqual(employee.display_position, self.row['position'])
        apply_employees(self.run, {'employees': [dict(self.row, unknown_sensitive='Not saved')]})
        employee.refresh_from_db(); self.assertNotIn('unknown_sensitive', employee.zup_card)

    def test_schema_proof_requires_history_and_date_offset(self):
        self.assertTrue(source_confirmed(self.connection))
        self.diagnostic.report['schema'] = [c for c in self.diagnostic.report['schema'] if c['table'] != '_YearOffset']
        self.diagnostic.save(); self.assertFalse(source_confirmed(self.connection))
        self.diagnostic.report = diagnostic()
        del self.diagnostic.report['metadata_labels']['dbo._Reference394']['columns']['_Fld31540RRef']
        self.diagnostic.save(); self.assertFalse(source_confirmed(self.connection))
