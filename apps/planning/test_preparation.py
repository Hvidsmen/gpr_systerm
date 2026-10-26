from datetime import date
from decimal import Decimal
from django.test import TestCase
from django.urls import reverse
from django.core.exceptions import PermissionDenied,ValidationError
from apps.accounts.models import User,Role
from apps.resources.models import Position
from . import test_workspace as fixtures
from . import preparation,estimate_review
from .models import WorkMonthAllocation,ResourceMonthAllocation,WorkTask,WorkResourceAssignment
from .workspace_services import WorkspaceService
from .global_services import GlobalPlanService
from .approval_test_helpers import departments
from .independent_smg import create as create_smg
from .daily_schedule_table import add_entry
from .daily_schedule import save_entry,identities,FIELDS
from .daily_schedule_approval import transition as smg_transition

JAN=date(2026,1,1)


class PreparationTests(TestCase):
    setUpTestData=classmethod(fixtures.WorkspaceTests.setUpTestData.__func__)

    def setUp(self):
        self.estimator=User.objects.create_user(username='parallel-estimator',company=self.company,role=Role.objects.get(code='ESTIMATOR'))
        self.other_manager=User.objects.create_user(username='other-project-manager',company=self.company,role=Role.objects.get(code='MANAGER'))
        self.version=WorkspaceService.create(self.planner,self.obj,'Новый процесс',JAN,date(2026,1,31)).baseline_version
        WorkMonthAllocation.objects.create(company=self.company,version=self.version,work=self.simple,month=JAN,quantity=10)
        self.simple.refresh_from_db()

    def handoff(self,owner=None):
        owner=owner or self.version
        return preparation.handoff(self.planner,owner,self.manager,self.estimator)

    def checked(self,owner=None):
        owner=owner or self.version;task=owner.estimate_reviews.order_by('-pk').first()
        for work_id in estimate_review.work_ids(owner):estimate_review.review_work(self.estimator,task,work_id,True)
        return estimate_review.finish(self.estimator,task,True)

    def test_parallel_tasks_and_manager_submits_only_when_both_branches_ready(self):
        record=self.handoff()
        self.assertTrue(WorkTask.objects.filter(version=self.version,assigned_to=self.manager,key=f'prepare-resources:{record.pk}',status='OPEN').exists())
        self.assertTrue(WorkTask.objects.filter(version=self.version,assigned_to=self.estimator,kind='ESTIMATE',status='OPEN').exists())
        with self.assertRaises(ValidationError):GlobalPlanService.transition(self.version,self.manager,'submit')
        preparation.ready(self.manager,self.version)
        with self.assertRaises(ValidationError):GlobalPlanService.transition(self.version,self.manager,'submit')
        self.checked()
        with self.assertRaises(PermissionDenied):GlobalPlanService.transition(self.version,self.planner,'submit')
        with self.assertRaises(PermissionDenied):GlobalPlanService.transition(self.version,self.other_manager,'submit')
        submitted=GlobalPlanService.transition(self.version,self.manager,'submit')
        self.assertEqual(submitted.status,'SUBMITTED')
        submitted=departments(submitted,self.approvers)
        approved=GlobalPlanService.transition(submitted,self.approvers['CEO'],'approve')
        self.assertEqual(approved.status,'APPROVED')
        self.assertTrue(WorkTask.objects.filter(key=f'prepare-resources:{record.pk}',status='DONE').exists())

    def test_resource_changes_preserve_work_review_but_require_manager_reconfirmation(self):
        self.handoff();task=self.checked();preparation.ready(self.manager,self.version)
        ResourceMonthAllocation.objects.create(company=self.company,version=self.version,month=JAN,kind='labor',brigade=self.brigade,count=3)
        self.assertTrue(estimate_review.check_valid(self.version,task))
        with self.assertRaisesMessage(ValidationError,'подтвердите ресурсы заново'):preparation.require_submission(self.manager,self.version)
        preparation.ready(self.manager,self.version)
        preparation.require_submission(self.manager,self.version)
        self.version.work_allocations.update(quantity=11)
        self.assertFalse(estimate_review.check_valid(self.version,task))
        with self.assertRaises(ValidationError):preparation.require_submission(self.manager,self.version)

    def test_planner_drafts_resources_but_manager_confirms_them(self):
        self.handoff()
        for actor in [self.planner,self.other_manager]:
            with self.assertRaises(PermissionDenied):preparation.require_resource_editor(actor,self.version)
        preparation.require_resource_editor(self.manager,self.version)
        from .work_resources import save
        position=Position.objects.create(company=self.company,name='Ресурс этапа')
        assignment=WorkResourceAssignment(position=position,count=2,start_date=JAN,end_date=JAN,shifts_per_day=1,hours_per_shift=8)
        save(self.planner,self.version,assignment)
        with self.assertRaises(PermissionDenied):preparation.ready(self.planner,self.version)
        save(self.manager,self.version,assignment)
        self.assertEqual(self.version.resource_assignments.count(),1)

    def test_handoff_pages_scope_permissions_and_monthly_resource_creation(self):
        self.client.force_login(self.planner)
        url=reverse('planning:preparation_gpr',args=[self.version.pk])
        response=self.client.post(url,{'action':'handoff','manager':self.manager.pk,'estimator':self.estimator.pk})
        self.assertEqual(response.status_code,302)
        self.client.force_login(self.manager)
        self.assertContains(self.client.get(url,follow=True),'Ресурсы определены')
        data={'action':'save_resources','month':str(JAN),'resources-TOTAL_FORMS':'1','resources-INITIAL_FORMS':'0','resources-0-month':str(JAN),'resources-0-kind':'labor','resources-0-brigade':self.brigade.pk,'resources-0-count':'3','resources-0-hours':'8','resources-0-rate':'0','resources-0-liters':'0','resources-0-balance':'0'}
        response=self.client.post(url,data)
        self.assertEqual(response.status_code,302, getattr(response,'context',None) and response.context['resource_forms'].errors)
        self.assertEqual(self.version.resource_allocations.get(kind='labor').count,3)
        self.client.force_login(self.planner)
        resource=self.version.resource_allocations.get(kind='labor')
        data.update({'resources-INITIAL_FORMS':'1','resources-0-id':resource.pk,'resources-0-count':'4'})
        self.assertEqual(self.client.post(url,data).status_code,302)
        resource.refresh_from_db();self.assertEqual(resource.count,4)
        self.assertEqual(self.client.post(url,{'action':'ready'}).status_code,403)

    def test_smg_allows_planner_resources_but_manager_must_confirm(self):
        schedule=create_smg(self.planner,self.obj,JAN)
        entry=add_entry(self.planner,schedule,{'kind':'work','work':self.simple})
        values=[{'quantity':Decimal(1)} for _ in identities(entry)]
        save_entry(self.planner,entry,values,entry.revision)
        self.handoff(schedule)
        resource=add_entry(self.manager,schedule,{'kind':'labor','brigade':self.brigade,'rate':Decimal(0),'reference':''})
        self.checked(schedule);task=schedule.estimate_reviews.order_by('-pk').first()
        save_entry(self.manager,resource,[{'planned_count':Decimal(2),'planned_hours':Decimal(16)} for _ in identities(resource)],resource.revision)
        self.assertTrue(estimate_review.check_valid(schedule,task))
        resource.refresh_from_db()
        save_entry(self.planner,resource,[{'planned_count':Decimal(3),'planned_hours':Decimal(24)} for _ in identities(resource)],resource.revision)
        self.assertTrue(estimate_review.check_valid(schedule,task))
        with self.assertRaises(PermissionDenied):preparation.ready(self.planner,schedule)
        preparation.ready(self.manager,schedule)
        with self.assertRaises(PermissionDenied):smg_transition(self.planner,schedule,'submit')
        result=smg_transition(self.manager,schedule,'submit')
        self.assertEqual(result.status,'SUBMITTED')
        for role,action in [('PRODUCTION_HEAD','approve_production'),('HR_HEAD','approve_hr'),('TECH_HEAD','approve_tech'),('SECURITY_HEAD','approve_security'),('CEO','approve')]:
            result=smg_transition(self.approvers[role],result,action)
        from .daily_schedule import activate
        with self.assertRaises(PermissionDenied):activate(self.other_manager,result)
        applied=activate(self.manager,result)
        self.assertEqual(applied.status,'APPLIED')

    def test_draft_without_handoff_cannot_skip_preparation(self):
        with self.assertRaises(ValidationError):preparation.require_submission(self.manager,self.version)
        with self.assertRaises(PermissionDenied):self.handoff_as_manager()

    def handoff_as_manager(self):return preparation.handoff(self.manager,self.version,self.manager,self.estimator)

    def test_planner_excel_import_only_writes_works_not_resources(self):
        from .test_gpr_excel import classic
        from .gpr_excel import parse_gpr
        from .gpr_excel_views import apply_gpr
        from uuid import uuid4
        self.simple.excel_aliases=['Монтаж'];self.simple.unit='м3';self.simple.save()
        self.brigade.excel_aliases=['Мастер'];self.brigade.save()
        self.equipment.excel_aliases=['Экскаватор'];self.equipment.save()
        payload=parse_gpr(classic(),'plan.xlsx')
        payload['entries']=[row for row in payload['entries'] if row['column']==0]
        apply_gpr(self.planner,self.obj,payload,{'month_0':JAN},str(uuid4()),target_version=self.version)
        self.assertTrue(self.version.work_allocations.exists())
        self.assertFalse(self.version.resource_allocations.exists())

    def test_smg_interface_allows_only_each_actors_own_section(self):
        schedule=create_smg(self.planner,self.obj,JAN)
        work=add_entry(self.planner,schedule,{'kind':'work','work':self.simple})
        save_entry(self.planner,work,[{'quantity':Decimal(1)} for _ in identities(work)],work.revision)
        self.handoff(schedule)
        resource=add_entry(self.manager,schedule,{'kind':'labor','brigade':self.brigade,'rate':Decimal(0),'reference':''})
        url=reverse('planning:daily_schedule_detail',args=[schedule.pk])
        self.client.force_login(self.manager)
        html=self.client.get(url).content.decode()
        self.assertIn('data-field="planned_count"',html)
        self.assertNotIn('data-field="quantity"',html)
        self.client.force_login(self.planner)
        html=self.client.get(url).content.decode()
        self.assertIn('data-field="quantity"',html)
        self.assertIn('data-field="planned_count"',html)
        self.assertEqual(self.client.post(reverse('planning:daily_schedule_edit',args=[resource.pk]),{}).status_code,200)
        self.client.force_login(self.manager)
        self.assertEqual(self.client.post(reverse('planning:daily_schedule_edit',args=[work.pk]),{}).status_code,403)

    def test_stale_draft_object_cannot_confirm_resources_after_submission(self):
        self.handoff()
        self.version.__class__.objects.filter(pk=self.version.pk).update(status='SUBMITTED')
        with self.assertRaises(ValidationError):preparation.ready(self.manager,self.version)
        self.assertIsNone(preparation.record(self.version).resources_ready_at)

    def test_handoff_creates_visible_manager_inbox_task_without_duplicates(self):
        self.client.force_login(self.planner)
        url=reverse('planning:preparation_gpr',args=[self.version.pk])
        payload={'action':'handoff','manager':self.manager.pk,'estimator':self.estimator.pk}
        response=self.client.post(url,payload)
        self.assertEqual(response.status_code,302)
        task=preparation.resource_tasks(self.version).get(status='OPEN')
        self.client.force_login(self.manager)
        response=self.client.get(reverse('planning:task_list'),{'kind':'RESOURCES','scope':'incoming'})
        self.assertContains(response,task.title)
        self.assertContains(response,reverse('planning:task_detail',args=[task.pk]))
        self.client.force_login(self.planner)
        self.assertEqual(self.client.post(url,payload).status_code,302)
        self.assertEqual(preparation.resource_tasks(self.version).filter(status='OPEN').count(),1)
        self.assertEqual(preparation.resource_tasks(self.version).get(status='OPEN').pk,task.pk)

    def test_explicit_rehandoff_after_admin_closure_creates_new_tasks_preserving_history(self):
        from .task_admin import close_tasks,fingerprint
        from . import task_services
        self.handoff()
        old_manager=preparation.resource_tasks(self.version).get()
        review=self.version.estimate_reviews.latest('pk')
        old_estimator=review.work_tasks.get(kind='ESTIMATE')
        admin=User.objects.create_user(username='handoff-retry-admin',company=self.company,role=Role.objects.get(code='ADMIN'))
        tasks=sorted([old_manager,old_estimator],key=lambda task:task.pk)
        close_tasks(admin,[task.pk for task in tasks],'Задания закрыты для повторной передачи',fingerprint(tasks))
        before=WorkTask.objects.count()
        task_services.sync_company(self.company)
        self.assertEqual(WorkTask.objects.count(),before)
        self.client.force_login(self.planner)
        response=self.client.post(reverse('planning:preparation_gpr',args=[self.version.pk]),{'action':'handoff','manager':self.manager.pk,'estimator':self.estimator.pk})
        self.assertEqual(response.status_code,302)
        new_manager=preparation.resource_tasks(self.version).get(status='OPEN')
        new_estimator=review.work_tasks.get(kind='ESTIMATE',status='OPEN')
        self.assertNotEqual(new_manager.pk,old_manager.pk)
        self.assertNotEqual(new_estimator.pk,old_estimator.pk)
        old_manager.refresh_from_db();old_estimator.refresh_from_db()
        self.assertEqual(old_manager.status,'CLOSED_ADMIN');self.assertEqual(old_estimator.status,'CLOSED_ADMIN')
        self.client.force_login(self.manager)
        response=self.client.get(reverse('planning:task_list'),{'kind':'RESOURCES','scope':'incoming'})
        self.assertContains(response,reverse('planning:task_detail',args=[new_manager.pk]))
        preparation.ready(self.manager,self.version)
        self.checked()
        GlobalPlanService.transition(self.version,self.manager,'submit')
        new_manager.refresh_from_db();old_manager.refresh_from_db()
        self.assertEqual(new_manager.status,'DONE')
        self.assertEqual(old_manager.status,'CLOSED_ADMIN')
