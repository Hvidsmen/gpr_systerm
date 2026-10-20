from copy import deepcopy
from datetime import date
from decimal import Decimal as D
from unittest.mock import patch
from django.test import TestCase
from django.urls import reverse
from django.core.exceptions import ValidationError
from .test_merge import WorkMergeTests
from .models import ProjectWork,MeasurementUnit
from .append_service import append_preview,apply_append
from .append_views import AppendForm
from apps.planning.models import WorkMonthAllocation,GlobalPlanVersion,MonthlyPlan
from apps.planning.workspace_services import WorkspaceService,build_workspace_snapshot
from apps.production.models import DailyFact

JAN=date(2026,1,1)


class AppendTests(TestCase):
    setUpTestData=classmethod(WorkMergeTests.setUpTestData.__func__)
    approve=WorkMergeTests.approve

    def setUp(self):
        WorkMergeTests.setUp(self)
        self.composite.items.update(load_profile=self.profile)
        self.workspace=WorkspaceService.create(self.planner,self.obj,'Append plan',JAN,date(2026,1,31))
        self.version=self.workspace.baseline_version
        WorkMonthAllocation.objects.create(company=self.company,version=self.version,work=self.composite,month=JAN,quantity=10,item_quantities={str(self.a.pk):'20',str(self.b.pk):'30'})
        WorkMonthAllocation.objects.create(company=self.company,version=self.version,work=self.simple,month=JAN,quantity=12)
        self.values={**self.data,'target':self.composite,'section':self.composite.section,'name':self.composite.name,'unit':self.composite.unit,'allow_fractional':True,f'norm_{self.simple.pk}':D(2)}

    def facts(self):
        self.fa=DailyFact.objects.create(company=self.company,project_work=self.composite,work_item=self.a,date=JAN.replace(day=5),actual_quantity=20,reported_by=self.planner)
        self.fb=DailyFact.objects.create(company=self.company,project_work=self.composite,work_item=self.b,date=JAN.replace(day=5),actual_quantity=30,reported_by=self.planner)
        self.fs=DailyFact.objects.create(company=self.company,project_work=self.simple,date=JAN.replace(day=5),actual_quantity=12,reported_by=self.planner)

    def append(self):
        report=append_preview(self.planner,[self.simple],self.values)
        return apply_append(self.planner,[self.simple.pk],self.values,report['fingerprint'])

    def test_append_keeps_existing_items_and_fact_and_merges_monthly_allocation(self):
        self.facts()
        report=append_preview(self.planner,[self.simple],self.values)
        self.assertEqual(report['fact_before'],D(10));self.assertEqual(report['fact_after'],D(6))
        self.assertEqual(report['plans'][0]['before'],D(10));self.assertEqual(report['plans'][0]['after'],D(6))
        target,revisions=apply_append(self.planner,[self.simple.pk],self.values,report['fingerprint'])
        self.assertEqual(target.pk,self.composite.pk);self.assertEqual(target.items.count(),3)
        self.a.refresh_from_db();self.assertEqual(self.a.quantity_per_unit,D(2));self.assertEqual(self.a.weight,D(40))
        self.fs.refresh_from_db();self.assertEqual(self.fs.project_work_id,target.pk);self.assertEqual(self.fs.actual_quantity,D(12))
        self.fa.refresh_from_db();self.assertEqual(self.fa.work_item_id,self.a.pk)
        self.assertEqual(self.fs.work_item.weight,D(0));self.assertEqual(target.unit_price,D(100))
        row=self.version.work_allocations.get(work=target)
        self.assertEqual(row.quantity,D(6));self.assertEqual(D(row.item_quantities[str(self.a.pk)]),D(20))
        self.assertEqual(D(row.item_quantities[str(self.fs.work_item_id)]),D(12))
        snapshot=build_workspace_snapshot(self.version)
        self.assertEqual(len([s for s in snapshot['works'] if s['id']==target.pk]),1)
        spec=next(s for s in snapshot['works'] if s['id']==target.pk)
        self.assertEqual(len(spec['items']),3)
        self.assertEqual(sum(D(r['quantity']) for r in spec['plans'] if r['item_id']==self.a.pk),D(20))
        self.assertEqual(sum(D(r['quantity']) for r in spec['plans'] if r['item_id']==self.fs.work_item_id),D(12))

    def test_approved_gpr_snapshot_is_unchanged_and_new_draft_retains_both_sets(self):
        self.approve(self.version)
        self.version.refresh_from_db();frozen=deepcopy(self.version.snapshot)
        target,revisions=self.append()
        self.version.refresh_from_db();self.assertEqual(self.version.snapshot,frozen)
        self.assertEqual(self.version.status,'APPROVED');self.assertTrue(revisions)
        draft=revisions[-1]
        self.assertEqual(draft.status,'DRAFT')
        self.assertEqual(draft.work_allocations.get(work=target).quantity,D(6))
        self.assertEqual(len([s for s in build_workspace_snapshot(draft)['works'] if s['id']==target.pk]),1)

    def test_stale_preview_and_atomic_rollback(self):
        self.facts()
        report=append_preview(self.planner,[self.simple],self.values)
        self.a.quantity_per_unit=3;self.a.save()
        with self.assertRaises(ValidationError):apply_append(self.planner,[self.simple.pk],self.values,report['fingerprint'])
        self.assertEqual(self.composite.items.count(),2)
        report=append_preview(self.planner,[self.simple],self.values)
        with patch('apps.works.merge_service.merge_lower_plans',side_effect=ValidationError('rollback')):
            with self.assertRaises(ValidationError):apply_append(self.planner,[self.simple.pk],self.values,report['fingerprint'])
        self.assertEqual(self.composite.items.count(),2)
        self.fs.refresh_from_db();self.assertEqual(self.fs.project_work_id,self.simple.pk)
        self.assertTrue(self.version.work_allocations.filter(work=self.simple).exists())

    def test_preview_confirm_and_no_duplicate_on_replay(self):
        url=reverse('works:work_append')
        response=self.client.post(url,{'selected':[self.simple.pk],'stage':'select'})
        self.assertContains(response,'Добавить самостоятельные работы')
        data={'selected':[self.simple.pk],'stage':'preview','target':self.composite.pk,f'norm_{self.simple.pk}':'2',f'profile_{self.simple.pk}':self.profile.pk}
        response=self.client.post(url,data)
        self.assertTrue(response.context.get('preview'),response.context['form'].errors)
        self.assertEqual(response.context['report']['plans'][0]['parts'][0]['quantity'],D(12))
        token=response.context['preview']
        response=self.client.post(url,{'stage':'confirm','preview':token})
        self.assertRedirects(response,reverse('works:work_detail',args=[self.composite.pk]),fetch_redirect_response=False)
        self.client.post(url,{'stage':'confirm','preview':token})
        self.assertEqual(self.composite.items.count(),3)

    def test_denied_objects_and_legacy_target_fail_without_changing_data(self):
        self.planner.assigned_objects.clear()
        form=AppendForm({'target':self.composite.pk,f'norm_{self.simple.pk}':'2',f'profile_{self.simple.pk}':self.profile.pk},user=self.planner,works=[self.simple])
        self.assertFalse(form.is_valid());self.assertIn('target',form.errors)
        self.planner.assigned_objects.add(self.obj)
        MonthlyPlan.objects.create(company=self.company,project_work=self.composite,year=2026,month=1,planned_quantity=10,start_date=JAN,end_date=date(2026,1,31))
        with self.assertRaises(ValidationError):append_preview(self.planner,[self.simple],self.values)
        self.assertEqual(self.composite.items.count(),2)

    def test_existing_forecast_can_be_rebuilt_after_appending(self):
        workspace=WorkspaceService.create(self.planner,self.obj,'Forecast append',JAN,date(2026,2,28))
        for work,quantity,items in [(self.composite,10,{str(self.a.pk):'20',str(self.b.pk):'30'}),(self.simple,12,{})]:
            WorkMonthAllocation.objects.create(company=self.company,version=workspace.baseline_version,work=work,month=JAN,quantity=quantity,item_quantities=items)
        self.approve(workspace.baseline_version)
        forecast=WorkspaceService.forecast(workspace,self.planner,JAN,'BASELINE')
        self.append()
        row=forecast.work_allocations.get(work=self.composite)
        self.assertEqual(row.quantity,D(6))
        snapshot=build_workspace_snapshot(forecast)
        spec=next(s for s in snapshot['works'] if s['id']==self.composite.pk)
        self.assertEqual(len(spec['items']),3)
        source_item=self.simple.merged_source.item
        row.item_quantities={str(self.a.pk):'20',str(self.b.pk):'30',str(source_item.pk):'16'}
        row.save()
        snapshot=build_workspace_snapshot(forecast)
        spec=next(s for s in snapshot['works'] if s['id']==self.composite.pk)
        self.assertEqual(sum(D(r['quantity']) for r in spec['plans'] if r['item_id']==source_item.pk),D(16))

    def test_norm_derived_old_volumes_and_exact_days_are_preserved(self):
        row=self.version.work_allocations.get(work=self.composite)
        original=[{'date':'2026-01-06','item_id':self.a.pk,'quantity':'20'},{'date':'2026-01-06','item_id':self.b.pk,'quantity':'30'}]
        WorkMonthAllocation.objects.filter(pk=row.pk).update(item_quantities={},daily_override=original)
        self.append()
        row.refresh_from_db()
        self.assertEqual(row.quantity,D(6))
        self.assertEqual(D(row.item_quantities[str(self.a.pk)]),D(20))
        self.assertEqual([r for r in row.daily_override if r['item_id'] in {self.a.pk,self.b.pk}],original)

    def test_approved_history_reports_old_parent_and_source_facts(self):
        from apps.planning.global_services import comparison
        from apps.planning.report_services import build_matrix
        from apps.planning.test_report_matrix import rows_in
        self.facts();self.approve(self.version)
        self.append()
        self.version.refresh_from_db()
        rows=comparison(self.version)['works']
        self.assertEqual(next(r for r in rows if r['spec']['id']==self.composite.pk)['fact'],D(10))
        self.assertEqual(next(r for r in rows if r['spec']['id']==self.simple.pk)['fact'],D(12))
        report=build_matrix(self.planner,{'start':JAN,'end':date(2026,1,31),'sections':['works'],'mode':'latest'})
        rows=rows_in(report['objects'][0]['sections'][0]['groups'])
        self.assertEqual(next(r for r in rows if r['label']==self.composite.name)['total']['fact'],D(10))
        self.assertEqual(next(r for r in rows if r['label']==self.simple.name)['total']['fact'],D(12))
