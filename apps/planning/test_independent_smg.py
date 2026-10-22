from datetime import date
from decimal import Decimal
from django.test import TestCase
from django.urls import reverse
from django.core.exceptions import ValidationError, PermissionDenied
from . import test_workspace as fixtures
from .models import DailySchedule, GlobalPlanVersion
from . import independent_smg as service
from .daily_schedule_table import add_entry
from .daily_schedule import save_entry, identities, activate
from .daily_schedule_approval import requires_approval
from .test_daily_schedule import activate as approve_and_activate
from .report_services import select_sources, build_matrix

JAN = fixtures.JAN


class IndependentSMGTests(TestCase):
    setUpTestData = classmethod(fixtures.WorkspaceTests.setUpTestData.__func__)

    def setUp(self):
        self.client.force_login(self.planner)

    def schedule(self):
        return service.create(self.planner,self.obj,JAN)

    def source(self,quantity=10):
        from .workspace_services import WorkspaceService
        from .models import WorkMonthAllocation
        workspace=WorkspaceService.create(self.planner,self.obj,'Reference',JAN,date(2026,1,31))
        source=workspace.baseline_version
        WorkMonthAllocation.objects.create(company=self.company,version=source,month=JAN,work=self.simple,quantity=quantity)
        from .workspace_services import build_workspace_snapshot
        source.snapshot=build_workspace_snapshot(source)
        GlobalPlanVersion.objects.filter(pk=source.pk).update(status='APPROVED',snapshot=source.snapshot)
        source.refresh_from_db()
        return source

    def add(self,schedule,quantity=7):
        entry=add_entry(self.planner,schedule,{'kind':'work','work':self.simple})
        values=[{'quantity':quantity if day.day==5 else 0} for day,item in identities(entry)]
        return save_entry(self.planner,entry,values,entry.revision)

    def test_ui_create_empty_and_catalog_access_without_gpr(self):
        response=self.client.get(reverse('planning:daily_schedule_list'))
        self.assertContains(response,'Планирование ГПР');self.assertContains(response,'Планирование СМГ')
        response=self.client.post(reverse('planning:smg_create'),{'construction_object':self.obj.pk,'month':'2026-01'})
        schedule=DailySchedule.objects.get(construction_object=self.obj)
        self.assertRedirects(response,reverse('planning:daily_schedule_detail',args=[schedule.pk]))
        self.assertIsNone(schedule.source_id)
        response=self.client.get(response.url)
        self.assertContains(response,'ГПР для сравнения не выбран')
        self.add(schedule)
        self.assertContains(self.client.get(response.request['PATH_INFO']),self.simple.name)
        self.assertTrue(requires_approval(schedule))

    def test_reference_changes_comparison_not_smg_and_copy_is_optional(self):
        schedule=self.schedule();entry=self.add(schedule)
        stored=entry.rows
        source=self.source()
        service.reference(self.planner,schedule,source)
        entry.refresh_from_db();schedule.refresh_from_db()
        self.assertEqual(entry.rows,stored)
        self.assertEqual(sum(Decimal(str(r['quantity'])) for r in entry.original),10)
        self.assertEqual(schedule.source_id,source.pk)
        service.reference(self.planner,schedule,None)
        entry.refresh_from_db();self.assertEqual(entry.rows,stored);self.assertEqual(entry.original,[])
        self.assertEqual(source.work_allocations.get().quantity,10)

    def test_empty_reference_and_copy_modes(self):
        source=self.source()
        schedule=service.create(self.planner,self.obj,JAN,source)
        self.assertTrue(all(Decimal(r['quantity'])==0 for e in schedule.entries.all() for r in e.rows))
        DailySchedule.objects.filter(pk=schedule.pk).update(status='ARCHIVED')
        copied=service.create(self.planner,self.obj,JAN,source,True)
        self.assertEqual(sum(Decimal(r['quantity']) for e in copied.entries.filter(kind='work') for r in e.rows),10)
        self.assertEqual(copied.revision,2)

    def test_full_approval_tasks_application_and_operative_report_without_gpr(self):
        schedule=self.schedule();entry=self.add(schedule)
        with self.assertRaises(ValidationError):activate(self.planner,schedule)
        schedule=approve_and_activate(self.planner,schedule)
        filters={'start':JAN,'end':date(2026,1,31),'mode':'current'}
        sources=select_sources(self.planner,filters,[self.obj])
        snapshot=sources[self.obj.pk][0].snapshot
        self.assertTrue(snapshot['standalone_smg'])
        report=build_matrix(self.planner,{**filters,'sections':['works','labor','equipment','fuel']})
        self.assertTrue(report['objects'][0]['sources'])
        response=self.client.get(reverse('planning:estimate_review_detail',args=[schedule.estimate_reviews.latest('pk').pk]))
        self.assertEqual(response.status_code,200)
        self.assertEqual(sum(Decimal(str(r['quantity'])) for r in snapshot['works'][0]['plans']),7)
        self.assertEqual(select_sources(self.planner,{**filters,'mode':'baseline'},[self.obj]),{})
        new=service.revise(self.planner,schedule)
        self.assertEqual(new.revision,2);self.assertEqual(new.entries.get().rows,entry.rows)
        approve_and_activate(self.planner,new)
        schedule.refresh_from_db();self.assertEqual(schedule.status,'ARCHIVED')

    def test_scope_and_invalid_reference(self):
        schedule=self.schedule()
        self.planner.assigned_objects.clear()
        for name in ['daily_schedule_detail','smg_reference','smg_revise']:
            response=self.client.get(reverse('planning:'+name,args=[schedule.pk]))
            self.assertEqual(response.status_code,404)
        self.assertNotContains(self.client.get(reverse('planning:daily_schedule_list')),self.obj.name+'</a>')
        with self.assertRaises(PermissionDenied):service.reference(self.planner,schedule,None)

    def test_reference_validation_and_approval_invalidation(self):
        schedule=self.schedule();self.add(schedule)
        from .approval_test_helpers import estimator_check
        from .estimate_review import check_valid
        task=estimator_check(schedule,self.planner)
        self.assertTrue(check_valid(schedule,task))
        source=self.source()
        service.reference(self.planner,schedule,source)
        task.refresh_from_db();schedule.refresh_from_db()
        self.assertFalse(check_valid(schedule,task))
        self.assertEqual(task.status,'OPEN')
        GlobalPlanVersion.objects.filter(pk=source.pk).update(status='DRAFT')
        with self.assertRaises(ValidationError):
            service.create(self.planner,self.obj,date(2026,2,1),source)
        self.assertFalse(DailySchedule.objects.filter(month=date(2026,2,1)).exists())

    def test_operative_source_is_independent_of_reference_version(self):
        schedule=self.schedule();self.add(schedule)
        approve_and_activate(self.planner,schedule)
        source=self.source(quantity=100)
        filters={'start':JAN,'end':date(2026,1,31),'mode':'current'}
        selected=select_sources(self.planner,filters,[self.obj])[self.obj.pk][0]
        self.assertEqual(sum(Decimal(str(r['quantity'])) for r in selected.snapshot['works'][0]['plans']),7)
        explicit=select_sources(self.planner,{**filters,f'version_{self.obj.pk}':source},[self.obj])[self.obj.pk][0]
        self.assertEqual(sum(Decimal(str(r['quantity'])) for r in explicit.snapshot['works'][0]['plans']),100)
