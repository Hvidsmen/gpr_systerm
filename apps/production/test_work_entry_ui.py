from datetime import timedelta
from decimal import Decimal
from django.test import TestCase
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from uuid import uuid4
from pathlib import Path
from apps.works.models import ComplexObject, MeasurementUnit, ProjectWork, ProjectWorkItem
from apps.projects.models import ConstructionObject,Section
from apps.planning.models import SubobjectWorkOrigin
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

    def test_parent_macro_object_work_is_materialised_and_searchable_on_child(self):
        parent=ConstructionObject.objects.create(company=self.company,project=self.obj.project,name='КТП')
        self.obj.parent=parent;self.obj.save(update_fields=['parent'])
        section=Section.objects.create(company=self.company,construction_object=parent,name='УЗА')
        complex_object=ComplexObject.objects.create(company=self.company,construction_object=parent,section=section,name='УЗА 30')
        source=ProjectWork.objects.create(company=self.company,section=section,complex_object=complex_object,name='Монтаж кабеля',kind='COMPOSITE',unit='компл',is_legacy_structure=True)
        source_item=ProjectWorkItem.objects.create(company=self.company,project_work=source,name='Прокладка кабеля',unit='м',quantity_per_unit=2)
        response=self.page();self.assertEqual(response.status_code,200)
        link=SubobjectWorkOrigin.objects.get(source_work=source)
        self.assertEqual(link.local_work.section.construction_object,self.obj)
        self.assertEqual(link.local_work.complex_object.name,'УЗА 30')
        self.assertTrue(link.local_work.is_legacy_structure)
        self.assertEqual(link.local_work.items.get().name,source_item.name)
        empty=response.context['groups'][0]['formset'].empty_form
        self.assertIn(link.local_work,list(empty.fields['project_work'].queryset))
        self.assertContains(response,'Монтаж кабеля')
        self.assertContains(response,'старая структура')
        from .production_entry import inherit_parent_works
        with CaptureQueriesContext(connection) as queries:inherit_parent_works(self.obj,self.users['DISPATCHER'])
        self.assertLessEqual(len(queries),6)
        self.assertFalse(any('production_projectworkitem' in query['sql'].lower() for query in queries))

    def test_hierarchy_shows_leaf_titles_exact_day_plan_units_and_zero(self):
        response = self.page(); self.assertEqual(response.status_code, 200)
        group = response.context['groups'][0]
        empty=group['formset'].empty_form
        self.assertEqual(empty.fields['project_work'].label,'Комплекс / самостоятельная работа')
        self.assertIn((str(self.complex_object.pk),self.complex_object.name),list(empty.fields['complex_object_filter'].choices))
        structured = next(g for g in group['work_groups'] if g['is_complex'] and g['label']=='Установка камеры')
        self.assertEqual(str(structured['objects'][0]['rows'][0]['complex_object_filter'].value()),str(self.complex_object.pk))
        self.assertEqual(structured['objects'][0]['label'], 'ПК2763')
        self.assertEqual(structured['object_count'], 1)
        self.assertEqual(structured['plan_total'], Decimal(30))
        self.assertEqual(structured['actual_total'], Decimal(0))
        rows = {g['label']:g['objects'][0]['rows'][0].work_display for g in group['work_groups'] if g['is_complex']}
        self.assertEqual(rows['Установка камеры']['path'], f'{self.work.section.name} → ПК2763')
        self.assertEqual(rows['Установка камеры']['plan'], Decimal(30))
        self.assertEqual(rows['Установка камеры']['unit'], 'шт')
        self.assertTrue(rows['Прокладка кабеля']['has_plan'])
        self.assertEqual(rows['Прокладка кабеля']['plan'], 0)
        self.assertContains(response, f'План СМГ на {self.day:%d.%m.%Y}')
        self.assertContains(response, '<h3 data-work-title>Установка камеры</h3>', html=True)
        self.assertContains(response,'complex_object_filter')
        html = response.content.decode()
        self.assertNotIn('Комплексная работа', html)
        self.assertLess(html.index('<span class="production-work-level">Работа в составе комплекса</span>'),html.index('<span class="production-work-level">Комплексный объект</span>'))
        self.assertIn('<span class="production-work-level">Работа в составе комплекса</span><strong>Установка камеры</strong>', html)
        self.assertIn('<span class="production-work-level">Комплексный объект</span><strong>ПК2763</strong>', html)
        self.assertContains(response,'Итого: план 30 шт · факт')
        self.assertEqual(WorkFactClaim.objects.count(), 0, 'Displaying planned work must not reserve it')
        self.assertEqual(DailyFact.objects.count(), 0)

    def test_dispatcher_creates_item_only_in_enabled_other_object(self):
        self.complex_object.allow_dispatcher_work_items=True
        self.complex_object.save(update_fields=['allow_dispatcher_work_items'])
        MeasurementUnit.objects.get_or_create(company=self.company,symbol='м',defaults={'name':'метр'})
        page=self.page()
        self.assertContains(page,'+ Создать работу в составе комплекса')
        response=self.client.post(reverse('production:dispatcher_work_item_create'),{
            'construction_object':self.obj.pk,'date':self.day,'project_work':self.package.pk,
            'name':'Дополнительная прокладка','unit':'м',
        })
        self.assertEqual(response.status_code,200,response.content)
        item=ProjectWorkItem.objects.get(name='Дополнительная прокладка')
        self.assertEqual(item.project_work,self.package);self.assertEqual(item.unit,'м')
        self.assertEqual(response.json()['choice']['item'],item.pk)
        refreshed=self.page()
        self.assertTrue(any(choice['item']==item.pk for choice in refreshed.context['production_work_choices']))
        self.assertIn(item,refreshed.context['groups'][0]['formset'].empty_form.fields['work_item'].queryset)
        self.complex_object.allow_dispatcher_work_items=False
        self.complex_object.save(update_fields=['allow_dispatcher_work_items'])
        denied=self.client.post(reverse('production:dispatcher_work_item_create'),{
            'construction_object':self.obj.pk,'date':self.day,'project_work':self.package.pk,
            'name':'Запрещённая работа','unit':'м',
        })
        self.assertEqual(denied.status_code,404)

    def test_empty_enabled_composite_is_available_for_first_dispatcher_item(self):
        empty=ProjectWork.objects.create(company=self.company,section=self.package.section,complex_object=self.complex_object,
            name='ЭМР. Прочее',kind='COMPOSITE',unit='компл')
        self.complex_object.allow_dispatcher_work_items=True
        self.complex_object.save(update_fields=['allow_dispatcher_work_items'])
        page=self.page();meta={row['id']:row for row in page.context['production_work_meta']}
        self.assertContains(page,'Фильтры работ');self.assertContains(page,'data-production-filters="works"',count=1)
        self.assertIn(empty.pk,meta);self.assertEqual(meta[empty.pk]['kind'],'COMPOSITE')
        self.assertEqual(meta[empty.pk]['complex_object_id'],self.complex_object.pk)
        self.assertIn(empty,page.context['groups'][0]['formset'].empty_form.fields['project_work'].queryset)

    def test_admin_can_create_item_in_enabled_operational_complex(self):
        self.complex_object.allow_dispatcher_work_items=True
        self.complex_object.save(update_fields=['allow_dispatcher_work_items'])
        MeasurementUnit.objects.get_or_create(company=self.company,symbol='шт',defaults={'name':'штука'})
        self.client.force_login(self.users['ADMIN'])
        page=self.page();self.assertContains(page,'+ Создать работу в составе комплекса')
        response=self.client.post(reverse('production:dispatcher_work_item_create'),{
            'construction_object':self.obj.pk,'date':self.day,'project_work':self.package.pk,
            'name':'Работа администратора','unit':'шт',
        })
        self.assertEqual(response.status_code,200,response.content)
        self.assertTrue(self.package.items.filter(name='Работа администратора').exists())

    def test_autosave_resumes_dirty_bound_rows_after_claim_wiring(self):
        source=(Path(__file__).resolve().parents[2]/'static/production/production_autosave.js').read_text(encoding='utf-8')
        self.assertIn("pending=true;message('Восстанавливаем резерв и сохраняем изменения…');timer=setTimeout(()=>flush(),0)",source)

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

    def test_composite_claim_requires_an_item_from_the_selected_work(self):
        scope={'construction_object':self.obj.pk,'date':str(self.day),'session':str(uuid4()),'action':'take'}
        missing=self.client.post(reverse('production:work_claims'),{**scope,'key':f'work:{self.package.pk}'},content_type='application/json')
        self.assertEqual(missing.status_code,400);self.assertIn('Сначала выберите',missing.json()['error'])
        other_package=ProjectWork.objects.create(company=self.company,section=self.package.section,complex_object=self.complex_object,
            name='Другой комплекс работ',kind='COMPOSITE',unit='компл')
        other=ProjectWorkItem.objects.create(company=self.company,project_work=other_package,name='Чужая позиция',unit='шт')
        mismatch=self.client.post(reverse('production:work_claims'),{**scope,'key':f'item:{self.package.pk}:{other.pk}'},content_type='application/json')
        self.assertEqual(mismatch.status_code,400);self.assertIn('не относится',mismatch.json()['error'])
