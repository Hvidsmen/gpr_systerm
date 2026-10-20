from datetime import date,timedelta
from decimal import Decimal
from unittest.mock import patch
from django.test import TestCase
from django.urls import reverse
from django.core.exceptions import ValidationError,PermissionDenied
from apps.accounts.models import Role,User,Company
from apps.projects.models import ConstructionObject
from apps.production.models import DailyFact
from . import test_workspace as fixtures
from .models import WorkTask,EstimateReviewTask,GlobalPlanVersion
from . import task_services as tasks
from . import estimate_review
from .global_services import GlobalPlanService
from .approval_test_helpers import departments

TODAY=date(2026,1,7)


class TaskTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        fixtures.WorkspaceTests.setUpTestData.__func__(cls)
        cls.dispatcher=User.objects.create_user(username='task-dispatcher',company=cls.company,role=Role.objects.get(code='DISPATCHER'))
        cls.dispatcher.assigned_objects.add(cls.obj)
        cls.estimator=User.objects.create_user(username='task-estimator',company=cls.company,role=Role.objects.get(code='ESTIMATOR'))
    create=fixtures.WorkspaceTests.create
    approve=fixtures.WorkspaceTests.approve

    def test_admin_can_claim_and_complete_estimator_review(self):
        admin=User.objects.create_user(username='estimate-admin',company=self.company,role=Role.objects.get(code='ADMIN'))
        version=GlobalPlanService.transition(self.create().baseline_version,self.planner,'submit')
        queue=WorkTask.objects.get(kind='ESTIMATE',estimate__isnull=True)
        estimate=tasks.claim_estimate(admin,queue)
        self.client.force_login(admin)
        response=self.client.get(reverse('planning:estimate_review_detail',args=[estimate.pk]))
        self.assertTrue(response.context['can_review'])
        for wid in estimate_review.work_ids(version):estimate_review.review_work(admin,estimate,wid,True)
        estimate_review.finish(admin,estimate,True)
        estimate.refresh_from_db();self.assertEqual(estimate.status,'APPROVED')
        self.assertTrue(version.review_rounds.get().decisions.filter(section='ESTIMATE',actor=admin,action='APPROVE').exists())

    def test_superuser_can_edit_another_estimators_review_and_create_remarks(self):
        admin=User.objects.create_user(username='estimate-superuser',company=self.company,is_superuser=True)
        from .models import LoadProfile,LoadProfileItem
        profile=LoadProfile.objects.create(company=self.company,name='Admin review profile')
        LoadProfileItem.objects.create(company=self.company,profile=profile,workday_number=1,percentage=100)
        self.simple.load_profile=profile;self.simple.save()
        version=self.create().baseline_version
        estimate=estimate_review.assign(self.planner,version,self.estimator)
        self.client.force_login(admin)
        response=self.client.get(reverse('planning:task_create')+f'?estimate={estimate.pk}&work={self.simple.pk}')
        self.assertEqual(response.status_code,200)
        estimate_review.change(admin,estimate,self.simple.pk,name='Admin reviewed',price=Decimal('12.00'),effective_from=fixtures.JAN,reason='Проверка цены')
        self.simple.refresh_from_db();self.assertEqual(self.simple.name,'Admin reviewed')
        for wid in estimate_review.work_ids(version):estimate_review.review_work(admin,estimate,wid,True)
        estimate_review.finish(admin,estimate,True)
        estimate.refresh_from_db();self.assertEqual(estimate.assigned_to_id,self.estimator.pk)
        other=User.objects.create_user(username='other-company-admin',company=Company.objects.create(name='Other admin'),is_superuser=True)
        self.client.force_login(other)
        self.assertEqual(self.client.get(reverse('planning:estimate_review_detail',args=[estimate.pk])).status_code,404)

    def test_daily_generation_is_scoped_idempotent_and_preserves_backlog(self):
        tasks.generate_daily(self.dispatcher,today=TODAY)
        tasks.generate_daily(self.dispatcher,today=TODAY)
        task=WorkTask.objects.get(kind='FACT')
        self.assertEqual(task.fact_date,TODAY-timedelta(days=1))
        tasks.generate_daily(self.dispatcher,today=TODAY+timedelta(days=3))
        self.assertEqual(WorkTask.objects.filter(kind='FACT').count(),4)
        task.refresh_from_db();self.assertEqual(task.status,'OPEN')
        self.dispatcher.assigned_objects.clear()
        tasks.generate_daily(self.dispatcher,today=TODAY+timedelta(days=4))
        self.assertFalse(WorkTask.objects.filter(kind='FACT',status='OPEN').exists())

    def test_fact_missing_is_not_zero_and_changes_reopen_confirmation(self):
        version=self.approve(self.create().baseline_version)
        tasks.generate_daily(self.dispatcher,today=TODAY)
        task=WorkTask.objects.get(kind='FACT')
        with self.assertRaises(ValidationError):tasks.complete_fact(self.dispatcher,task,['works','labor','equipment','fuel'])
        fact=DailyFact.objects.create(company=self.company,project_work=self.simple,date=task.fact_date,actual_quantity=0)
        with self.assertRaises(ValidationError):tasks.complete_fact(self.dispatcher,task,['works'])
        tasks.complete_fact(self.dispatcher,task,['works','labor','equipment','fuel'])
        task.refresh_from_db();self.assertEqual(task.status,'DONE')
        fact.actual_quantity=1;fact.save()
        task.refresh_from_db();self.assertEqual(task.status,'OPEN')
        self.assertTrue(task.events.filter(action='Открыто').exists())

    def test_parallel_approval_tasks_ceo_only_after_four_and_stale_round(self):
        version=self.create().baseline_version
        version=GlobalPlanService.transition(version,self.planner,'submit')
        self.assertEqual(WorkTask.objects.filter(kind='APPROVAL',status='OPEN').count(),0)
        with self.assertRaisesMessage(ValidationError,'Сначала требуется подтверждение проверки сметчика'):
            GlobalPlanService.transition(version,self.approvers['PRODUCTION_HEAD'],'approve_production')
        with self.assertRaises(ValidationError):
            GlobalPlanService.transition(version,self.approvers['PRODUCTION_HEAD'],'reject','До проверки сметчика')
        from .approval_workflow import panel
        before=panel(version,self.approvers['PRODUCTION_HEAD'])
        self.assertFalse(before['can_return'])
        self.assertFalse(any(row['can_approve'] for row in before['approval_sections']))
        self.assertFalse(WorkTask.objects.filter(target_role='CEO').exists())
        queue=WorkTask.objects.get(kind='ESTIMATE',estimate__isnull=True)
        est=tasks.claim_estimate(self.estimator,queue)
        for wid in estimate_review.work_ids(version):estimate_review.review_work(self.estimator,est,wid,True)
        estimate_review.finish(self.estimator,est,True)
        self.assertEqual(WorkTask.objects.filter(kind='APPROVAL',status='OPEN').count(),4)
        version=departments(version,self.approvers)
        self.assertEqual(WorkTask.objects.filter(target_role='CEO',status='OPEN').count(),1)
        version=GlobalPlanService.transition(version,self.approvers['CEO'],'reject','Исправить')
        self.assertFalse(WorkTask.objects.filter(kind='APPROVAL',status='OPEN').exists())
        repair=WorkTask.objects.get(key=f'repair:{version.pk}:{version.approval_round}')
        self.assertEqual(repair.assigned_to,self.planner)
        version=GlobalPlanService.transition(version,self.planner,'submit')
        repair.refresh_from_db();self.assertEqual(repair.status,'DONE')
        self.assertEqual(WorkTask.objects.filter(kind='APPROVAL',status='OPEN').count(),4)

    def test_remarks_need_author_acceptance_and_block_estimator(self):
        version=self.create().baseline_version
        est=estimate_review.assign(self.planner,version,self.estimator)
        remark=tasks.create_remark(self.estimator,est,self.planner,'Название','Используйте работу из справочника',work_id=self.simple.pk)
        remark.refresh_from_db();self.assertEqual(remark.assigned_to_id,self.planner.pk)
        for wid in estimate_review.work_ids(version):estimate_review.review_work(self.estimator,est,wid,True)
        with self.assertRaises(ValidationError):estimate_review.finish(self.estimator,est,True)
        tasks.discussion_action(self.planner,remark,'resolve','Проверено и исправлено')
        with self.assertRaises(PermissionDenied):tasks.discussion_action(self.planner,remark,'accept')
        with self.assertRaises(ValidationError):estimate_review.finish(self.estimator,est,True)
        tasks.discussion_action(self.estimator,remark,'accept','Принимаю')
        estimate_review.finish(self.estimator,est,True)
        self.assertEqual(WorkTask.objects.get(estimate=est,kind='ESTIMATE').status,'DONE')

    def test_inbox_ui_company_security_and_dispatcher_scope(self):
        tasks.generate_daily(self.dispatcher,today=TODAY)
        task=WorkTask.objects.get(kind='FACT')
        self.client.force_login(self.dispatcher)
        with patch('django.utils.timezone.localdate',return_value=TODAY):
            self.assertContains(self.client.get(reverse('planning:task_list')),'Внести факт')
            self.assertContains(self.client.get(reverse('planning:task_detail',args=[task.pk])),'Факт заполнен')
            page=self.client.get(reverse('production:fact_day_workspace')+f'?construction_object={self.obj.pk}&date=2026-01-06')
            self.assertEqual(page.status_code,200)
        self.client.force_login(self.estimator)
        self.assertEqual(self.client.get(reverse('planning:task_detail',args=[task.pk])).status_code,404)
        self.assertEqual(self.client.post(reverse('planning:task_detail',args=[task.pk]),{'action':'fact_done','confirmed':['works','labor','equipment','fuel']}).status_code,404)
        other=Company.objects.create(name='Other tasks')
        foreign=User.objects.create_user(username='task-foreign',company=other,role=Role.objects.get(code='ADMIN'))
        self.assertFalse(tasks.visible(foreign).filter(pk=task.pk).exists())

    def test_backfill_does_not_request_approval_again_for_approved_plan(self):
        version=self.approve(self.create().baseline_version)
        tasks.sync_company(self.company)
        self.assertFalse(WorkTask.objects.filter(version=version,kind='APPROVAL',status='OPEN').exists())
        self.assertFalse(WorkTask.objects.filter(version=version,kind='ESTIMATE',status='OPEN').exists())

    def test_dispatcher_cannot_access_unassigned_object_and_can_create_manual_task(self):
        obj=ConstructionObject.objects.create(company=self.company,project=self.obj.project,name='Not assigned')
        self.client.force_login(self.dispatcher)
        response=self.client.get(reverse('production:fact_day_workspace')+f'?construction_object={obj.pk}&date=2026-01-06')
        self.assertContains(response,'Выберите корректный вариант')
        response=self.client.post(reverse('planning:task_create'),{'title':'Уточнить','description':'Проверьте данные','assigned_to':self.planner.pk,'due_date':'2026-10-20','construction_object':self.obj.pk})
        self.assertEqual(response.status_code,302)
        task=WorkTask.objects.get(kind='MANUAL',title='Уточнить')
        tasks.discussion_action(self.planner,task,'resolve','Проверено')
        tasks.discussion_action(self.dispatcher,task,'accept','Принято')
        task.refresh_from_db();self.assertEqual(task.status,'DONE')

    def test_moving_fact_reopens_previous_day_and_actuation_allows_zero_works(self):
        from apps.production.models import DayActuation
        self.approve(self.create().baseline_version)
        tasks.generate_daily(self.dispatcher,today=TODAY)
        task=WorkTask.objects.get(kind='FACT')
        fact=DailyFact.objects.create(company=self.company,project_work=self.simple,date=task.fact_date,actual_quantity=0)
        tasks.complete_fact(self.dispatcher,task,['works','labor','equipment','fuel'])
        fact.date=task.fact_date+timedelta(days=1);fact.save()
        task.refresh_from_db();self.assertEqual(task.status,'OPEN')
        # Missing planned work may be confirmed only with a valid actuation record.
        with self.assertRaises(ValidationError):tasks.complete_fact(self.dispatcher,task,['works','labor','equipment','fuel'])
        DayActuation.objects.create(company=self.company,construction_object=self.obj,date=task.fact_date,actuation_type='FULL_DAY',reason='Погодные условия',reported_by=self.dispatcher)
        tasks.complete_fact(self.dispatcher,task,['works','labor','equipment','fuel'])
        task.refresh_from_db();self.assertEqual(task.status,'DONE')

    def test_missing_planned_people_are_not_implicitly_zero(self):
        from .models import ResourceMonthAllocation
        from apps.production.models import LaborFact
        w=self.create()
        ResourceMonthAllocation.objects.create(company=self.company,version=w.baseline_version,month=fixtures.JAN,kind='labor',brigade=self.brigade,count=2,hours=8,rate=100)
        self.approve(w.baseline_version)
        tasks.generate_daily(self.dispatcher,today=TODAY)
        task=WorkTask.objects.get(kind='FACT')
        DailyFact.objects.create(company=self.company,project_work=self.simple,date=task.fact_date,actual_quantity=0)
        with self.assertRaises(ValidationError):tasks.complete_fact(self.dispatcher,task,['works','labor','equipment','fuel'])
        LaborFact.objects.create(company=self.company,construction_object=self.obj,brigade=self.brigade,date=task.fact_date,actual_workers=0)
        tasks.complete_fact(self.dispatcher,task,['works','labor','equipment','fuel'])
        task.refresh_from_db();self.assertEqual(task.status,'DONE')

    def test_explicit_estimator_assignment_cancels_unclaimed_role_queue(self):
        version=self.create().baseline_version
        version=GlobalPlanService.transition(version,self.planner,'submit')
        queue=WorkTask.objects.get(kind='ESTIMATE',estimate__isnull=True)
        estimate=estimate_review.assign(self.planner,version,self.estimator)
        queue.refresh_from_db();self.assertEqual(queue.status,'SUPERSEDED')
        self.assertEqual(WorkTask.objects.filter(version=version,kind='ESTIMATE',status='OPEN').count(),1)
        with self.assertRaises(PermissionDenied):tasks.claim_estimate(self.estimator,queue)

    def test_observer_can_read_company_queue_but_cannot_post(self):
        tasks.generate_daily(self.dispatcher,today=TODAY)
        task=WorkTask.objects.get(kind='FACT')
        observer=User.objects.create_user(username='task-observer',company=self.company,role=Role.objects.get(code='OBSERVER'))
        self.client.force_login(observer)
        self.assertContains(self.client.get(reverse('planning:task_detail',args=[task.pk])),'Переписка и история')
        self.assertEqual(self.client.post(reverse('planning:task_detail',args=[task.pk]),{'action':'comment','text':'No'}).status_code,403)

    def test_security_task_blocks_and_reopens_existing_ceo_task(self):
        from .approval_test_helpers import estimator_check
        from .approval_workflow import current_review
        version=self.create().baseline_version
        estimator_check(version,self.planner)
        version=GlobalPlanService.transition(version,self.planner,'submit')
        for role,action in [('PRODUCTION_HEAD','approve_production'),('HR_HEAD','approve_hr'),('TECH_HEAD','approve_tech')]:
            version=GlobalPlanService.transition(version,self.approvers[role],action)
        review=current_review(version)
        ceo=tasks.ensure(self.company,f'approval:{review.pk}:CEO',kind='APPROVAL',title='Old CEO task',version=version,review=review,target_role='CEO',assigned_to=self.approvers['CEO'])
        tasks.sync_version(version)
        ceo.refresh_from_db();self.assertEqual(ceo.status,'SUPERSEDED')
        with self.assertRaises(ValidationError):GlobalPlanService.transition(version,self.approvers['CEO'],'approve')
        security=WorkTask.objects.get(review=review,target_role='SECURITY_HEAD')
        self.client.force_login(self.approvers['SECURITY_HEAD'])
        response=self.client.post(reverse('planning:task_detail',args=[security.pk]),{'action':'approve'})
        self.assertEqual(response.status_code,302)
        security.refresh_from_db();self.assertEqual(security.status,'DONE')
        ceo.refresh_from_db();self.assertEqual(ceo.status,'OPEN')


    def test_carryover_is_in_common_inbox_and_legacy_tab_redirects(self):
        from .models import CarryoverReviewTask
        from django.utils import timezone
        version=self.create().baseline_version
        record=CarryoverReviewTask.objects.create(company=self.company,version=version,source_version=version,month=TODAY.replace(day=1),due_date=timezone.localdate(),assigned_to=self.planner)
        self.client.force_login(self.planner)
        response=self.client.get(reverse('planning:task_list'),{'kind':'CARRYOVER'})
        task=WorkTask.objects.get(carryover=record)
        self.assertContains(response,reverse('planning:task_detail',args=[task.pk]))
        self.assertEqual(list(response.context['page']),[task])
        self.assertNotContains(response,'> Задания по остаткам ')
        self.assertEqual(tasks.visible(self.planner).filter(carryover=record).count(),1)
        self.assertRedirects(self.client.get(reverse('planning:carryover_task_list')),reverse('planning:task_list')+'?kind=CARRYOVER',fetch_redirect_response=False)
        self.client.get(reverse('planning:task_list'))
        self.assertEqual(WorkTask.objects.filter(carryover=record).count(),1)
