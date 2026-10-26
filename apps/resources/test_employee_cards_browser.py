"""Optional real-browser checks, including mobile card layout."""
import json
import os
import shutil
import subprocess
from pathlib import Path
from unittest import skipUnless

from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.test import Client
from django.urls import reverse

from apps.accounts.models import Company, Role, User
from .models import Employee


@skipUnless(os.environ.get('EMPLOYEE_BROWSER_TESTS') == '1', 'Optional Playwright/Chromium check')
class EmployeeCardBrowserTests(StaticLiveServerTestCase):
    def test_card_links_private_data_and_mobile(self):
        company = Company.objects.create(name='Browser company')
        def user(code):
            role, _ = Role.objects.get_or_create(code=code, defaults={'name': code})
            return User.objects.create_user(username='browser-'+code, company=company, role=role)
        def cookie(person):
            client = Client(); client.force_login(person)
            return client.cookies['sessionid'].value
        employee = Employee.objects.create(company=company, zup_ref='1'*32, zup_server='sql', zup_database='zup',
            zup_full_name='Иванов Иван Иванович', employee_number='ТЕСТ-002', zup_organisation='Тестовая организация',
            zup_position_name='Инженер отдела цифровизации', zup_department='Управление цифровизации', is_active=False,
            zup_card={'birth_date': '1990-02-03', 'inn': '123456789012', 'snils': '111-222-333 44', 'tariff_rate': '123456.78',
                      'position_source': 'Кадровая история', 'hire_date': '2020-01-15', 'dismissal_date': '2025-04-30',
                      'schedule': 'Пятидневная рабочая неделя', 'fte': '1'})
        staffing_name = 'Аналитик /Управление цифровизации/'
        staffing_employee = Employee.objects.create(company=company, zup_ref='2'*32,
            zup_full_name='Сотрудник со штатной позицией', employee_number='ТЕСТ-003',
            zup_card={'staffing_position': staffing_name, 'staffing_since': '2025-02-12',
                'staffing_source': 'Занятость штатных позиций (наименование)',
                'position_state': 'staffing', 'position_diagnostics': {
                    'probes': {'occupancy_position_row': 0}, 'references': {'position': 'b'*32}}})
        config = dict(base=self.live_server_url, listing=reverse('resources:employee_list')+'?include_archived=1',
                      detail=reverse('resources:employee_detail', args=[employee.pk]),
                      staffing_detail=reverse('resources:employee_detail', args=[staffing_employee.pk]),
                      staffing_name='Аналитик', staffing_department='Управление цифровизации',
                      admin_cookie=cookie(user('ADMIN')), planner_cookie=cookie(user('PLANNER')),
                      chromium=shutil.which('chromium'))
        script = Path(__file__).resolve().parents[2]/'tests/browser/employee_cards.js'
        result = subprocess.run(['node', str(script)], cwd=script.parents[2], text=True, capture_output=True,
                                env=dict(os.environ, EMPLOYEE_BROWSER_CONFIG=json.dumps(config)), timeout=90)
        self.assertEqual(result.returncode, 0, result.stdout+'\n'+result.stderr)
        print(result.stdout.strip())
