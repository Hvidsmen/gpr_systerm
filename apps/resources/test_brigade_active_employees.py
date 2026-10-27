from io import BytesIO

from django.test import TestCase
from django.urls import reverse
from openpyxl import load_workbook

from apps.accounts.models import Company, Role, User
from .models import Brigade, BrigadeGroup, Employee
from .position_catalog import position_for


class BrigadeActiveEmployeesTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(name='Active position employees')
        self.user = User.objects.create_user(username='active-position-planner',
            company=self.company, role=Role.objects.get(code='PLANNER'))
        self.client.force_login(self.user)
        self.group = BrigadeGroup.objects.create(company=self.company, name='ИТР Состав')
        self.mixed = Brigade.objects.create(company=self.company, name='Инженер', group=self.group)
        self.archived = Brigade.objects.create(company=self.company, name='Старший инженер')
        self.empty = Brigade.objects.create(company=self.company, name='Техник')
        self.employee(self.mixed)
        self.employee(self.mixed)
        self.employee(self.mixed, active=False)
        self.employee(self.archived, active=False)
        self.url = reverse('resources:brigade_list')

    def employee(self, brigade, active=True, company=None):
        return Employee.objects.create(company=company or self.company,
            position=position_for(brigade), first_name='Иван', last_name='Иванов', is_active=active)

    def rows(self, params):
        response = self.client.get(self.url, params)
        self.assertEqual(response.status_code, 200)
        return response, {row.pk: row for row in response.context['brigades']}

    def test_filters_active_people_not_position_status_and_shows_both_counts(self):
        response, rows = self.rows({})
        self.assertEqual(set(rows), {self.mixed.pk, self.archived.pk, self.empty.pk})
        self.assertEqual((rows[self.mixed.pk].employee_count, rows[self.mixed.pk].active_employee_count), (3, 2))
        self.assertEqual(rows[self.archived.pk].active_employee_count, 0)
        self.assertContains(response, 'Активные сотрудники')
        self.assertContains(response, f'?catalogue_position={position_for(self.mixed).pk}">2</a>')
        self.mixed.is_active = False
        self.mixed.save(update_fields=['is_active'])
        _, rows = self.rows({'active_employees': 'yes'})
        self.assertEqual(set(rows), {self.mixed.pk})
        _, rows = self.rows({'active_employees': 'no'})
        self.assertEqual(set(rows), {self.archived.pk, self.empty.pk})

    def test_foreign_employee_does_not_make_local_position_active(self):
        other = Company.objects.create(name='Foreign employee company')
        self.employee(self.empty, company=other)
        foreign = Brigade.objects.create(company=other, name='Чужой инженер')
        self.employee(foreign, company=other)
        _, rows = self.rows({'active_employees': 'yes'})
        self.assertEqual(set(rows), {self.mixed.pk})
        _, rows = self.rows({'active_employees': 'no'})
        self.assertEqual(rows[self.empty.pk].employee_count, 0)

    def test_filter_combines_with_name_group_and_is_retained_across_pages(self):
        params = {'active_employees': 'yes', 'name': 'ИНЖЕНЕР', 'group': self.group.pk}
        _, rows = self.rows(params)
        self.assertEqual(set(rows), {self.mixed.pk})
        self.assertEqual(self.rows({**params, 'name': 'Техник'})[1], {})
        for index in range(20):
            brigade = Brigade.objects.create(company=self.company, name=f'Инженер {index}', group=self.group)
            self.employee(brigade)
        response, rows = self.rows({**params, 'page': 2})
        self.assertEqual(response.context['catalogue_count'], 21)
        self.assertEqual(len(rows), 1)
        self.assertIn('active_employees=yes', response.context['filter_query'])
        self.assertContains(response, 'active_employees=yes&amp;')

    def test_excel_uses_same_filter_and_rejects_invalid_choice(self):
        url = reverse('resources:brigade_export')
        for choice, expected in [('yes', {'Инженер'}), ('no', {'Старший инженер', 'Техник'})]:
            response = self.client.get(url, {'active_employees': choice})
            self.assertEqual(response.status_code, 200)
            workbook = load_workbook(BytesIO(response.content))
            self.assertEqual({row[0] for row in list(workbook.active.values)[1:]}, expected)
            workbook.close()
        response, rows = self.rows({'active_employees': 'invalid'})
        self.assertEqual(rows, {})
        self.assertIn('active_employees', response.context['filter_form'].errors)
        self.assertEqual(self.client.get(url, {'active_employees': 'invalid'}).status_code, 400)
