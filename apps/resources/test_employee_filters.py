from django.test import TestCase
from django.urls import reverse
from apps.accounts.models import Company, Role, User
from .models import Employee, Position


class EmployeeFilterTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(name='Employee filters')
        role, _ = Role.objects.get_or_create(code='ADMIN', defaults={'name': 'Admin'})
        self.client.force_login(User.objects.create_user(username='employee-filters', company=self.company, role=role))
        self.position = Position.objects.create(company=self.company, name='Монтажник')
        self.active = Employee.objects.create(company=self.company, first_name='Иван', last_name='Петров',
            position=self.position, employee_number='АО-111')
        self.archived = Employee.objects.create(company=self.company, zup_full_name='Петров Иван Архивный',
            zup_ref='1'*32, zup_position_name='Инженер', is_active=False)
        Employee.objects.create(company=self.company, zup_full_name='Иванов Пётр', zup_ref='2'*32,
            zup_position_name='Инженер', position=self.position)
        Employee.objects.create(company=Company.objects.create(name='Foreign filters'), first_name='Иван', last_name='Петров', zup_position_name='Монтажник')
        self.url = reverse('resources:employee_list')

    def ids(self, params=None):
        response = self.client.get(self.url, params or {})
        self.assertEqual(response.status_code, 200)
        return {e.pk for e in response.context['employees']}

    def test_default_active_only_and_checkbox_includes_archived(self):
        self.assertNotIn(self.archived.pk, self.ids())
        self.assertIn(self.archived.pk, self.ids({'include_archived': '1'}))
        self.assertNotIn(self.archived.pk, self.ids({'q': 'архивный'}))
        self.assertEqual(self.ids({'q': 'архивный', 'include_archived': '1'}), {self.archived.pk})

    def test_case_insensitive_name_parts_number_position_and_combined_filters(self):
        self.assertEqual(self.ids({'q': 'пЕТРОВ иВАН'}), {self.active.pk})
        self.assertEqual(self.ids({'q': 'ао-111'}), {self.active.pk})
        self.assertEqual(self.ids({'position': 'мОНТАЖНИК'}), {self.active.pk})
        self.assertEqual(self.ids({'q': 'петров', 'position': 'монтаж'}), {self.active.pk})
        self.assertEqual(self.ids({'q': 'петров', 'position': 'инженер'}), set())

    def test_validation_and_pagination_preserve_filters(self):
        self.assertEqual(self.ids({'q': 'x'*251}), set())
        for n in range(22):
            Employee.objects.create(company=self.company, first_name='Иван', last_name=f'Петров {n:02}', position=self.position)
        response = self.client.get(self.url, {'q': 'петров', 'position': 'монтаж', 'include_archived': '1', 'page': '2'})
        self.assertEqual(response.context['paginator'].count, 23)
        self.assertEqual(len(response.context['employees']), 3)
        self.assertIn('include_archived=1', response.context['filter_query'])
        self.assertIn('position=', response.context['filter_query'])
        self.assertNotIn('page=', response.context['filter_query'])
        self.assertContains(response, 'Страницы справочника')
