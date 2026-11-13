from django import forms
from django.test import TestCase
from django.urls import reverse

from apps.accounts.models import Company, Role, User
from apps.resources.models import Employee, Position
from .employee_widgets import RemoteEmployeeSelect


class EmployeeRemoteSearchTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.company = Company.objects.create(name='Remote employee search')
        cls.other = Company.objects.create(name='Other remote search')
        cls.user = User.objects.create_user(
            username='remote-search', company=cls.company,
            role=Role.objects.get(code='PERSONNEL_DISPATCHER'),
        )
        cls.position = Position.objects.create(company=cls.company, name='Машинист')
        Employee.objects.bulk_create([
            Employee(company=cls.company, last_name=f'Поиск{index:03}', first_name='Иван',
                     employee_number=f'R-{index:03}', position=cls.position)
            for index in range(105)
        ])
        cls.archived = Employee.objects.create(
            company=cls.company, last_name='Архивный', employee_number='OLD-1', is_active=False,
        )
        Employee.objects.create(company=cls.other, last_name='Чужой', employee_number='FOREIGN-1')

    def setUp(self):
        self.client.force_login(self.user)

    def test_searches_all_company_employees_in_pages_of_fifty(self):
        url = reverse('personnel:employee_search')
        first = self.client.get(url, {'q': 'Поиск', 'page': 1}).json()
        self.assertEqual(len(first['items']), 50)
        self.assertEqual(first['total'], 105)
        self.assertTrue(first['has_next'])
        exact = self.client.get(url, {'q': 'R-104'}).json()
        self.assertEqual([item['text'] for item in exact['items']], ['Поиск104 Иван · № R-104 · Машинист'])

    def test_company_scope_and_archived_switch(self):
        url = reverse('personnel:employee_search')
        self.assertEqual(self.client.get(url, {'q': 'FOREIGN-1'}).json()['items'], [])
        self.assertEqual(self.client.get(url, {'q': 'OLD-1'}).json()['items'], [])
        archived = self.client.get(url, {'q': 'OLD-1', 'active': '0'}).json()['items']
        self.assertEqual([item['id'] for item in archived], [self.archived.pk])

    def test_remote_widget_renders_selected_employee_not_full_catalogue(self):
        selected = Employee.objects.get(company=self.company, employee_number='R-104')

        class ExampleForm(forms.Form):
            employee = forms.ModelChoiceField(
                queryset=Employee.objects.filter(company=self.company),
                widget=RemoteEmployeeSelect,
            )

        html = str(ExampleForm(initial={'employee': selected.pk})['employee'])
        self.assertIn('Поиск104 Иван', html)
        self.assertNotIn('Поиск000', html)
        self.assertIn('data-remote-search-url', html)

