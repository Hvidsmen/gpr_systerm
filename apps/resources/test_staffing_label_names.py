from decimal import Decimal
from unittest.mock import patch
from django.core.exceptions import ValidationError
from django.test import SimpleTestCase
from django.urls import reverse
from django.utils import timezone
from apps.accounts.test_staffing_sync import StaffingScheduleSourceTests
from apps.accounts.one_c_positions import apply_names
from apps.resources.models import Brigade, BrigadeGroup, BrigadeMerge, Employee
from apps.resources.position_catalog import position_for
from apps.resources.employee_staffing import staffing_parts

JOB = 'Водитель тягача с п/п-площадкой'
DEPARTMENTS = ['Основной производственный персонал', 'Служба главного механика', 'Цеха Талачу']


class StaffingSlashLabelTests(SimpleTestCase):
    def test_job_abbreviations_survive_department_split(self):
        for department in DEPARTMENTS:
            with self.subTest(department=department):
                self.assertEqual(staffing_parts(f'{JOB} /{department}/'), (JOB, department))
                self.assertEqual(staffing_parts(f'{JOB}/{department}/'), (JOB, department))
        self.assertEqual(staffing_parts(JOB), (JOB, ''))
        self.assertEqual(staffing_parts('Водитель а/м с к/м /Участок/'), ('Водитель а/м с к/м', 'Участок'))

    def test_unknown_or_ambiguous_slash_formats_are_unchanged(self):
        for label in ['Слесарь / сварщик', 'Начальник /Участок/Отдел/', '/Отдел/', 'Оператор / /', 'Водитель п/п']:
            self.assertEqual(staffing_parts(label), (label, ''))


class StaffingAbbreviationSyncTests(StaffingScheduleSourceTests):
    def labels(self):
        return [(f'{JOB} /{department}/', False, False) for department in DEPARTMENTS]

    def legacy(self, **values):
        defaults = {'staffing_current': True, 'staffing_synced_at': timezone.now(),
            'staffing_source': self.connection.server.casefold() + '/' + self.connection.database.casefold()}
        defaults.update(values)
        return [Brigade.objects.create(company=self.company, name=label, **defaults) for label, _, _ in self.labels()]

    def test_three_departments_create_one_job_and_refresh_keeps_one(self):
        result = apply_names(self.run_record(), self.report(self.labels()))
        self.assertEqual(result['created'], 1)
        self.assertEqual(result['duplicates'], 2)
        self.assertEqual(list(Brigade.objects.filter(company=self.company).values_list('name', flat=True)), [JOB])
        self.assertEqual(apply_names(self.run_record(), self.report(self.labels()))['created'], 0)

    def test_existing_imported_variants_merge_without_losing_people_or_metadata(self):
        rows = self.legacy()
        group = BrigadeGroup.objects.create(company=self.company, name='Водители')
        target = Brigade.objects.create(company=self.company, name=JOB, group=group, description='Ручные настройки')
        target_position = position_for(target)
        target_position.default_hourly_rate = Decimal('120'); target_position.save()
        employee = Employee.objects.create(company=self.company, first_name='Иван', last_name='Тестовый',
            position=position_for(rows[1]), zup_card={'staffing_position': rows[1].name}, zup_department=DEPARTMENTS[1])
        original_card = dict(employee.zup_card)
        result = apply_names(self.run_record(), self.report(self.labels()))
        self.assertEqual(result['created'], 0)
        self.assertEqual(result['staffing_name_repair'], {'renamed': 3, 'merged': 3, 'warnings': []})
        self.assertEqual(Brigade.objects.filter(company=self.company, merge_source__isnull=True).count(), 1)
        self.assertEqual(set(BrigadeMerge.objects.filter(company=self.company).values_list('target_id', flat=True)), {target.pk})
        employee.refresh_from_db(); target.refresh_from_db(); target_position.refresh_from_db()
        self.assertEqual(employee.position_id, target_position.pk)
        self.assertEqual(employee.zup_card, original_card); self.assertEqual(employee.zup_department, DEPARTMENTS[1])
        self.assertEqual((target.name, target.group_id, target.description), (JOB, group.pk, 'Ручные настройки'))
        self.assertEqual(target_position.default_hourly_rate, Decimal('120'))
        response = self.client.get(reverse('resources:brigade_list'))
        self.assertContains(response, JOB)
        for department in DEPARTMENTS: self.assertNotContains(response, department)
        again = apply_names(self.run_record(), self.report(self.labels()))
        self.assertEqual(again['staffing_name_repair']['merged'], 0)

    def test_without_existing_clean_record_first_id_is_preserved(self):
        rows = self.legacy()
        result = apply_names(self.run_record(), self.report(self.labels()))
        target = Brigade.objects.get(company=self.company, merge_source__isnull=True)
        self.assertEqual((target.pk, target.name, target.staffing_current), (rows[0].pk, JOB, True))
        self.assertEqual(result['staffing_name_repair']['merged'], 2)

    def test_unsynchronized_manual_labels_and_other_source_are_not_renamed(self):
        manual = Brigade.objects.create(company=self.company, name=self.labels()[0][0])
        other = Brigade.objects.create(company=self.company, name=self.labels()[1][0], staffing_synced_at=timezone.now(), staffing_source='other/database')
        result = apply_names(self.run_record(), self.report(self.labels()))
        manual.refresh_from_db(); other.refresh_from_db()
        self.assertEqual(manual.name, self.labels()[0][0]); self.assertEqual(other.name, self.labels()[1][0])
        self.assertEqual(result['staffing_name_repair']['renamed'], 0)

    def test_conflicting_manual_groups_keep_original_rows_and_report_conflict(self):
        rows = self.legacy()
        for index, row in enumerate(rows[:2]):
            row.group = BrigadeGroup.objects.create(company=self.company, name=f'Группа {index}')
            row.save()
        result = apply_names(self.run_record(), self.report(self.labels()))
        self.assertTrue(result['staffing_name_repair']['warnings'])
        self.assertEqual(result['staffing_name_repair']['merged'], 0)
        self.assertFalse(BrigadeMerge.objects.filter(company=self.company).exists())
        for row, source in zip(rows, self.labels()):
            row.refresh_from_db(); self.assertEqual(row.name, source[0])

    def test_collision_preflight_rolls_back_renames(self):
        rows = self.legacy()
        with patch('apps.resources.brigade_batch_merge.preview', return_value={'eligible': [], 'blocked': 1,
                'groups': [{'errors': ['Разные ставки в плане']}]}):
            result = apply_names(self.run_record(), self.report(self.labels()))
        self.assertIn('Разные ставки в плане', result['staffing_name_repair']['warnings'][0])
        self.assertFalse(BrigadeMerge.objects.filter(company=self.company).exists())
        for row, source in zip(rows, self.labels()):
            row.refresh_from_db(); self.assertEqual(row.name, source[0])

    def test_company_and_full_import_rollback(self):
        rows = self.legacy()
        run = self.run_record()
        self.connection.server = 'changed-server'; self.connection.save()
        run.connection = self.connection
        with self.assertRaises(ValidationError):
            apply_names(run, self.report(self.labels()))
        self.assertFalse(BrigadeMerge.objects.filter(company=self.company).exists())
        for row, source in zip(rows, self.labels()):
            row.refresh_from_db(); self.assertEqual(row.name, source[0])
