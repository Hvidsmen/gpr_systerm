from html.parser import HTMLParser
from io import BytesIO
from urllib.parse import parse_qs, urlsplit

from django.test import TestCase
from django.urls import reverse
from openpyxl import load_workbook
from apps.accounts.models import Company, Role, User
from .models import Employee, Position


class EmployeeFilterTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(name='Employee filters')
        role, _ = Role.objects.get_or_create(code='ADMIN', defaults={'name': 'Admin'})
        self.client.force_login(User.objects.create_user(username='employee-filters', company=self.company, role=role))
        self.position = Position.objects.create(company=self.company, name='Монтажник')
        self.active = Employee.objects.create(company=self.company, first_name='Иван', last_name='Петров',
            position=self.position, employee_number='АО-111', zup_department='Цех Таналау')
        self.archived = Employee.objects.create(company=self.company, zup_full_name='Петров Иван Архивный',
            zup_ref='1'*32, zup_position_name='Инженер', is_active=False, zup_department='Архивный отдел')
        Employee.objects.create(company=self.company, zup_full_name='Иванов Пётр', zup_ref='2'*32,
            zup_position_name='Инженер', position=self.position)
        Employee.objects.create(company=Company.objects.create(name='Foreign filters'), first_name='Иван', last_name='Петров', zup_position_name='Монтажник', zup_department='Чужое подразделение')
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

    def test_department_matches_displayed_zup_and_staffing_values_exactly(self):
        staffing = Employee.objects.create(company=self.company, first_name='Штатный',
            zup_card={'staffing_position': 'Монтажник /Цех Таналау/'})
        Employee.objects.create(company=self.company, first_name='Другой цех', zup_department='Цех Таналау №2')
        Employee.objects.create(company=self.company, first_name='Приоритет ЗУП',
            zup_department='Другой отдел', zup_card={'staffing_position': 'Монтажник /Цех Таналау/'})
        hidden_fallback = Employee.objects.create(company=self.company, first_name='Прямая должность',
            zup_position_name='Монтажник', zup_card={'staffing_position': 'Монтажник /Скрытый отдел/'})
        self.assertEqual(staffing.display_department, self.active.display_department)
        self.assertEqual(hidden_fallback.display_department, '')
        self.assertEqual(self.ids({'department': self.active.display_department}), {self.active.pk, staffing.pk})
        self.assertEqual(self.ids({'department': self.active.display_department, 'q': 'Петров', 'position': 'МОНТАЖ'}), {self.active.pk})
        self.assertEqual(self.ids({'department': self.active.display_department, 'q': 'Несуществующий'}), set())

    def test_department_choices_are_scoped_and_keep_archive_selection(self):
        response = self.client.get(self.url)
        choices = list(response.context['filter_form'].fields['department'].choices)
        self.assertEqual(choices, [('', 'Все подразделения'), ('Архивный отдел', 'Архивный отдел'),
                                  ('Цех Таналау', 'Цех Таналау')])
        self.assertContains(response, 'Подразделение')
        self.assertEqual(self.ids({'department': 'Архивный отдел'}), set())
        self.assertEqual(self.ids({'department': 'Архивный отдел', 'include_archived': '1'}), {self.archived.pk})
        for department in ('Чужое подразделение', 'Несуществующий отдел'):
            self.assertEqual(self.ids({'department': department}), set())
            response = self.client.get(self.url, {'department': department, 'export': 'xlsx'})
            self.assertEqual(response.status_code, 400)

    def test_department_pagination_and_excel_share_combined_filters(self):
        Employee.objects.bulk_create([
            Employee(company=self.company, first_name='Иван', last_name=f'Петров {n:02}',
                     position=self.position, zup_department=self.active.zup_department)
            for n in range(22)
        ])
        params = {'department': self.active.zup_department, 'q': 'петров', 'position': 'монтаж',
                  'catalogue_position': self.position.pk, 'include_archived': '1', 'page': '2'}
        response = self.client.get(self.url, params)
        self.assertEqual(response.context['paginator'].count, 23)
        self.assertEqual(len(response.context['employees']), 3)
        self.assertIn('department=', response.context['filter_query'])
        self.assertNotIn('page=', response.context['filter_query'])
        self.assertEqual(response.context['filter_form']['department'].value(), self.active.zup_department)
        class Links(HTMLParser):
            def __init__(self):
                super().__init__()
                self.urls = []

            def handle_starttag(self, tag, attrs):
                if tag == 'a':
                    self.urls.append(dict(attrs).get('href', ''))

        links = Links()
        links.feed(response.content.decode())
        queries = [(url, parse_qs(urlsplit(url).query)) for url in links.urls]
        page_links = [query for url, query in queries if 'page' in query]
        self.assertTrue(page_links)
        self.assertTrue(all(query.get('department') == [self.active.zup_department] for query in page_links))
        export, export_query = next((url, query) for url, query in queries if query.get('export') == ['xlsx'])
        self.assertEqual(export_query['department'], [self.active.zup_department])
        response = self.client.get(export)
        self.assertEqual(response.status_code, 200)
        book = load_workbook(BytesIO(response.content), read_only=True)
        try:
            rows = list(book.active.values)
            self.assertEqual(len(rows), 24)  # Header and every matching page.
            department_index = rows[0].index('Подразделение')
            self.assertEqual({row[department_index] for row in rows[1:]}, {self.active.zup_department})
        finally:
            book.close()
