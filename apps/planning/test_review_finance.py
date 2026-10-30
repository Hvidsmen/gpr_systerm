from copy import deepcopy
from datetime import date,timedelta
from decimal import Decimal
from django.test import SimpleTestCase,TestCase
from django.urls import reverse
from apps.works.prices import reprice_plan_rows,change_price
from apps.works.models import ProjectWork,WorkPrice
from . import estimate_review as service,test_estimate_review as fixtures
from .test_price_review_batch import prepare_future
from .workspace_services import build_workspace_snapshot
from .plan_finance import gpr_finances
from .independent_smg import create
from .daily_schedule_table import add_work_branch
from .preparation import handoff


class DatedRowValueTests(SimpleTestCase):
    def test_composite_uses_dated_price_and_group_rounding_preserves_fact_and_closed_days(self):
        spec={'kind':'COMPOSITE','unit_price':'0','items':[{'id':7,'norm':'10','weight':'100'}],
            'revenue_prices':[{'effective_from':'2027-01-01','price':'10'},{'effective_from':'2027-01-03','price':'20'}]}
        rows=[{'date':f'2027-01-0{i}','item_id':7,'quantity':'5','value':'999','origin':'PLAN'} for i in range(1,5)]
        fact={'date':'2027-01-03','item_id':7,'quantity':'1','value':'7','origin':'FACT'};rows.append(fact)
        reprice_plan_rows(spec,rows,start=date(2027,1,2))
        self.assertEqual(rows[0]['value'],'999');self.assertEqual(fact['value'],'7')
        self.assertEqual(sum(Decimal(r['value']) for r in rows[1:4]),25)
        tiny=[{'date':f'2027-01-{i:02}','item_id':None,'quantity':'0.001','origin':'PLAN'} for i in range(1,31)]
        reprice_plan_rows({'kind':'SIMPLE','unit_price':'1','items':[]},tiny)
        self.assertEqual(sum(Decimal(r['value']) for r in tiny),Decimal('.03'))


class PriceFinanceTests(TestCase):
    setUpTestData=classmethod(fixtures.EstimateReviewTests.setUpTestData.__func__)
    create=fixtures.EstimateReviewTests.create
    prepare=fixtures.EstimateReviewTests.prepare
    def setUp(self):prepare_future(self)

    def test_future_price_reaches_gpr_values_and_both_editor_summaries(self):
        ProjectWork.objects.filter(pk=self.simple.pk).update(unit_price=0)
        WorkPrice.objects.filter(work=self.simple).delete()
        service.change(self.estimator,self.task,self.simple.pk,price=Decimal('55'),effective_from=self.day,reason='Согласованная цена')
        self.simple.refresh_from_db();self.assertEqual(self.simple.unit_price,0)
        self.version.work_allocations.exclude(month=self.day).delete()
        self.version.resource_allocations.exclude(month=self.day).delete()
        from .models import ProductionCalendar,CalendarDay
        calendar,_=ProductionCalendar.objects.get_or_create(company=self.company,year=self.day.year,is_default=True,defaults={'name':'Будущий период'})
        for day in [5,6]:CalendarDay.objects.get_or_create(company=self.company,calendar=calendar,date=self.day.replace(day=day),defaults={'is_working':True})
        snapshot=build_workspace_snapshot(self.version)
        spec=next(s for s in snapshot['works'] if s['id']==self.simple.pk)
        allocation=self.version.work_allocations.get(work=self.simple,month=self.day)
        self.assertEqual(sum(Decimal(r['value']) for r in spec['plans'] if r['origin']=='PLAN'),allocation.quantity*55)
        self.assertEqual(gpr_finances(self.version,[allocation])[allocation.pk]['amount'],allocation.quantity*55)
        self.client.force_login(self.planner)
        for url in [reverse('planning:workspace_edit',args=[self.version.pk])+'?month='+str(self.day),reverse('planning:workspace_months',args=[self.version.pk])]:
            response=self.client.get(url);self.assertEqual(response.status_code,200)
            self.assertContains(response,'data-plan-finance');self.assertGreater(response.context['finance_summary']['amount'],0)

    def test_smg_composite_reprices_captured_norms_and_retains_closed_rows_and_rates(self):
        schedule=create(self.planner,self.obj,self.day,name='Проверка суммы')
        entry=add_work_branch(self.planner,schedule,'package:'+str(self.simple.pk))[0]
        entry.metadata.update(kind='COMPOSITE',unit_price='10',items=[{'id':99,'name':'Сохранённый состав','norm':'2','weight':'100','unit':'м'}],revenue_prices=[{'effective_from':str(self.day),'price':'10'}])
        entry.rows=[{'date':str(self.day),'item_id':99,'quantity':'2','value':'10','origin':'PLAN'}, {'date':str(self.day+timedelta(days=1)),'item_id':99,'quantity':'2','value':'0','origin':'PLAN'}]
        entry.save();schedule.locked_before=self.day+timedelta(days=1);schedule.save(update_fields=['locked_before'])
        old=deepcopy(entry.rows[0]);olditems=deepcopy(entry.metadata['items'])
        change_price(self.catalogue_admin,self.simple.pk,Decimal('30'),self.day,'Цена для открытых дней')
        service.reprice_schedule(schedule);entry.refresh_from_db()
        self.assertEqual(entry.rows[0],old);self.assertEqual(entry.metadata['items'],olditems)
        self.assertEqual(Decimal(entry.rows[1]['value']),30)
        self.assertEqual(entry.metadata['revenue_prices'][0]['price'],'10')

    def test_price_approval_is_independent_of_component_cost_share_setup(self):
        from .models import WorkMonthAllocation
        self.composite.items.update(weight=0,load_profile=self.simple.load_profile)
        WorkMonthAllocation.objects.create(company=self.company,version=self.version,work=self.composite,month=self.day,quantity=1)
        for wid in service.work_ids(self.version):service.review_work(self.estimator,self.task,wid,True)
        service.finish(self.estimator,self.task,True)
        self.task.refresh_from_db();self.assertEqual(self.task.status,'APPROVED')

    def test_completed_task_uses_valid_whole_review_fingerprint_for_old_per_work_marks(self):
        for wid in service.work_ids(self.version):service.review_work(self.estimator,self.task,wid,True)
        service.finish(self.estimator,self.task,True);self.task.refresh_from_db()
        decisions=deepcopy(self.task.decisions)
        for mark in decisions.values():mark['signature']='older-before-derived-repricing'
        type(self.task).objects.filter(pk=self.task.pk).update(decisions=decisions)
        self.client.force_login(self.estimator)
        url=reverse('planning:estimate_review_detail',args=[self.task.pk]);response=self.client.get(url)
        self.assertEqual(response.context['pending_count'],0);self.assertEqual(response.context['checked_count'],2)
        self.assertTrue(all(w.review_status=='checked' for w in response.context['works']))
        ProjectWork.objects.filter(pk=self.second.pk).update(unit_price=31)
        response=self.client.get(url);self.assertGreater(response.context['pending_count'],0)
