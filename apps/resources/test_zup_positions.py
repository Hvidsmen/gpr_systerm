from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from apps.accounts.models import Company, OneCConnection, Role, User
from apps.accounts import test_one_c_employees as imports
from .models import Employee, Brigade, Position
from .zup_positions import sync_names


class ZupPositionCatalogueTests(TestCase):
    setUp=imports.EmployeesImportTests.setUp
    row=imports.EmployeesImportTests.row
    run_record=imports.EmployeesImportTests.run_record

    def test_employee_import_does_not_create_catalogue_positions_and_repeats(self):
        from apps.accounts.one_c_employees import apply_employees
        rows=[self.row(position='',staffing_position='Аналитик /Управление цифровизации/'),
              self.row('2'*32,position='',staffing_position='аналитик /Другой отдел/',archived=1),
              self.row('3'*32,position='Инженер')]
        result=apply_employees(self.run_record(),{'employees':rows})
        self.assertEqual(result['positions_result']['created'],0)
        self.assertEqual(Brigade.objects.filter(company=self.company).count(),0)
        self.assertFalse(Brigade.objects.filter(name__contains='/').exists())
        analyst=Employee.objects.get(zup_ref='1'*32)
        self.assertIsNone(analyst.position)
        self.assertEqual(apply_employees(self.run_record(),{'employees':rows})['unchanged'],3)
        self.assertEqual(Brigade.objects.filter(company=self.company).count(),0)

    def test_reuse_manual_names_preserves_rates_status_groups_and_ambiguity(self):
        existing=Brigade.objects.create(company=self.company,name='Аналитик',is_active=False,description='Не менять')
        position=existing.position_mapping.position;position.default_hourly_rate=700;position.save()
        result,mapped=sync_names(self.company.pk,['аналитик'])
        self.assertEqual(result['existing'],1);self.assertEqual(mapped['аналитик'].pk,position.pk)
        existing.refresh_from_db();position.refresh_from_db()
        self.assertFalse(existing.is_active);self.assertEqual(existing.description,'Не менять')
        self.assertEqual(position.default_hourly_rate,700)
        Brigade.objects.create(company=self.company,name='АНАЛИТИК')
        result,mapped=sync_names(self.company.pk,['аналитик'])
        self.assertEqual(result['ambiguous'],1);self.assertFalse(mapped)

    def test_button_uses_current_connection_only_and_excludes_manual_or_unknown_labels(self):
        Employee.objects.create(company=self.company,zup_ref='1'*32,zup_server='sql',zup_database='zup',
            zup_synced_at=timezone.now(),zup_card={'staffing_position':'Аналитик /Управление/'})
        Employee.objects.create(company=self.company,zup_ref='2'*32,zup_server='sql',zup_database='old',zup_synced_at=timezone.now(),zup_position_name='Старый источник')
        Employee.objects.create(company=self.company,zup_ref='3'*32,zup_server='sql',zup_database='zup',zup_synced_at=timezone.now(),zup_card={'staffing_position':'Некорректная / подпись'})
        Employee.objects.create(company=self.company,first_name='Ручной',zup_position_name='Ручная должность')
        foreign=Company.objects.create(name='Foreign jobs')
        Employee.objects.create(company=foreign,zup_ref='4'*32,zup_server='sql',zup_database='zup',zup_synced_at=timezone.now(),zup_position_name='Чужая должность')
        url=reverse('resources:brigade_zup_employees')
        self.assertEqual(self.client.get(url).status_code,405)
        self.assertEqual(self.client.post(url).status_code,302)
        self.assertFalse(Brigade.objects.filter(company=self.company).exists())
        self.assertEqual(self.client.post(url).status_code,302)
        self.assertEqual(Brigade.objects.filter(company=self.company).count(),0)
        self.admin.role=Role.objects.get(code='PLANNER');self.admin.save()
        self.assertEqual(self.client.post(url).status_code,403)

    def test_overlong_names_are_not_truncated_or_merged(self):
        result,mapped=sync_names(self.company.pk,['x'*151,'Должность'])
        self.assertEqual((result['created'],result['skipped']),(1,1))
        self.assertEqual(set(mapped),{'должность'})
