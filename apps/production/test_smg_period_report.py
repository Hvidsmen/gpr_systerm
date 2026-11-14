from datetime import timedelta
from decimal import Decimal
from io import BytesIO
from uuid import uuid4
from openpyxl import load_workbook
from django.test import TestCase
from django.urls import reverse
from apps.planning.models import DailySchedule, DailyScheduleEntry
from apps.resources.models import EquipmentType
from apps.works.models import ProjectWork, ProjectWorkItem
from .models import DailyFact, EmployeeActivity, EquipmentActivity, EquipmentFact, LaborFact, FuelFact
from .transport_models import EquipmentDayRecord
from .camp_fuel_models import CampFuelDay
from .smg_period_report import build


class SmgPeriodReportTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        from .test_dispatch import DispatcherTests
        DispatcherTests.setUpTestData.__func__(cls)
        cls.start = cls.day.replace(day=1)
        cls.selected = cls.start + timedelta(days=3)
        cls.later = cls.start + timedelta(days=15)
        entry = cls.schedule.entries.get(kind='work')
        entry.metadata['unit'] = cls.work.unit
        entry.rows = [{'date': str(cls.start), 'quantity': '5'},
                      {'date': str(cls.later), 'quantity': '7'},
                      {'date': str(cls.start-timedelta(days=1)), 'quantity': '999'}]
        entry.save()

    def entry(self, kind, rows, **kwargs):
        return DailyScheduleEntry.objects.create(schedule=self.schedule, kind=kind,
            key=f'test:{kind}:{self.schedule.entries.count()}', label=kwargs.pop('label', kind), rows=rows, **kwargs)

    def fact(self, quantity, day=None, **kwargs):
        return DailyFact.objects.create(company=self.company, project_work=self.work,
            date=day or self.start, actual_quantity=quantity, **kwargs)

    def request(self, export=False, **query):
        self.client.force_login(self.users['DISPATCHER'])
        return self.client.get(reverse('production:smg_period_export' if export else 'production:smg_period_detail'),
            {'construction_object': self.obj.pk, 'date': str(self.selected), **query})

    def test_full_month_applied_version_only_and_all_reporters(self):
        self.fact(3, reported_by=self.users['ADMIN'])
        self.fact(4, self.later, reported_by=self.users['DISPATCHER'])
        self.fact(100, self.start-timedelta(days=1))
        draft = DailySchedule.objects.create(company=self.company, construction_object=self.obj,
            month=self.start, revision=2, status='DRAFT', created_by=self.users['ADMIN'])
        DailyScheduleEntry.objects.create(schedule=draft, key='work:draft', kind='work',
            metadata={'id': self.work.pk}, rows=[{'date': str(self.start), 'quantity': '777'}])
        report = build(self.obj, self.selected)
        self.assertEqual(report['start'], self.start)
        self.assertGreaterEqual(report['end'], self.later)
        self.assertEqual(report['schedule'], self.schedule)
        self.assertEqual(len(report['works']), 1)
        row = report['works'][0]
        self.assertEqual((row['plan'], row['actual'], row['deviation']), (12, 7, -5))
        self.assertEqual(row['recorded_days'], 2)

    def test_missing_actual_zero_and_outside_plan_are_distinct(self):
        report = build(self.obj, self.selected)
        self.assertIsNone(report['works'][0]['actual'])
        self.fact(0)
        second = ProjectWork.objects.create(company=self.company, section=self.work.section, name='Вне СМГ', unit='т')
        DailyFact.objects.create(company=self.company, project_work=second, date=self.later, actual_quantity=9)
        report = build(self.obj, self.selected)
        rows = {r['label']: r for r in report['works']}
        self.assertEqual(rows[self.work.name]['actual'], 0)
        self.assertEqual(rows[self.work.name]['percent'], 0)
        self.assertIsNone(rows[second.name]['plan'])
        self.assertIsNone(rows[second.name]['deviation'])
        self.assertContains(self.request(), '0 дн. с фактом', count=0)

    def test_work_comments_follow_complex_object_and_are_collapsed_by_default(self):
        self.fact(3,comment='Комментарий за первый день')
        self.fact(4,self.later,comment='Комментарий за второй день')
        report=build(self.obj,self.selected)
        obj=report['work_groups'][0]['works'][0]['objects'][0]
        self.assertEqual([row['text'] for row in obj['comments']],['Комментарий за первый день','Комментарий за второй день'])
        response=self.request()
        self.assertContains(response,'data-report-comment-toggle=')
        self.assertContains(response,'aria-expanded="false"')
        self.assertContains(response,'data-report-comment=')
        self.assertRegex(response.content.decode(),r'data-report-comment="[^"]+" hidden')
        self.assertNotContains(response,'<summary>Комментарий')

    def test_operational_work_outside_smg_is_included(self):
        package = ProjectWork.objects.create(company=self.company, section=self.work.section,
            complex_object=self.work.complex_object, name='Прочее', kind='COMPOSITE')
        key = uuid4()
        DailyFact.objects.create(company=self.company, project_work=package, date=self.start,
            operational_key=key, operational_name='Временное ограждение', operational_unit='м.п.',
            actual_quantity=4, outside_smg=True, reported_by=self.users['DISPATCHER'])
        DailyFact.objects.create(company=self.company, project_work=package, date=self.later,
            operational_key=key, operational_name='Временное ограждение', operational_unit='м.п.',
            actual_quantity=6, outside_smg=True, reported_by=self.users['DISPATCHER'])

        report = build(self.obj, self.selected)
        row = next(row for row in report['works'] if row['label'] == 'Временное ограждение')
        self.assertEqual((row['plan'], row['actual'], row['unit'], row['recorded_days']),
            (None, 10, 'м.п.', 2))
        self.assertContains(self.request(), 'Временное ограждение')
        self.assertContains(self.request(scope='day', date=str(self.start)), 'Временное ограждение')

    def test_legacy_operational_work_without_name_is_not_lost(self):
        package = ProjectWork.objects.create(company=self.company, section=self.work.section,
            complex_object=self.work.complex_object, name='ЭМР. Прочее', kind='COMPOSITE', unit='компл.')
        DailyFact.objects.bulk_create([DailyFact(company=self.company, project_work=package, date=self.start,
            operational_key=uuid4(), operational_name='', operational_unit='', actual_quantity=12,
            outside_smg=True, reported_by=self.users['DISPATCHER'])])

        row = next(row for row in build(self.obj, self.selected)['works'] if row['label'] == 'Работа без названия')
        self.assertEqual((row['plan'], row['actual'], row['unit']), (None, 12, 'компл.'))
        self.assertContains(self.request(), 'Работа без названия')
        self.assertContains(self.request(scope='day', date=str(self.start)), 'Работа без названия')

    def test_no_applied_smg_has_no_gpr_fallback(self):
        self.schedule.status = 'DRAFT'; self.schedule.save()
        self.fact(3)
        report = build(self.obj, self.selected)
        self.assertIsNone(report['schedule'])
        self.assertIsNone(report['works'][0]['plan'])
        self.assertContains(self.request(), 'нет действующего СМГ')

    def test_composite_header_not_double_counted_and_units_not_added(self):
        package = ProjectWork.objects.create(company=self.company, section=self.work.section, name='Комплекс', kind='COMPOSITE')
        item = ProjectWorkItem.objects.create(company=self.company, project_work=package, name='Кабель', unit='м')
        self.entry('work', [{'date': str(self.start), 'quantity': '900'},
            {'date': str(self.start), 'item_id': item.pk, 'quantity': '10'}],
            metadata={'id': package.pk, 'kind': 'COMPOSITE', 'items': [{'id': item.pk, 'name': item.name, 'unit': 'м'}]})
        DailyFact.objects.create(company=self.company, project_work=package, work_item=item, date=self.start, actual_quantity=8)
        report = build(self.obj, self.selected)
        cable = next(r for r in report['works'] if r['label'] == item.name)
        self.assertEqual((cable['plan'], cable['actual']), (10, 8))
        self.assertEqual(len(report['works']), 2)
        self.assertNotIn(Decimal(900), [r['plan'] for r in report['works']])

    def test_changed_work_unit_does_not_compare_incompatible_quantities(self):
        self.work.unit = 'км'; self.work.save()
        self.fact(2)
        report = build(self.obj, self.selected)
        self.assertEqual(len(report['works']), 2)
        self.assertTrue(all(r['deviation'] is None for r in report['works']))

    def test_equipment_daily_override_shift_dedup_and_projected_fact_exclusion(self):
        target = EquipmentType.objects.create(company=self.company, name='Плановый кран')
        self.entry('equipment', [{'date': str(self.start), 'equipment_type_id': target.pk, 'planned_count': '2'},
            {'date': str(self.later), 'equipment_type_id': target.pk, 'planned_count': '3'}])
        activity = EquipmentActivity.objects.create(company=self.company, construction_object=self.obj,
            equipment=self.machine, date=self.start, shift=1, state='WORKING')
        EquipmentActivity.objects.create(company=self.company, construction_object=self.obj,
            equipment=self.machine, date=self.start, shift=2, state='WORKING')
        EquipmentDayRecord.objects.create(company=self.company, construction_object=self.obj,
            equipment=self.machine, camp=self.housing_camp, date=self.start,
            planning_type=target, status='WORKED', activity=activity)
        EquipmentFact.objects.create(company=self.company, construction_object=self.obj,
            equipment_type=self.type, date=self.start, actual_count=1)
        EquipmentActivity.objects.create(company=self.company, construction_object=self.obj,
            equipment=self.machine, date=self.later, shift=1, state='IDLE')
        # Unassigned camp-only use cannot become an object actual.
        EquipmentDayRecord.objects.create(company=self.company, equipment=self.machine,
            camp=self.housing_camp, date=self.start+timedelta(days=1), status='WORKED')
        report = build(self.obj, self.start)
        rows = {r['label']: r for r in report['equipment']}
        self.assertEqual((rows[target.name]['plan'], rows[target.name]['actual']), (2, 1))
        self.assertIsNone(rows[self.type.name]['actual'])

    def test_labor_unique_person_per_day_and_no_projected_double_count(self):
        self.entry('labor', [{'date': str(self.start), 'brigade_id': self.brigade.pk, 'planned_count': '2'},
            {'date': str(self.later), 'brigade_id': self.brigade.pk, 'planned_workers': '3'}])
        for shift in (1, 2):
            EmployeeActivity.objects.create(company=self.company, construction_object=self.obj,
                employee=self.employee, position=self.employee.position, date=self.start, shift=shift)
        LaborFact.objects.create(company=self.company, construction_object=self.obj, brigade=self.brigade,
            date=self.start, actual_workers=1)
        LaborFact.objects.create(company=self.company, construction_object=self.obj, brigade=self.brigade,
            date=self.later, actual_workers=2)
        row = build(self.obj, self.start)['labor'][0]
        self.assertEqual((row['plan'], row['actual']), (2, 1))
        self.assertEqual(row['unit'], 'чел.')

    def test_fuel_object_plan_separate_from_shared_camp_total_no_legacy_double_count(self):
        self.entry('fuel', [{'date': str(self.start), 'fuel_type': 'DIESEL', 'planned_liters': '2000'},
            {'date': str(self.later), 'fuel_type': 'DIESEL', 'planned_liters': '3000'}])
        CampFuelDay.objects.create(company=self.company, camp=self.housing_camp, date=self.start,
            fuel_type='TOTAL', received_liters='3.5', actual_liters='1.25', actual_balance='2.25')
        CampFuelDay.objects.create(company=self.company, camp=self.housing_camp, date=self.start,
            fuel_type='DIESEL', received_liters='900', actual_liters='500', actual_balance='400')
        FuelFact.objects.create(company=self.company, construction_object=self.obj, date=self.start, actual_liters='999')
        report = build(self.obj, self.selected)
        self.assertEqual(report['fuel'][0]['plan'], 5000)
        self.assertIsNone(report['fuel'][0]['actual'])
        self.assertEqual(report['fuel'][0]['unit'], 'л')
        self.assertEqual(report['warehouse'][0]['actual'], Decimal('1.25'))
        self.assertEqual(report['warehouse'][0]['unit'], 'м³')
        self.assertContains(self.request(), 'Расход, м³')
        response = self.request(export=True)
        self.assertEqual(response.status_code, 200)
        book = load_workbook(BytesIO(b''.join(response.streaming_content)))
        self.assertEqual(book['ГСМ · план объекта']['C5'].value, 5000)
        self.assertEqual(book['ГСМ · факт ВЖГ']['D5'].value, 1.25)

    def test_read_requests_do_not_change_catalogues_or_facts(self):
        from apps.resources.models import PositionPlanningBrigade
        before = (PositionPlanningBrigade.objects.count(), DailyFact.objects.count(), EquipmentFact.objects.count())
        self.assertEqual(self.request().status_code, 200)
        self.assertEqual(before, (PositionPlanningBrigade.objects.count(), DailyFact.objects.count(), EquipmentFact.objects.count()))

    def test_scope_permission_and_pinned_revision(self):
        self.assertEqual(self.request(construction_object=self.other.pk).status_code, 400)
        self.assertEqual(self.request(schedule=999999).status_code, 404)
        draft = DailySchedule.objects.create(company=self.company, construction_object=self.obj,
            month=self.start, revision=2, status='DRAFT', created_by=self.users['ADMIN'])
        self.assertEqual(self.request(schedule=draft.pk).status_code, 404)
        self.schedule.status = 'ARCHIVED'; self.schedule.save()
        self.assertEqual(self.request(schedule=self.schedule.pk).status_code, 200)
        self.client.force_login(self.users['PERSONNEL_DISPATCHER'])
        self.assertEqual(self.client.get(reverse('production:smg_period_detail'),
            {'construction_object': self.obj.pk, 'date': self.selected}).status_code, 403)

    def test_excel_preserves_zero_numbers_and_escapes_formula_names(self):
        self.work.name = '=1+2'; self.work.save()
        entry = self.schedule.entries.get(kind='work'); entry.metadata['name'] = self.work.name; entry.save()
        self.fact(0)
        response = self.request(export=True)
        self.assertEqual(response.status_code, 200)
        book = load_workbook(BytesIO(b''.join(response.streaming_content)))
        self.assertEqual(len(book.sheetnames), 5)
        cell=next(cell for row in book['Работы'] for cell in row if cell.column==1 and cell.value=='=1+2')
        self.assertEqual(cell.data_type,'s')
        self.assertEqual(book['Работы'].cell(cell.row,4).value,12)
        self.assertEqual(book['Работы'].cell(cell.row,5).value,0)

    def test_fifth_tab_and_presence_are_available(self):
        self.client.force_login(self.users['DISPATCHER'])
        response = self.client.get(self.url, {'construction_object': self.obj.pk, 'date': str(self.day), 'section': 'works'})
        self.assertContains(response, '6. План / факт СМГ')
        self.assertContains(response, 'data-smg-period')
