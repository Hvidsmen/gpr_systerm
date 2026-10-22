from copy import deepcopy
from datetime import date
from decimal import Decimal
from unittest.mock import patch
from django.test import TestCase
from django.core.exceptions import ValidationError,PermissionDenied
from django.urls import reverse
from .test_daily_schedule import DailyScheduleTests,activate
from .models import DailySchedule,GlobalPlanVersion,CarryoverReviewTask,WorkTask
from .workspace_services import WorkspaceService
from .task_services import sync_version,sync_company
from .smg_rebase import start
from .daily_schedule import create,save_entry,overlay
from .approval_test_helpers import estimator_check


class SMGRebaseTests(TestCase):
    setUpTestData=classmethod(DailyScheduleTests.setUpTestData.__func__)
    prepare=DailyScheduleTests.prepare
    values=DailyScheduleTests.values

    def setup_rebase(self,approved=True):
        self.prepare()
        activate(self.planner,self.schedule)
        self.schedule.refresh_from_db();self.entry.refresh_from_db()
        self.correction=WorkspaceService.new_version(self.source.workspace,self.planner,'FORECAST',date(2026,2,1),'BASELINE')
        snapshot=deepcopy(self.original);snapshot['works'][0]['plans'][0]['quantity']='120'
        snapshot['works'][0]['daily'][0]['quantity']='120'
        GlobalPlanVersion.objects.filter(pk=self.correction.pk).update(status='APPROVED' if approved else 'DRAFT',snapshot=snapshot)
        self.correction.refresh_from_db()
        self.record=CarryoverReviewTask.objects.create(company=self.company,version=self.source,source_version=self.source,month=date(2026,2,1),due_date=date(2026,2,1),assigned_to=self.planner,correction_version=self.correction)

    def test_task_only_after_gpr_approval_and_backfill_is_idempotent(self):
        self.setup_rebase(False)
        sync_version(self.correction)
        self.assertFalse(WorkTask.objects.filter(key__startswith='smg-update:').exists())
        GlobalPlanVersion.objects.filter(pk=self.correction.pk).update(status='APPROVED')
        self.correction.refresh_from_db();sync_version(self.correction);sync_company(self.company)
        task=WorkTask.objects.get(key__startswith='smg-update:')
        self.assertEqual(task.assigned_to,self.planner);self.assertEqual(task.version,self.correction)
        self.assertIn(self.work.name,task.description)
        self.assertFalse(task.schedule_id)
        self.assertEqual(task.status,'OPEN')
        self.assertEqual(self.client.get(reverse('planning:task_detail',args=[task.pk])).status_code,200)

    @patch('apps.planning.smg_rebase.timezone.localdate',return_value=date(2026,2,10))
    def test_rebase_keeps_previous_and_locks_past_without_writing_facts(self,clock):
        from apps.production.models import DailyFact
        self.setup_rebase();sync_version(self.correction)
        DailyFact.objects.create(company=self.company,project_work=self.work,date=date(2026,2,1),actual_quantity=5)
        facts=list(DailyFact.objects.values())
        before=DailyFact.objects.count();task=WorkTask.objects.get(key__startswith='smg-update:')
        new=start(self.planner,task);entry=new.entries.get(kind='work')
        self.assertEqual(new.replaces_id,self.schedule.pk);self.assertEqual(new.locked_before,date(2026,2,10))
        self.assertEqual(entry.rows,self.entry.rows);self.assertEqual(entry.original[0]['quantity'],'120')
        self.assertEqual(start(self.planner,task).pk,new.pk)
        self.assertEqual(create(self.planner,self.correction,date(2026,2,1)).pk,new.pk)
        self.schedule.refresh_from_db();self.assertEqual(self.schedule.status,'APPLIED')
        with self.assertRaises(ValidationError):save_entry(self.planner,entry,self.values(entry,120,1),0)
        values=self.values(entry,20,12);values[0]['quantity']=Decimal(100)
        save_entry(self.planner,entry,values,0);entry.refresh_from_db()
        self.assertEqual([r for r in entry.rows if r['date']<'2026-02-10'],self.entry.rows)
        response=self.client.get(reverse('planning:daily_schedule_detail',args=[new.pk]))
        self.assertEqual(response.status_code,200);self.assertContains(response,'закрыты для изменения')
        self.assertEqual(DailyFact.objects.count(),before)
        self.assertEqual(list(DailyFact.objects.values()),facts)
        task.refresh_from_db();self.assertEqual(task.status,'OPEN')
        estimator_check(new,self.planner)
        from .daily_schedule import activate as apply
        apply(self.planner,new)
        task.refresh_from_db();self.assertEqual(task.status,'DONE')
        self.source.refresh_from_db();self.assertEqual(self.source.snapshot,self.original)
        self.schedule.refresh_from_db();self.assertEqual(self.schedule.status,'ARCHIVED')
        later=create(self.planner,self.correction,date(2026,2,1));self.assertEqual(later.locked_before,new.locked_before)

    @patch('apps.planning.smg_rebase.timezone.localdate',return_value=date(2026,2,10))
    def test_old_operative_overlay_survives_until_new_schedule_applied(self,clock):
        self.setup_rebase();sync_version(self.correction)
        snapshot=deepcopy(self.correction.snapshot)
        before=overlay(self.correction,snapshot,date(2026,2,1),date(2026,2,28))
        self.assertEqual(before['daily_schedule_ids'],[self.schedule.pk])
        task=WorkTask.objects.get(key__startswith='smg-update:')
        self.client.post(reverse('planning:task_detail',args=[task.pk]),{'action':'launch'})
        task.refresh_from_db();self.assertTrue(task.schedule_id)
        self.assertEqual(overlay(self.correction,snapshot,date(2026,2,1),date(2026,2,28))['daily_schedule_ids'],[self.schedule.pk])

    def test_unauthorized_and_unapproved_start_rejected(self):
        self.setup_rebase();sync_version(self.correction)
        task=WorkTask.objects.get(key__startswith='smg-update:')
        from apps.accounts.models import User,Role
        observer=User.objects.create_user(username='rebase-reader',company=self.company,role=Role.objects.get(code='OBSERVER'))
        with self.assertRaises(PermissionDenied):start(observer,task)
        GlobalPlanVersion.objects.filter(pk=self.correction.pk).update(status='SUBMITTED')
        with self.assertRaises(ValidationError):start(self.planner,task)

    @patch('apps.planning.smg_rebase.timezone.localdate',return_value=date(2026,2,10))
    def test_removed_work_keeps_history_with_zero_new_gpr_baseline(self,clock):
        self.setup_rebase()
        snapshot=deepcopy(self.correction.snapshot);snapshot['works']=[]
        GlobalPlanVersion.objects.filter(pk=self.correction.pk).update(snapshot=snapshot)
        self.correction.refresh_from_db();sync_version(self.correction)
        new=start(self.planner,WorkTask.objects.get(key__startswith='smg-update:'))
        entry=new.entries.get(kind='work')
        self.assertEqual(entry.original,[])
        self.assertEqual(entry.rows,self.entry.rows)
        from .daily_schedule_approval import requires_approval
        self.assertTrue(requires_approval(new))

    @patch('apps.planning.smg_rebase.timezone.localdate',return_value=date(2026,2,10))
    def test_without_old_smg_preserves_past_gpr_and_existing_future_edits(self,clock):
        self.setup_rebase()
        self.schedule.delete()
        sync_version(self.correction)
        new=start(self.planner,WorkTask.objects.get(key__startswith='smg-update:'))
        self.assertIsNone(new.replaces_id)
        self.assertEqual(new.locked_before,date(2026,2,10))
        self.assertEqual(new.entries.get(kind='work').rows[0]['quantity'],'100')
