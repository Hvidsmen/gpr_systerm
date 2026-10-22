from datetime import date
from decimal import Decimal
from itertools import permutations
from django.test import TestCase
from django.urls import reverse
from django.core.exceptions import ValidationError
from apps.works.models import ComplexObject, ProjectWork
from apps.works.forms import ProjectWorkForm
from apps.production.models import DailyFact
from . import test_workspace as fixtures
from .models import WorkMonthAllocation
from .workspace_services import WorkspaceService, build_workspace_snapshot
from .report_services import build_matrix, Source
from .decomposition_report import atoms, grouped, summarize, DecompositionForm

D=Decimal;JAN=fixtures.JAN


class DecompositionTests(TestCase):
    setUpTestData=classmethod(fixtures.WorkspaceTests.setUpTestData.__func__)
    def setUp(self):
        self.client.force_login(self.planner)
        self.facility=ComplexObject.objects.create(company=self.company,construction_object=self.obj,name='УЗА №33')
        self.composite.complex_object=self.facility;self.composite.save()
        workspace=WorkspaceService.create(self.planner,self.obj,'Decomposition',JAN,date(2026,1,31))
        self.version=workspace.baseline_version
        WorkMonthAllocation.objects.create(company=self.company,version=self.version,month=JAN,work=self.composite,quantity=10)
        WorkMonthAllocation.objects.create(company=self.company,version=self.version,month=JAN,work=self.simple,quantity=5)
        self.snapshot=build_workspace_snapshot(self.version)
        DailyFact.objects.create(company=self.company,project_work=self.composite,work_item=self.a,date=date(2026,1,5),actual_quantity=1,reported_by=self.planner)
        self.filters={'start':JAN,'end':date(2026,1,31),'sections':['works'],'mode':'latest'}

    def report(self,basis='real'):
        return build_matrix(self.planner,{**self.filters,'money_basis':basis},source_overrides={self.obj.pk:[Source(self.version,self.snapshot,JAN,date(2026,1,31))]})

    def test_real_and_estimated_completion_are_distinct(self):
        rows=atoms(self.report());composite=[r for r in rows if r['package']==self.composite.name]
        self.assertEqual(sum(r['real_amount'] for r in rows),D('1050'))
        self.assertEqual(sum(r['estimated_amount'] for r in rows),D('1050'))
        self.assertEqual(sum((r['real_fact_amount'] or 0) for r in composite),0)
        self.assertEqual(sum((r['estimated_fact_amount'] or 0) for r in composite),D('20'))
        self.assertEqual(self.facility.quantity,1)
        self.assertEqual(next(s for s in self.snapshot['works'] if s['id']==self.composite.pk)['complex_object_id'],self.facility.pk)

    def test_all_six_grouping_orders_preserve_totals_and_units(self):
        rows=atoms(self.report());expected=summarize(rows)
        for levels in permutations(['complex_object','package','work']):
            nodes=grouped(rows,levels)
            self.assertEqual(sum(n['summary']['real_amount'] for n in nodes),expected['real_amount'])
            self.assertEqual(sum(n['summary']['estimated_amount'] for n in nodes),expected['estimated_amount'])
        self.assertEqual(expected['complex_object_count'],1)
        self.assertEqual({r['unit'] for r in expected['volumes']},{'m','m3'})
        response=self.client.get(reverse('planning:decomposition_gpr',args=[self.version.pk]),{'level_1':'work','level_2':'complex_object','level_3':'package'})
        self.assertEqual(response.status_code,200);self.assertContains(response,'УЗА №33');self.assertContains(response,'Оценочная')
        self.assertEqual(response.context['row_count'],3)

    def test_scope_and_hierarchy_forms(self):
        response=self.client.get(reverse('works:complex_object_list'))
        self.assertContains(response,'УЗА №33')
        response=self.client.get(reverse('works:work_update',args=[self.composite.pk]))
        self.assertContains(response,'id_complex_object')
        self.planner.assigned_objects.clear()
        self.assertNotContains(self.client.get(reverse('works:complex_object_list')),'УЗА №33')
        self.assertEqual(self.client.get(reverse('planning:decomposition_gpr',args=[self.version.pk])).status_code,404)
        self.assertEqual(self.client.get(reverse('works:complex_object_update',args=[self.facility.pk])).status_code,404)

    def test_invalid_repeated_grouping_and_foreign_facility(self):
        response=self.client.get(reverse('planning:decomposition_gpr',args=[self.version.pk]),{'level_1':'work','level_2':'work','level_3':'package'})
        self.assertTrue(response.context['form'].non_field_errors())
        from apps.projects.models import ConstructionObject
        other=ConstructionObject.objects.create(company=self.company,project=self.obj.project,name='Other')
        self.facility.construction_object=other
        with self.assertRaises(ValidationError):self.facility.full_clean()

    def test_estimated_selector_uses_weighted_amount_for_partial_plan(self):
        allocation=self.version.work_allocations.get(work=self.composite)
        allocation.item_quantities={str(self.a.pk):'20',str(self.b.pk):'0'};allocation.save()
        self.snapshot=build_workspace_snapshot(self.version)
        rows=atoms(self.report());estimated=atoms(self.report('estimated'))
        self.assertEqual(sum(r['real_amount'] for r in rows),50)
        self.assertEqual(sum(r['estimated_amount'] for r in rows),450)
        self.assertEqual(sum(r['estimated_amount'] for r in estimated),450)
