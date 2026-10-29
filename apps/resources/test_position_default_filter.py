from io import BytesIO

from django.test import TestCase
from django.urls import reverse
from openpyxl import load_workbook

from apps.accounts.models import Company, Role, User
from .models import Brigade


class PositionDefaultFilterTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(name='Default current staffing')
        self.user = User.objects.create_user(username='current-staffing-planner', company=self.company,
                                             role=Role.objects.get(code='PLANNER'))
        self.client.force_login(self.user)
        self.current = Brigade.objects.create(company=self.company, name='Актуальная', staffing_current=True)
        self.old = Brigade.objects.create(company=self.company, name='Неактуальная', staffing_current=False)
        self.unknown = Brigade.objects.create(company=self.company, name='Ручная', staffing_current=None)
        other = Company.objects.create(name='Foreign current staffing')
        Brigade.objects.create(company=other, name='Чужая актуальная', staffing_current=True)
        self.url = reverse('resources:brigade_list')

    def test_first_visit_and_table_default_to_current_and_export_matches(self):
        for params in [{}, {'view': 'table'}]:
            response = self.client.get(self.url, params)
            self.assertEqual(list(response.context['brigades']), [self.current])
            self.assertEqual(response.context['filter_form']['staffing_current'].value(), 'yes')
            self.assertIn('staffing_current=yes', response.context['filter_query'])
        response = self.client.get(reverse('resources:brigade_export'))
        workbook = load_workbook(BytesIO(response.content))
        rows = list(workbook.active.values)
        self.assertEqual([row[rows[0].index('Название')] for row in rows[1:]], [self.current.name])
        workbook.close()

    def test_explicit_all_inactive_and_unknown_remain_available(self):
        for value, expected in [('', {self.current.pk, self.old.pk, self.unknown.pk}),
                                ('no', {self.old.pk}), ('unknown', {self.unknown.pk})]:
            response = self.client.get(self.url, {'staffing_current': value})
            self.assertEqual(set(response.context['brigades'].values_list('pk', flat=True)), expected)
            self.assertEqual(response.context['filter_form']['staffing_current'].value(), value)
            self.assertContains(response, 'staffing_current=' + value)

    def test_search_keeps_default_and_reset_returns_to_current(self):
        response = self.client.get(self.url, {'name': self.unknown.name})
        self.assertEqual(len(response.context['brigades']), 0)
        response = self.client.get(self.url, {'name': self.unknown.name, 'staffing_current': ''})
        self.assertEqual(list(response.context['brigades']), [self.unknown])
        self.assertEqual(list(self.client.get(self.url).context['brigades']), [self.current])
