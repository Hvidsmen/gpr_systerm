from datetime import timedelta
from decimal import Decimal
from django.test import TestCase
from apps.works.models import ComplexObject, ProjectWork, ProjectWorkItem
from apps.planning.models import DailyScheduleEntry
from . import test_dispatch as fixtures
from .models import DailyFact
from .dispatch_models import WorkFactClaim


def hierarchy_fixture(owner):
    complex_object = ComplexObject.objects.create(company=owner.company, section=owner.work.section,
        construction_object=owner.obj, name='ПК2763')
    package = ProjectWork.objects.create(company=owner.company, section=owner.work.section, complex_object=complex_object,
        name='Монтаж системы видеонаблюдения', kind='COMPOSITE', unit='компл')
    camera = ProjectWorkItem.objects.create(company=owner.company, project_work=package, name='Установка камеры', unit='шт', quantity_per_unit=1)
    cable = ProjectWorkItem.objects.create(company=owner.company, project_work=package, name='Прокладка кабеля', unit='м', quantity_per_unit=5)
    other_day = owner.day + timedelta(days=1 if owner.day.day < 28 else -1)
    entry = DailyScheduleEntry.objects.create(schedule=owner.schedule, key=f'work:{package.pk}', kind='work', label=package.name,
        metadata={'id':package.pk, 'kind':'COMPOSITE'}, rows=[{'date':str(owner.day),'item_id':camera.pk,'quantity':'30'},
            {'date':str(owner.day),'item_id':cable.pk,'quantity':'0'}, {'date':str(other_day),'item_id':camera.pk,'quantity':'90'}])
    return complex_object, package, camera, cable, entry, other_day


class WorkEntryPresentationTests(TestCase):
    setUpTestData = classmethod(fixtures.DispatcherTests.setUpTestData.__func__)
    payload = fixtures.DispatcherTests.payload

    def setUp(self):
        self.complex_object, self.package, self.camera, self.cable, self.entry, self.other_day = hierarchy_fixture(self)
        self.client.force_login(self.users['DISPATCHER'])

    def page(self, day=None):
        return self.client.get(self.url, {'construction_object':self.obj.pk,'date':str(day or self.day),'section':'works'})

    def test_hierarchy_shows_leaf_titles_exact_day_plan_units_and_zero(self):
        response = self.page(); self.assertEqual(response.status_code, 200)
        group = response.context['groups'][0]
        structured = next(g for g in group['work_groups'] if g['is_complex'])
        self.assertEqual(structured['label'], 'ПК2763')
        self.assertEqual(structured['packages'][0]['label'], self.package.name)
        rows = {r.work_display['title']:r.work_display for r in structured['packages'][0]['rows']}
        self.assertEqual(rows['Установка камеры']['plan'], Decimal(30))
        self.assertEqual(rows['Установка камеры']['unit'], 'шт')
        self.assertTrue(rows['Прокладка кабеля']['has_plan'])
        self.assertEqual(rows['Прокладка кабеля']['plan'], 0)
        self.assertContains(response, f'План СМГ на {self.day:%d.%m.%Y}')
        self.assertContains(response, '<h3 data-work-title>Установка камеры</h3>', html=True)
        self.assertEqual(WorkFactClaim.objects.count(), 0, 'Displaying planned work must not reserve it')
        self.assertEqual(DailyFact.objects.count(), 0)

    def test_plan_changes_with_selected_day_and_previous_fact_excludes_this_day(self):
        DailyFact.objects.create(company=self.company, project_work=self.package, work_item=self.camera, date=self.day-timedelta(days=2), actual_quantity=3)
        DailyFact.objects.create(company=self.company, project_work=self.package, work_item=self.camera, date=self.day, actual_quantity=7)
        page = self.page()
        choice = next(c for c in page.context['production_work_choices'] if c['item']==self.camera.pk)
        self.assertEqual(choice['display']['previous'], 3)
        self.assertEqual(choice['display']['plan'], 30)
        other = self.page(self.other_day)
        choice = next(c for c in other.context['production_work_choices'] if c['item']==self.camera.pk)
        self.assertEqual(choice['display']['plan'], 90)

    def test_without_applied_smg_previous_work_is_unplanned_not_a_zero_plan(self):
        self.schedule.status='DRAFT';self.schedule.save(update_fields=['status'])
        DailyFact.objects.create(company=self.company, project_work=self.package, work_item=self.camera, date=self.day-timedelta(days=1), actual_quantity=2)
        response = self.page()
        choice = next(c for c in response.context['production_work_choices'] if c['item']==self.camera.pk)
        self.assertFalse(choice['display']['has_plan']);self.assertIsNone(choice['display']['plan'])
        self.assertContains(response, 'Вне СМГ')

    def test_grouping_preserves_form_names_and_saving_all_leaf_quantities(self):
        data = self.payload('ADMIN', 'works')
        for field,value in list(data.items()):
            if field.endswith('-project_work') and str(value)==str(self.package.pk):
                prefix=field[:-len('project_work')]
                data[prefix+'actual_quantity']='30' if str(data[prefix+'work_item'])==str(self.camera.pk) else '0'
            elif field.endswith('-project_work') and str(value)==str(self.work.pk):
                data[field[:-len('project_work')]+'actual_quantity']='5'
        original = self.entry.rows
        result = self.client.post(self.url, data)
        self.assertEqual(result.status_code, 302)
        self.assertEqual(DailyFact.objects.get(project_work=self.package,work_item=self.camera).actual_quantity,30)
        self.assertEqual(DailyFact.objects.get(project_work=self.package,work_item=self.cable).actual_quantity,0)
        self.entry.refresh_from_db();self.assertEqual(self.entry.rows,original)
