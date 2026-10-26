from datetime import date
from decimal import Decimal
from unittest.mock import patch
from django.test import TestCase, SimpleTestCase
from django.urls import reverse
from apps.accounts.models import Company, Role
from apps.planning import test_workspace as fixtures
from apps.planning.models import LoadProfile, LoadProfileItem, WorkMonthAllocation
from apps.planning.workspace_services import WorkspaceService, build_workspace_snapshot
from apps.production.models import DailyFact
from .models import ComplexObject, MeasurementUnit, ProjectWorkItem
from .structure import structure_revision
from .templatetags.work_catalogue import highlight


class CatalogueHighlightTests(SimpleTestCase):
    def test_literal_case_insensitive_highlight_escapes_all_input(self):
        self.assertEqual(str(highlight('<img>Кабель [А]</img>', 'кабель [а]')),
            '&lt;img&gt;<mark>Кабель [А]</mark>&lt;/img&gt;')
        self.assertEqual(str(highlight('<script>', '.*')), '&lt;script&gt;')
        self.assertEqual(str(highlight('<script>', '')), '&lt;script&gt;')


class CatalogueUXTests(TestCase):
    setUpTestData = classmethod(fixtures.WorkspaceTests.setUpTestData.__func__)

    def setUp(self):
        self.client.force_login(self.planner)
        self.profile = LoadProfile.objects.create(company=self.company, name='Равномерный')
        LoadProfileItem.objects.create(company=self.company, profile=self.profile, workday_number=1, percentage=100)
        self.a.load_profile = self.b.load_profile = self.profile
        self.a.save(); self.b.save()
        self.url = reverse('works:work_structure_update', args=[self.composite.pk])

    def data(self, **extra):
        data = {'revision': structure_revision(list(self.composite.items.order_by('sequence', 'pk')))}
        for item in [self.a, self.b]:
            data.update({f'norm_{item.pk}':str(item.quantity_per_unit), f'unit_{item.pk}':item.unit,
                f'profile_{item.pk}':str(self.profile.pk), f'share_{item.pk}':str(item.weight)})
        return {**data, **extra}

    def test_global_search_finds_children_preserves_full_path_and_highlights_safely(self):
        facility = ComplexObject.objects.create(company=self.company, construction_object=self.obj,
            section=self.composite.section, name='УЗА 26')
        self.composite.complex_object = facility; self.composite.save()
        self.a.name = 'Кабель [А] <img>'; self.a.save()
        response = self.client.get(reverse('works:work_list'), {'q':'кабель [а]'})
        self.assertEqual(response.context['view_mode'], 'tree')
        self.assertEqual([w.pk for w in response.context['works']], [self.composite.pk])
        self.assertContains(response, '<mark>Кабель [А]</mark> &lt;img&gt;', html=False)
        self.assertContains(response, 'data-tree-reveal')
        self.assertContains(response, 'УЗА 26')
        self.assertContains(response, f'#work-item-{self.a.pk}')
        self.assertNotContains(response, '<img>')
        self.assertEqual(response.context['works'][0].items_count, 2)
        self.assertEqual(len(response.context['works'][0].catalogue_items), 2)
        self.assertEqual(self.client.get(reverse('works:work_list'), {'q':'Composite'}).context['result_count'], 1)
        self.assertEqual(self.client.get(reverse('works:work_list'), {'q':'.*'}).context['result_count'], 0)

    def test_combined_filters_readonly_and_company_scope(self):
        response = self.client.get(reverse('works:work_list'), {'q':'A', 'package':self.composite.pk})
        self.assertTrue(response.context['advanced_filters_open'])
        self.assertContains(response, 'data-bulk-contextual hidden')
        self.planner.assigned_objects.clear()
        self.assertFalse(self.client.get(reverse('works:work_list'), {'q':'A'}).context['works'])
        self.assertEqual(self.client.post(self.url, self.data()).status_code, 404)
        self.planner.role = Role.objects.get(code='OBSERVER'); self.planner.save()
        self.assertEqual(self.client.post(self.url, self.data()).status_code, 403)
        response = self.client.get(reverse('works:work_detail', args=[self.composite.pk]))
        self.assertNotContains(response, 'data-catalogue-edit')
        self.assertNotContains(response, 'Сохранить состав')

    def test_atomic_update_all_fields_and_audit_and_reject_stale_tab(self):
        data = self.data(**{f'norm_{self.a.pk}':'2,500', f'unit_{self.a.pk}':'м',
            f'share_{self.a.pk}':'25', f'share_{self.b.pk}':'75'})
        self.assertEqual(self.client.post(self.url, data).status_code, 302)
        self.a.refresh_from_db(); self.b.refresh_from_db()
        self.assertEqual((self.a.quantity_per_unit,self.a.unit,self.a.weight,self.b.weight),
            (Decimal('2.5'),'м',25,75))
        from apps.audit.models import AuditLog
        self.assertTrue(AuditLog.objects.filter(entity_type='works.ProjectWork', new_value__has_key='structure').exists())
        response = self.client.post(self.url, data)
        self.assertEqual(response.status_code, 400)
        self.assertContains(response, 'Состав изменился в другой вкладке', status_code=400)

    def test_invalid_norm_share_unit_or_foreign_profile_never_partially_save(self):
        foreign = Company.objects.create(name='Other company')
        other_profile = LoadProfile.objects.create(company=foreign, name='Чужой')
        MeasurementUnit.objects.create(company=foreign, symbol='foreign-only', name='Foreign only')
        for invalid in [{f'norm_{self.a.pk}':'0'}, {f'norm_{self.a.pk}':'NaN'},
            {f'unit_{self.a.pk}':'foreign-only'}, {f'profile_{self.a.pk}':other_profile.pk},
            {f'share_{self.a.pk}':'5'}, {f'profile_{self.a.pk}':''}]:
            with self.subTest(invalid=invalid):
                response = self.client.post(self.url, self.data(**{f'norm_{self.b.pk}':'8', **invalid}))
                self.assertEqual(response.status_code, 400)
                self.a.refresh_from_db(); self.b.refresh_from_db()
                self.assertEqual((self.a.quantity_per_unit,self.b.quantity_per_unit,self.a.weight),(2,3,40))
                self.assertEqual(response.context['structure_form'][f'norm_{self.b.pk}'].value(), '8')

    def test_norm_with_fact_is_locked_and_other_rows_are_rolled_back(self):
        DailyFact.objects.create(company=self.company, project_work=self.composite, work_item=self.a,
            date=date(2026,1,5), actual_quantity=1, reported_by=self.planner)
        response = self.client.post(self.url, self.data(**{f'norm_{self.a.pk}':'4',f'norm_{self.b.pk}':'8'}))
        self.assertEqual(response.status_code, 400)
        self.assertContains(response, 'Норматив с фактом или утверждённым планом менять нельзя.', status_code=400)
        self.a.refresh_from_db();self.b.refresh_from_db()
        self.assertEqual((self.a.quantity_per_unit,self.b.quantity_per_unit),(2,3))

    def test_audit_failure_rolls_back_whole_structure(self):
        with patch('apps.audit.models.AuditLog.objects.create', side_effect=RuntimeError('audit')):
            with self.assertRaises(RuntimeError):
                self.client.post(self.url, self.data(**{f'norm_{self.a.pk}':'8'}))
        self.a.refresh_from_db();self.assertEqual(self.a.quantity_per_unit, 2)

    def test_existing_plan_snapshot_keeps_its_norms_units_profiles_and_shares(self):
        version = WorkspaceService.create(self.planner,self.obj,'Snapshot',fixtures.JAN,date(2026,1,31)).baseline_version
        WorkMonthAllocation.objects.create(company=self.company,version=version,work=self.composite,month=fixtures.JAN,quantity=1)
        version = fixtures.WorkspaceTests.approve(self, version)
        before = version.snapshot
        response = self.client.post(self.url,self.data(**{f'share_{self.a.pk}':'25',f'share_{self.b.pk}':'75'}))
        self.assertEqual(response.status_code,302)
        version.refresh_from_db();self.assertEqual(version.snapshot,before)
        self.assertEqual(self.client.post(self.url,self.data(**{f'norm_{self.a.pk}':'8'})).status_code,400)

    def test_return_links_are_local_and_preserve_catalogue_query(self):
        target = reverse('works:work_list') + '?q=A&view=tree'
        response = self.client.get(reverse('works:work_detail',args=[self.composite.pk]), {'return_to':target})
        self.assertEqual(response.context['catalogue_return'], target)
        for unsafe in ['https://example.com/works/', '//example.com/works/', '/accounts/users/', '/works/\\example.com']:
            response = self.client.get(reverse('works:work_detail',args=[self.composite.pk]), {'return_to':unsafe})
            self.assertEqual(response.context['catalogue_return'], reverse('works:work_list'))
        self.assertEqual(self.client.get(self.url).status_code,405)
        response = self.client.post(reverse('works:work_item_update', args=[self.a.pk]), {
            'name':self.a.name,'unit':'м','load_profile':self.profile.pk,
            'quantity_per_unit':2,'weight':40,'return_to':target})
        self.assertRedirects(response,target)

    def test_create_in_complex_preselects_the_location(self):
        facility = ComplexObject.objects.create(company=self.company,construction_object=self.obj,
            section=self.composite.section,name='УЗА 27')
        response = self.client.get(reverse('works:work_create'), {'kind':'COMPOSITE',
            'project':self.obj.project_id,'construction_object':self.obj.pk,
            'section':self.composite.section_id,'complex_object':facility.pk})
        form = response.context['form']
        self.assertEqual(int(form['complex_object'].value()),facility.pk)
        self.assertEqual(int(form['section'].value()),self.composite.section_id)
        self.assertEqual(list(form.fields['complex_object'].queryset),[facility])
