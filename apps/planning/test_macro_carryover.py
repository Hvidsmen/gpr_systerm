from datetime import date
from decimal import Decimal
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse

from apps.production.models import DailyFact
from apps.works.models import ProjectWork, ProjectWorkItem
from . import carryover, carryover_review
from .models import MacroCarryoverWork, WorkCarryover, CarryoverReviewTask, GlobalPlanVersion
from .test_macro_history import MacroHistoryTests
from . import test_workspace as fixtures


class MacroCarryoverTests(TestCase):
    setUpTestData = classmethod(MacroHistoryTests.setUpTestData.__func__)
    prepare = MacroHistoryTests.prepare
    approve = fixtures.WorkspaceTests.approve

    def test_combined_history_preview_confirm_and_idempotency(self):
        self.prepare()
        self.client.force_login(self.planner)
        url=reverse('planning:workspace_carryover',args=[self.version.pk])+'?month=2026-03-01'
        response=self.client.get(url)
        self.assertEqual(response.context['form'].initial['source'],'history')
        self.assertContains(response,'Подключенная история: КТП-1, КТП-2')
        DailyFact.objects.create(company=self.company,project_work=self.child_works[0],date=fixtures.FEB,actual_quantity=40)
        response=self.client.post(url,{'source':'history','mode':'missing'})
        self.assertEqual([Decimal(r['quantity']) for r in response.context['rows']],[60,200])
        response=self.client.post(url,{'token':response.context['token'],'selected':[w.pk for w in self.child_works]})
        self.assertEqual(response.status_code,302)
        self.assertEqual(sorted(self.version.work_allocations.values_list('quantity',flat=True)),[60,200])
        self.assertEqual(MacroCarryoverWork.objects.count(),2)
        self.assertEqual(set(WorkCarryover.objects.values_list('source_version_id',flat=True)),{v.pk for v in self.sources})
        for mapping in MacroCarryoverWork.objects.select_related('target_work__section','source_work__section'):
            self.assertEqual(mapping.target_work.section.construction_object_id,self.obj.pk)
            self.assertNotEqual(mapping.target_work_id,mapping.source_work_id)
            self.assertIn('Остатки',mapping.target_work.section.name)
        rows=carryover.preview(self.version,fixtures.MAR,None)
        self.assertTrue(all(r['done'] for r in rows))
        self.assertEqual(carryover.apply(self.planner,self.version,fixtures.MAR,None,'missing',{str(r['work_id']) for r in rows}),0)
        self.assertEqual(MacroCarryoverWork.objects.count(),2)
        for source,original in zip(self.sources,self.original):
            source.refresh_from_db();self.assertEqual(source.snapshot,original)

    def test_pinned_approved_round_and_invalid_source(self):
        self.prepare()
        GlobalPlanVersion.objects.filter(pk=self.sources[0].pk).update(snapshot={},status='REJECTED')
        rows=carryover.preview(self.version,fixtures.MAR,None)
        self.assertEqual([Decimal(r['planned']) for r in rows],[100,200])
        with self.assertRaises(ValidationError):
            carryover.preview(self.version,fixtures.MAR,self.version)
        self.assertEqual(len(carryover.preview(self.version,fixtures.MAR,self.sources[0])),1)

    def test_existing_mapped_work_is_annotated_without_changing_monthly_volume(self):
        from .workspace_services import WorkspaceService,build_workspace_snapshot
        self.prepare()
        carryover.apply(self.planner,self.version,fixtures.MAR,None,'missing',{str(w.pk) for w in self.child_works})
        self.version=self.approve(self.version)
        target=WorkspaceService.forecast(self.workspace,self.planner,fixtures.MAR,'BASELINE')
        mapping=MacroCarryoverWork.objects.get(source_work=self.child_works[0])
        allocation=target.work_allocations.get(work_id=mapping.target_work_id)
        allocation.quantity=7;allocation.save()
        rows=carryover.preview(target,fixtures.MAR,None)
        self.assertTrue(all(row['exists'] for row in rows))
        carryover.apply(self.planner,target,fixtures.MAR,None,'missing',{str(row['work_id']) for row in rows})
        allocation.refresh_from_db();self.assertEqual(allocation.quantity,7)
        self.assertFalse(target.work_carryovers.get(work_id=mapping.target_work_id).added)
        snapshot=build_workspace_snapshot(target)
        self.assertEqual(len(snapshot['macro_history_sources']),2)
        self.assertEqual(MacroCarryoverWork.objects.count(),2)

    def test_child_fact_change_invalidates_signed_preview(self):
        self.prepare();self.client.force_login(self.planner)
        url=reverse('planning:workspace_carryover',args=[self.version.pk])+'?month=2026-03-01'
        token=self.client.post(url,{'source':'history','mode':'missing'}).context['token']
        DailyFact.objects.create(company=self.company,project_work=self.child_works[0],date=fixtures.FEB,actual_quantity=10)
        response=self.client.post(url,{'token':token,'selected':[w.pk for w in self.child_works]})
        self.assertContains(response,'Сформируйте предварительный просмотр заново')
        self.assertFalse(self.version.work_allocations.exists())
        self.assertFalse(MacroCarryoverWork.objects.exists())

    def test_forecast_tasks_reconcile_each_child_on_day_one(self):
        self.prepare()
        DailyFact.objects.create(company=self.company,project_work=self.child_works[0],date=fixtures.FEB,actual_quantity=10)
        with patch('django.utils.timezone.localdate',return_value=date(2026,2,15)):
            carryover.apply(self.planner,self.version,fixtures.MAR,None,'missing',{str(w.pk) for w in self.child_works})
        self.assertEqual(CarryoverReviewTask.objects.count(),1)
        self.assertIsNone(CarryoverReviewTask.objects.get().source_version_id)
        self.assertEqual(sorted(self.version.work_allocations.values_list('quantity',flat=True)),[80,200])
        DailyFact.objects.create(company=self.company,project_work=self.child_works[0],date=date(2026,2,28),actual_quantity=60)
        with patch('django.utils.timezone.localdate',return_value=fixtures.MAR):
            for task in CarryoverReviewTask.objects.all():
                rows=carryover_review.preview(task)
                self.assertEqual(len(rows),2)
                carryover_review.reconcile(self.planner,task,{str(r['work_id']) for r in rows})
        self.assertEqual(sorted(self.version.work_allocations.values_list('quantity',flat=True)),[30,200])
        self.assertFalse(WorkCarryover.objects.filter(is_forecast=True).exists())

    def test_approved_macro_has_one_separate_correction_for_both_children(self):
        self.prepare()
        with patch('django.utils.timezone.localdate',return_value=date(2026,2,15)):
            carryover.apply(self.planner,self.version,fixtures.MAR,None,'missing',{str(w.pk) for w in self.child_works})
        self.version=self.approve(self.version)
        original=self.version.snapshot
        DailyFact.objects.create(company=self.company,project_work=self.child_works[0],date=fixtures.FEB,actual_quantity=40)
        DailyFact.objects.create(company=self.company,project_work=self.child_works[1],date=fixtures.FEB,actual_quantity=60)
        with patch('django.utils.timezone.localdate',return_value=fixtures.MAR):
            task=CarryoverReviewTask.objects.get()
            rows=carryover_review.preview(task)
            correction=carryover_review.reconcile(self.planner,task,{str(r['work_id']) for r in rows})
        self.assertNotEqual(correction.pk,self.version.pk)
        self.assertEqual(sorted(correction.work_allocations.values_list('quantity',flat=True)),[60,140])
        self.version.refresh_from_db();self.assertEqual(self.version.snapshot,original)
        from .hierarchy_services import create_smg
        correction=self.approve(correction)
        mapping=MacroCarryoverWork.objects.get(source_work=self.child_works[0])
        quantities={m.target_work_id:0 for m in MacroCarryoverWork.objects.all()}
        quantities[mapping.target_work_id]=60
        smg=create_smg(self.planner,correction,self.children[0],fixtures.MAR,quantities)
        self.assertEqual(sum(smg.baseline_version.work_allocations.values_list('quantity',flat=True)),60)

    def test_composite_mapping_and_reconciliation(self):
        self.prepare()
        source=self.sources[0];work=self.child_works[0]
        # Create a fresh frozen approved round with a composite child work.
        work.kind='COMPOSITE';work.save()
        a=ProjectWorkItem.objects.create(company=self.company,project_work=work,name='A',unit='m',quantity_per_unit=2,weight=40)
        b=ProjectWorkItem.objects.create(company=self.company,project_work=work,name='B',unit='m3',quantity_per_unit=3,weight=60)
        from apps.works.progress import work_specification
        spec=work_specification(work)
        spec['plans']=[{'date':'2026-02-01','item_id':a.pk,'quantity':'200'},{'date':'2026-02-01','item_id':b.pk,'quantity':'300'}]
        # The connected review is immutable in production; this is fixture setup only.
        link=self.workspace.macro_history_sources.get(construction_object=self.children[0])
        type(link.review).objects.filter(pk=link.review_id).update(snapshot={'works':[spec]})
        DailyFact.objects.create(company=self.company,project_work=work,work_item=a,date=fixtures.FEB,actual_quantity=10)
        with patch('django.utils.timezone.localdate',return_value=date(2026,2,15)):
            carryover.apply(self.planner,self.version,fixtures.MAR,source,'missing',{str(work.pk)})
        mapping=MacroCarryoverWork.objects.get()
        allocation=self.version.work_allocations.get()
        self.assertEqual(set(allocation.item_quantities),set(str(v) for v in mapping.item_mapping.values()))
        self.assertEqual(Decimal(allocation.item_quantities[str(mapping.item_mapping[str(a.pk)])]),180)
        DailyFact.objects.create(company=self.company,project_work=work,work_item=a,date=date(2026,2,28),actual_quantity=50)
        with patch('django.utils.timezone.localdate',return_value=fixtures.MAR):
            task=CarryoverReviewTask.objects.get()
            rows=carryover_review.preview(task)
            carryover_review.reconcile(self.planner,task,{str(r['work_id']) for r in rows})
        allocation.refresh_from_db()
        self.assertEqual(Decimal(allocation.item_quantities[str(mapping.item_mapping[str(a.pk)])]),140)
        self.assertEqual(ProjectWork.objects.filter(section__construction_object=self.children[0]).count(),1)
