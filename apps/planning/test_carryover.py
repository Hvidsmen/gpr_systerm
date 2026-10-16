from datetime import date
from decimal import Decimal
from django.test import TestCase
from django.urls import reverse
from django.core.exceptions import ValidationError
from apps.production.models import DailyFact
from apps.works.progress import work_specification
from . import test_workspace as fixtures
from .workspace_services import WorkspaceService
from .models import GlobalPlanVersion, WorkMonthAllocation, WorkCarryover
from .carryover import preview, apply


class CarryoverTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        fixtures.WorkspaceTests.setUpTestData.__func__(cls)

    def prepare(self, composite=False):
        self.work=self.composite if composite else self.simple
        self.workspace=WorkspaceService.create(self.planner,self.obj,'Carry',fixtures.JAN,date(2026,3,31))
        self.source=self.workspace.baseline_version
        spec=work_specification(self.work)
        spec['plans']=[{'date':'2026-01-01','item_id':i.pk,'quantity':str(i.quantity_per_unit*10)} for i in self.work.items.all()] if composite else [{'date':'2026-01-01','item_id':None,'quantity':'100'}]
        GlobalPlanVersion.objects.filter(pk=self.source.pk).update(status='APPROVED',snapshot={'works':[spec]})
        self.source.refresh_from_db()
        self.target=WorkspaceService.new_version(self.workspace,self.planner,'FORECAST',fixtures.FEB,'BASELINE')
        self.client.force_login(self.planner)

    def test_add_remaining_not_double_counted_and_other_months_unchanged(self):
        self.prepare()
        DailyFact.objects.create(company=self.company,project_work=self.work,date=fixtures.JAN,actual_quantity=40)
        row=WorkMonthAllocation.objects.create(company=self.company,version=self.target,work=self.work,month=fixtures.FEB,quantity=20)
        rows=preview(self.target,fixtures.FEB,self.source)
        self.assertEqual(Decimal(rows[0]['quantity']),60)
        self.assertEqual(apply(self.planner,self.target,fixtures.FEB,self.source,'missing',{str(self.work.pk)}),1)
        self.assertEqual(apply(self.planner,self.target,fixtures.FEB,self.source,'missing',{str(self.work.pk)}),0)
        row.refresh_from_db();self.assertEqual(row.quantity,20)
        self.assertFalse(WorkCarryover.objects.get().added)
        url=reverse('planning:workspace_edit',args=[self.target.pk])+'?month=2026-02-01'
        editor=self.client.get(url)
        self.assertEqual(editor.status_code,200)
        self.assertEqual(editor.context['work_carryover_notes'][str(self.work.pk)]['quantity'],'60.000')
        self.assertEqual(apply(self.planner,self.target,fixtures.FEB,self.source,'add',{str(self.work.pk)}),0)
        row.refresh_from_db();self.assertEqual(row.quantity,20)
        self.assertEqual(WorkCarryover.objects.count(),1)
        self.assertEqual(self.target.work_allocations.count(),1)

    def test_composite_transfers_subwork_remainders(self):
        self.prepare(composite=True)
        DailyFact.objects.create(company=self.company,project_work=self.work,work_item=self.a,date=fixtures.JAN,actual_quantity=5)
        rows=preview(self.target,fixtures.FEB,self.source)
        self.assertEqual(Decimal(rows[0]['items'][str(self.a.pk)]),15)
        self.assertEqual(Decimal(rows[0]['items'][str(self.b.pk)]),30)
        self.assertEqual(apply(self.planner,self.target,fixtures.FEB,self.source,'add',{str(self.work.pk)}),1)
        allocation=self.target.work_allocations.get()
        self.assertEqual(Decimal(allocation.item_quantities[str(self.a.pk)]),15)

    def test_preview_confirm_and_updated_facts_require_new_preview(self):
        self.prepare()
        url=reverse('planning:workspace_carryover',args=[self.target.pk])+'?month=2026-02-01'
        self.assertEqual(self.client.get(url).status_code,200)
        response=self.client.post(url,{'source':self.source.pk,'mode':'missing'})
        self.assertEqual(response.status_code,200)
        token=response.context['token']
        DailyFact.objects.create(company=self.company,project_work=self.work,date=fixtures.JAN,actual_quantity=10)
        response=self.client.post(url,{'token':token,'selected':[self.work.pk]})
        self.assertEqual(response.status_code,200)
        self.assertEqual(self.target.work_allocations.count(),0)
        token=self.client.post(url,{'source':self.source.pk,'mode':'missing'}).context['token']
        self.assertEqual(self.client.post(url,{'token':token,'selected':[self.work.pk]}).status_code,302)
        self.assertEqual(self.target.work_allocations.get().quantity,90)

    def test_overfulfillment_and_draft_source(self):
        self.prepare()
        DailyFact.objects.create(company=self.company,project_work=self.work,date=fixtures.JAN,actual_quantity=120)
        self.assertEqual(preview(self.target,fixtures.FEB,self.source),[])
        GlobalPlanVersion.objects.filter(pk=self.source.pk).update(status='DRAFT')
        with self.assertRaises(ValidationError):preview(self.target,fixtures.FEB,self.source)

    def test_access_and_noneditable_month(self):
        from apps.accounts.models import Role, User, Company
        self.prepare()
        url=reverse('planning:workspace_carryover',args=[self.target.pk])+'?month=2026-02-01'
        observer=User.objects.create_user(username='carry-observer',company=self.company,role=Role.objects.get(code='OBSERVER'))
        self.client.force_login(observer)
        self.assertEqual(self.client.get(url).status_code,403)
        self.client.force_login(self.planner)
        self.assertEqual(self.client.get(reverse('planning:workspace_carryover',args=[self.target.pk])+'?month=2026-01-01').status_code,404)
        GlobalPlanVersion.objects.filter(pk=self.target.pk).update(status='APPROVED')
        self.assertEqual(self.client.get(url).status_code,403)
