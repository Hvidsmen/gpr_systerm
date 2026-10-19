from django.test import TestCase
from django.urls import reverse
from django.core.exceptions import ValidationError,PermissionDenied
from . import test_daily_schedule as fixtures,daily_schedule as daily
from .approval_test_helpers import estimator_check
from .daily_schedule_approval import transition,requires_approval
from .task_services import sync_company
from .models import WorkTask,DailyScheduleReview


class SmgHandoffTests(TestCase):
    setUpTestData=classmethod(fixtures.DailyScheduleTests.setUpTestData.__func__)
    prepare=fixtures.DailyScheduleTests.prepare
    values=fixtures.DailyScheduleTests.values

    def checked(self,amount=100):
        self.prepare()
        daily.save_entry(self.planner,self.entry,self.values(self.entry,amount),0)
        return estimator_check(self.schedule,self.planner)

    def test_equal_month_totals_create_planner_task_for_direct_operative_save(self):
        self.checked()
        self.assertFalse(requires_approval(self.schedule))
        task=WorkTask.objects.get(kind='SUBMISSION')
        self.assertEqual(task.assigned_to_id,self.planner.pk)
        self.assertIn('оперативный план',task.title)
        self.assertFalse(WorkTask.objects.filter(schedule=self.schedule,kind='APPROVAL').exists())
        response=self.client.post(reverse('planning:task_detail',args=[task.pk]),{'action':'launch'})
        self.assertEqual(response.status_code,302)
        self.schedule.refresh_from_db();self.assertEqual(self.schedule.status,'APPLIED')
        task.refresh_from_db();self.assertEqual(task.status,'DONE')
        self.assertFalse(DailyScheduleReview.objects.exists())

    def test_deviation_requires_three_heads_then_ceo_then_application(self):
        self.checked(120)
        with self.assertRaises(ValidationError):daily.activate(self.planner,self.schedule)
        launch=WorkTask.objects.get(kind='SUBMISSION')
        self.client.post(reverse('planning:task_detail',args=[launch.pk]),{'action':'launch'})
        self.schedule.refresh_from_db();self.assertEqual(self.schedule.status,'SUBMITTED')
        launch.refresh_from_db();self.assertEqual(launch.status,'DONE')
        self.assertEqual(WorkTask.objects.filter(schedule=self.schedule,kind='APPROVAL',status='OPEN').count(),3)
        self.assertFalse(WorkTask.objects.filter(schedule=self.schedule,target_role='CEO').exists())
        with self.assertRaises(ValidationError):transition(self.approvers['CEO'],self.schedule,'approve')
        with self.assertRaises(PermissionDenied):daily.save_entry(self.planner,self.entry,self.values(self.entry),1)
        frozen=self.schedule.approval_reviews.get().snapshot
        for role in ['PRODUCTION_HEAD','HR_HEAD','TECH_HEAD','CEO']:
            task=WorkTask.objects.get(schedule=self.schedule,kind='APPROVAL',target_role=role,status='OPEN')
            self.client.force_login(self.approvers[role])
            self.assertEqual(self.client.post(reverse('planning:task_detail',args=[task.pk]),{'action':'approve'}).status_code,302)
        self.schedule.refresh_from_db();self.assertEqual(self.schedule.status,'APPROVED')
        self.assertEqual(self.schedule.approval_reviews.get().snapshot,frozen)
        apply_task=WorkTask.objects.get(schedule=self.schedule,kind='SUBMISSION',status='OPEN')
        self.client.force_login(self.planner)
        self.client.post(reverse('planning:task_detail',args=[apply_task.pk]),{'action':'launch'})
        self.schedule.refresh_from_db();self.assertEqual(self.schedule.status,'APPLIED')
        self.source.refresh_from_db();self.assertEqual(self.source.snapshot,self.original)

    def test_existing_checked_schedule_backfills_one_task_and_rejects_stale_check(self):
        self.checked()
        WorkTask.objects.filter(kind='SUBMISSION').delete()
        sync_company(self.company);sync_company(self.company)
        task=WorkTask.objects.get(kind='SUBMISSION')
        self.entry.refresh_from_db()
        daily.save_entry(self.planner,self.entry,self.values(self.entry,125),self.entry.revision)
        sync_company(self.company)
        task.refresh_from_db();self.assertEqual(task.status,'SUPERSEDED')
        with self.assertRaises(ValidationError):transition(self.planner,self.schedule,'submit')

    def test_pre_scope_estimator_check_is_preserved_for_backfill(self):
        from apps.works.models import ProjectWork
        from .models import DailyScheduleEntry
        from . import estimate_review
        self.prepare()
        old=ProjectWork.objects.create(company=self.company,section=self.simple.section,name='Old empty history',unit='m')
        DailyScheduleEntry.objects.create(schedule=self.schedule,key=f'work:{old.pk}',kind='work',label=old.name,metadata={'id':old.pk},rows=[],original=[])
        check=estimator_check(self.schedule,self.planner)
        check.fingerprint=estimate_review.signature(self.schedule,legacy=True);check.save(update_fields=['fingerprint'])
        WorkTask.objects.filter(kind='SUBMISSION').delete()
        sync_company(self.company)
        self.assertEqual(WorkTask.objects.filter(kind='SUBMISSION',status='OPEN').count(),1)
        daily.activate(self.planner,self.schedule)

    def test_handoff_stays_with_original_planner_when_admin_assigned_check(self):
        from apps.accounts.models import User,Role
        check=self.checked()
        admin=User.objects.create_user(username='handoff-admin',company=self.company,role=Role.objects.get(code='ADMIN'))
        check.created_by=admin;check.save(update_fields=['created_by'])
        WorkTask.objects.filter(kind='SUBMISSION').delete()
        sync_company(self.company)
        self.assertEqual(WorkTask.objects.get(kind='SUBMISSION').assigned_to_id,self.planner.pk)

    def test_rejection_preserves_round_and_requires_new_estimator_check(self):
        self.checked(120)
        self.schedule=transition(self.planner,self.schedule,'submit')
        review=self.schedule.approval_reviews.get();frozen=review.snapshot
        self.schedule=transition(self.approvers['PRODUCTION_HEAD'],self.schedule,'reject','Исправить объем')
        self.assertEqual(self.schedule.status,'DRAFT')
        self.assertFalse(WorkTask.objects.filter(schedule=self.schedule,kind='APPROVAL',status='OPEN').exists())
        review.refresh_from_db();self.assertEqual(review.snapshot,frozen)
        with self.assertRaises(ValidationError):transition(self.planner,self.schedule,'submit')
        estimator_check(self.schedule,self.planner)
        self.assertEqual(WorkTask.objects.filter(schedule=self.schedule,kind='SUBMISSION',status='OPEN').count(),1)
