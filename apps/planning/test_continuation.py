from copy import deepcopy
from datetime import date
from decimal import Decimal
from django.test import TestCase
from django.core.exceptions import ValidationError,PermissionDenied
from django.urls import reverse
from apps.accounts.models import Company,Role,User
from apps.works.models import WorkPrice
from apps.works.prices import rate_on
from . import test_workspace as fixtures
from .models import WorkMonthAllocation,ResourceMonthAllocation
from .workspace_services import build_workspace_snapshot
from .continuation import continue_gpr
from .bulk_add import editable_months
from .approval_workflow import current_review

APR=date(2026,4,1)


class ContinuationTests(TestCase):
    @classmethod
    def setUpTestData(cls):fixtures.WorkspaceTests.setUpTestData.__func__(cls)
    create=fixtures.WorkspaceTests.create
    approve=fixtures.WorkspaceTests.approve

    def prepare(self,composite=False):
        from .models import CalendarDay,ProductionCalendar
        calendar=ProductionCalendar.objects.get(company=self.company,is_default=True)
        for day in [5,6]:CalendarDay.objects.create(company=self.company,calendar=calendar,date=APR.replace(day=day),is_working=True)
        self.work=self.composite if composite else self.simple
        w=self.create(works=[self.work])
        ResourceMonthAllocation.objects.create(company=self.company,version=w.baseline_version,month=fixtures.JAN,kind='labor',brigade=self.brigade,count=7,hours=8,rate=123)
        self.source=self.approve(w.baseline_version)
        self.original=deepcopy(self.source.snapshot)
        self.continued=continue_gpr(self.planner,self.source,'Continued GPR',date(2026,4,30))
        self.version=self.continued.baseline_version

    def test_new_period_empty_and_old_months_and_resources_frozen(self):
        self.prepare()
        self.assertEqual(self.continued.start_date,fixtures.JAN)
        self.assertEqual(self.continued.continuation_start,APR)
        self.assertEqual(self.version.previous_version_id,self.source.pk)
        self.assertEqual(list(editable_months(self.version)),[APR])
        self.assertFalse(self.version.work_allocations.exists())
        self.assertFalse(self.version.resource_allocations.exists())
        combined=build_workspace_snapshot(self.version)
        self.assertEqual(combined['works'],self.original['works'])
        self.assertEqual(combined['resources'],self.original['resources'])
        self.source.refresh_from_db();self.assertEqual(self.source.snapshot,self.original)

    def test_new_work_volume_and_prices_do_not_reprice_previous_months(self):
        self.prepare()
        WorkPrice.objects.create(company=self.company,work=self.work,price=99,effective_from=fixtures.JAN)
        WorkMonthAllocation.objects.create(company=self.company,version=self.version,work=self.work,month=APR,quantity=80)
        combined=build_workspace_snapshot(self.version);work=combined['works'][0]
        self.assertEqual([r for r in work['plans'] if r['date']<'2026-04-01'],self.original['works'][0]['plans'])
        self.assertEqual([r for r in work['daily'] if r['date']<'2026-04-01'],self.original['works'][0]['daily'])
        self.assertEqual(sum(Decimal(r['quantity']) for r in work['plans'] if r['date']>='2026-04-01'),80)
        self.assertEqual(rate_on(work['revenue_prices'],fixtures.JAN),10)
        self.assertEqual(rate_on(work['revenue_prices'],APR),99)

    def test_past_allocations_and_import_are_blocked(self):
        from .meeting_import import validate_target_period
        self.prepare()
        with self.assertRaises(ValidationError):WorkMonthAllocation(company=self.company,version=self.version,work=self.work,month=fixtures.JAN,quantity=12).full_clean()
        with self.assertRaises(ValidationError):validate_target_period(self.version,fixtures.JAN,date(2026,1,31))
        self.client.force_login(self.planner)
        editor=self.client.get(reverse('planning:workspace_edit',args=[self.version.pk]))
        self.assertEqual(editor.status_code,200)
        self.assertEqual(editor.context['selected_month'],APR)
        self.assertEqual(self.client.get(reverse('planning:workspace_edit',args=[self.version.pk])+'?month=2026-01-01').status_code,404)

    def test_composite_daily_history_is_preserved(self):
        self.prepare(composite=True)
        WorkMonthAllocation.objects.create(company=self.company,version=self.version,work=self.work,month=APR,quantity=50)
        combined=build_workspace_snapshot(self.version)
        work=combined['works'][0]
        self.assertEqual([r for r in work['plans'] if r['date']<'2026-04-01'],self.original['works'][0]['plans'])
        self.assertEqual([r for r in work['daily'] if r['date']<'2026-04-01'],self.original['works'][0]['daily'])
        self.assertEqual(sum(Decimal(r['quantity']) for r in work['daily'] if r['date']>='2026-04-01'),50)
        self.assertEqual(Decimal(work['daily'][-1]['cumulative']),1050)

    def test_validation_and_permissions_and_creation_form(self):
        self.prepare()
        for end in [date(2026,3,31),date(2026,4,15)]:
            with self.assertRaises(ValidationError):continue_gpr(self.planner,self.source,'Bad',end)
        with self.assertRaises(PermissionDenied):continue_gpr(self.manager,self.source,'No',date(2026,4,30))
        self.client.force_login(self.planner)
        url=reverse('planning:workspace_continue')
        self.assertContains(self.client.get(url+'?source='+str(self.source.pk)),'Продолжить предыдущий ГПР')
        result=self.client.post(url,{'source':self.source.pk,'name':'Continued via UI','end_date':'2026-05-31'})
        self.assertEqual(result.status_code,302)
        self.client.force_login(self.manager);self.assertEqual(self.client.get(url).status_code,403)

    def test_combined_plan_can_be_approved_and_continued_again(self):
        self.prepare()
        WorkMonthAllocation.objects.create(company=self.company,version=self.version,work=self.work,month=APR,quantity=80)
        approved=self.approve(self.version)
        self.assertEqual(approved.snapshot['start'],'2026-01-01')
        self.assertEqual(approved.snapshot['end'],'2026-04-30')
        next_workspace=continue_gpr(self.planner,approved,'Through May',date(2026,5,31))
        combined=build_workspace_snapshot(next_workspace.baseline_version)
        self.assertEqual(combined['works'],approved.snapshot['works'])
        self.assertEqual(combined['resources'],approved.snapshot['resources'])
        self.assertFalse(next_workspace.baseline_version.work_allocations.exists())
