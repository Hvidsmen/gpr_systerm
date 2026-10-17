from datetime import date
from decimal import Decimal
from django.test import TestCase
from django.urls import reverse
from django.core.exceptions import PermissionDenied,ValidationError
from apps.works.models import ProjectWork,MeasurementUnit
from apps.production.models import DailyFact
from . import estimate_review as service,test_estimate_review as fixtures,test_workspace
from .models import WorkMonthAllocation
from .workspace_services import WorkspaceService,build_workspace_snapshot
from .continuation import continue_gpr


class EstimateWorkflowTests(TestCase):
    setUpTestData=classmethod(fixtures.EstimateReviewTests.setUpTestData.__func__)
    create=fixtures.EstimateReviewTests.create
    prepare=fixtures.EstimateReviewTests.prepare
    approve=test_workspace.WorkspaceTests.approve

    def add_work(self,quantity=10):
        work=ProjectWork.objects.create(company=self.company,section=self.simple.section,name='Second',unit=self.simple.unit,unit_price=5,load_profile=self.simple.load_profile)
        WorkMonthAllocation.objects.create(company=self.company,version=self.version,work=work,month=test_workspace.JAN,quantity=quantity)
        return work

    def test_bulk_review_sort_filter_controls_and_changed_volume(self):
        self.prepare();other=self.add_work()
        service.review_selected(self.estimator,self.task,[self.simple.pk])
        self.client.force_login(self.estimator)
        url=reverse('planning:estimate_review_detail',args=[self.task.pk])
        response=self.client.get(url)
        self.assertEqual([w.pk for w in response.context['works']],[other.pk,self.simple.pk])
        self.assertContains(response,'review-select-all');self.assertContains(response,'review-status')
        row=self.version.work_allocations.filter(work=self.simple).first();row.quantity+=1;row.save()
        response=self.client.get(url)
        work=next(w for w in response.context['works'] if w.pk==self.simple.pk)
        self.assertFalse(work.review_valid)
        response=self.client.post(url,{'action':'bulk_approve','selected':[self.simple.pk,other.pk]})
        self.assertEqual(response.status_code,302)
        self.task.refresh_from_db();self.assertEqual(len(self.task.decisions),2)
        service.finish(self.estimator,self.task,True)

    def test_replacement_excludes_itself_and_identifies_same_named_records(self):
        self.prepare();other=self.add_work()
        other.name=self.simple.name;other.save()
        incompatible=ProjectWork.objects.create(company=self.company,section=self.simple.section,name='Wrong unit',unit='шт')
        self.client.force_login(self.estimator)
        response=self.client.get(reverse('planning:estimate_review_detail',args=[self.task.pk]))
        work=next(w for w in response.context['works'] if w.pk==self.simple.pk)
        self.assertIn(other.pk,{r.pk for r in work.replacements})
        self.assertNotIn(self.simple.pk,{r.pk for r in work.replacements})
        self.assertNotIn(incompatible.pk,{r.pk for r in work.replacements})
        self.assertContains(response,f'ID {other.pk}')

    def test_month_scope_ignores_other_months_and_cannot_check_them(self):
        self.prepare();other=self.add_work()
        approved=self.approve(self.version)
        target=WorkspaceService.forecast(approved.workspace,self.planner,test_workspace.FEB,'BASELINE')
        task=service.assign(self.planner,target,self.estimator)
        self.assertEqual(service.work_ids(target),{self.simple.pk})
        before=service.signature(target)
        old=self.version.work_allocations.filter(work=other).get();old.quantity+=2
        # Approved rows are immutable; a different month's unrelated catalog also has no scope.
        self.assertNotIn(other.pk,service.work_ids(target))
        with self.assertRaises(PermissionDenied):service.review_selected(self.estimator,task,[self.simple.pk,other.pk])
        task.refresh_from_db();self.assertEqual(task.decisions,{})
        self.assertEqual(service.signature(target),before)

    def test_continuation_reviews_only_new_month(self):
        self.prepare();old=self.add_work();approved=self.approve(self.version)
        workspace=continue_gpr(self.planner,approved,'April extension',date(2026,4,30))
        version=workspace.baseline_version
        WorkMonthAllocation.objects.create(company=self.company,version=version,work=self.simple,month=date(2026,4,1),quantity=20)
        task=service.assign(self.planner,version,self.estimator)
        self.assertEqual(service.work_ids(version),{self.simple.pk})
        self.assertEqual(service.review_period(version),(date(2026,4,1),date(2026,4,30)))
        self.assertNotIn(old.pk,service.work_ids(version))
        service.review_selected(self.estimator,task,[self.simple.pk]);service.finish(self.estimator,task,True)

    def test_duplicate_merge_preserves_facts_and_sums_months(self):
        self.prepare();other=self.add_work()
        fact=DailyFact.objects.create(company=self.company,project_work=other,date=test_workspace.JAN,actual_quantity=2)
        service.replace_work(self.estimator,self.task,other.pk,self.simple.pk)
        self.assertEqual(self.version.work_allocations.get(work=self.simple,month=test_workspace.JAN).quantity,310)
        self.assertEqual(service.work_ids(self.version),{self.simple.pk})
        fact.refresh_from_db();self.assertEqual(fact.project_work_id,other.pk)

    def test_composite_merge_keeps_original_volumes_as_items(self):
        self.prepare();other=self.add_work()
        MeasurementUnit.objects.get_or_create(company=self.company,symbol='шт',defaults={'name':'Штука'})
        values={'name':'Составная','section':self.simple.section,'unit':'шт','allow_fractional':True,'work_group':None}
        for work in [self.simple,other]:values[f'norm_{work.pk}']=Decimal(1);values[f'profile_{work.pk}']=self.simple.load_profile
        parent=service.merge_composite(self.estimator,self.task,[self.simple.pk,other.pk],values)
        self.assertEqual(service.work_ids(self.version),{parent.pk})
        allocation=self.version.work_allocations.get(work=parent,month=test_workspace.JAN)
        self.assertEqual(allocation.quantity,10)
        self.assertEqual(sorted(Decimal(v) for v in allocation.item_quantities.values()),[10,300])
        self.assertEqual(parent.items.count(),2)
        self.assertTrue(build_workspace_snapshot(self.version)['works'])

    def test_composite_setup_and_confirmation_in_estimator_interface(self):
        self.prepare();other=self.add_work()
        MeasurementUnit.objects.get_or_create(company=self.company,symbol='шт',defaults={'name':'Штука'})
        self.client.force_login(self.estimator)
        url=reverse('planning:estimate_review_detail',args=[self.task.pk])
        ids=[self.simple.pk,other.pk]
        response=self.client.post(url,{'action':'composite_setup','selected':ids})
        self.assertContains(response,'Норматив на единицу основной работы')
        data={'action':'composite_confirm','selected':ids,'name':'Общая работа','section':self.simple.section_id,'unit':'шт','allow_fractional':'on'}
        for wid in ids:data[f'norm_{wid}']='1';data[f'profile_{wid}']=self.simple.load_profile_id
        response=self.client.post(url,data)
        self.assertEqual(response.status_code,302)
        self.assertEqual(ProjectWork.objects.get(pk=next(iter(service.work_ids(self.version)))).name,'Общая работа')

    def test_forecast_merge_preserves_future_volumes(self):
        self.prepare();other=self.add_work()
        for month,quantity in [(test_workspace.FEB,20),(test_workspace.MAR,30)]:
            WorkMonthAllocation.objects.create(company=self.company,version=self.version,work=other,month=month,quantity=quantity)
        approved=self.approve(self.version)
        target=WorkspaceService.forecast(approved.workspace,self.planner,test_workspace.FEB,'REMAINING')
        before=build_workspace_snapshot(target)
        task=service.assign(self.planner,target,self.estimator)
        values={'name':'Составная','section':self.simple.section,'unit':'шт','allow_fractional':True,'work_group':None}
        for work in [self.simple,other]:values[f'norm_{work.pk}']=Decimal(1);values[f'profile_{work.pk}']=self.simple.load_profile
        parent=service.merge_composite(self.estimator,task,[self.simple.pk,other.pk],values)
        after=build_workspace_snapshot(target)
        def future(snapshot,wid):return sum(Decimal(r['quantity']) for w in snapshot['works'] if w['id']==wid for r in w['daily'] if r['date']>='2026-03-01')
        for work in [self.simple,other]:self.assertEqual(future(before,work.pk),future(after,work.pk))
        self.assertEqual(future(after,parent.pk),0)
        self.assertEqual(service.work_ids(target),{parent.pk})

    def test_similar_names_are_suggestions_only_and_unit_scoped(self):
        from .estimate_similarity import suggestions
        self.prepare()
        other=self.add_work();other.name=self.simple.name+' 1'
        match=ProjectWork(company=self.company,section=self.simple.section,name=self.simple.name,unit='шт',pk=9999)
        pairs=suggestions([self.simple,other,match])
        self.assertEqual(len(pairs),1)
        self.assertEqual({pairs[0]['first'].pk,pairs[0]['second'].pk},{self.simple.pk,other.pk})
