from copy import deepcopy
from importlib import import_module
from types import SimpleNamespace
from django.apps import apps
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import connection
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from apps.accounts.models import Company, Role, User
from core.plan_approval import DEPARTMENTS
from . import test_work_resources as fixtures
from .approval_test_helpers import estimator_check
from .approval_workflow import transition, current_review, panel, ready
from .daily_schedule_approval import transition as smg_transition, current as smg_review
from .daily_schedule_table import add_entry
from .independent_smg import create
from .models import WorkMonthAllocation, WorkTask, GlobalPlanDecision, DailyScheduleDecision
from .task_services import sync_company


class SboApprovalTests(TestCase):
    setUpTestData = classmethod(fixtures.WorkResourceTests.setUpTestData.__func__)
    prepare_owner = fixtures.WorkResourceTests.prepare_owner

    def setUp(self):
        fixtures.WorkResourceTests.setUp(self)
        WorkMonthAllocation.objects.create(company=self.company, version=self.version, work=self.simple, month=fixtures.JAN, quantity=1)
        self.sbo = self.approvers['SBO_HEAD']

    def gpr(self):
        estimator_check(self.version, self.planner)
        self.version = transition(self.version, self.manager, 'submit')
        return self.version

    def smg(self):
        schedule = create(self.planner, self.obj, fixtures.JAN)
        add_entry(self.planner, schedule, {'kind': 'work', 'work': self.simple})
        estimator_check(schedule, self.planner)
        return smg_transition(self.manager, schedule, 'submit')

    def other_departments(self, owner, daily=False):
        for section, _, role, action in DEPARTMENTS:
            if section == 'SBO':
                continue
            owner = smg_transition(self.approvers[role], owner, action) if daily else transition(owner, self.approvers[role], action)
        return owner

    def test_role_is_seeded_and_sbo_can_approve_first_in_parallel(self):
        self.assertEqual(Role.objects.get(code='SBO_HEAD').name, 'Служба СБО')
        version = self.gpr()
        review = current_review(version)
        self.assertEqual(WorkTask.objects.filter(review=review, kind='APPROVAL', status='OPEN').count(), 5)
        self.assertTrue(next(s for s in panel(version, self.sbo)['approval_sections'] if s['label'] == 'Служба СБО')['can_approve'])
        version = transition(version, self.sbo, 'approve_sbo', 'Согласовано СБО')
        decision = review.decisions.get(section='SBO', action='APPROVE')
        self.assertEqual((decision.actor_id, decision.actor_role, decision.get_section_display()), (self.sbo.pk, 'SBO_HEAD', 'Служба СБО'))
        self.assertEqual(version.status, 'SUBMITTED')
        with self.assertRaises(ValidationError):
            transition(version, self.sbo, 'approve_sbo')

    def test_four_departments_cannot_bypass_sbo_and_task_approval_unlocks_ceo(self):
        version = self.other_departments(self.gpr())
        review = current_review(version)
        self.assertFalse(ready(review))
        self.assertFalse(WorkTask.objects.filter(review=review, target_role='CEO', status='OPEN').exists())
        with self.assertRaises(ValidationError):
            transition(version, self.approvers['CEO'], 'approve')
        self.client.force_login(self.sbo)
        response = self.client.get(reverse('planning:approval_list'))
        self.assertContains(response, '<th>Служба СБО</th>', html=True)
        self.assertEqual(len(response.context['rows']), 1)
        task = WorkTask.objects.get(review=review, target_role='SBO_HEAD')
        response = self.client.post(reverse('planning:task_detail', args=[task.pk]), {'action': 'approve'})
        self.assertEqual(response.status_code, 302)
        task.refresh_from_db(); self.assertEqual(task.status, 'DONE')
        self.assertTrue(WorkTask.objects.filter(review=review, target_role='CEO', status='OPEN').exists())
        self.assertEqual(self.client.get(reverse('planning:approval_list')).context['rows'], [])
        version = transition(version, self.approvers['CEO'], 'approve')
        self.assertEqual(version.status, 'APPROVED')

    def test_sbo_permissions_are_scoped_to_department_and_company(self):
        version = self.gpr()
        foreign = User.objects.create_user(username='foreign-sbo', company=Company.objects.create(name='Foreign SBO'), role=Role.objects.get(code='SBO_HEAD'))
        for actor, action in [(self.sbo, 'approve_hr'), (self.approvers['TECH_HEAD'], 'approve_sbo'), (self.planner, 'approve_sbo'), (foreign, 'approve_sbo')]:
            with self.subTest(actor=actor.username, action=action), self.assertRaises(PermissionDenied):
                transition(version, actor, action)
        self.client.force_login(foreign)
        self.assertEqual(self.client.get(reverse('planning:approval_list')).context['rows'], [])

    def test_sbo_return_requires_reason_and_new_round_requires_new_sbo_decision(self):
        version = self.gpr(); old = current_review(version)
        transition(version, self.sbo, 'approve_sbo')
        with self.assertRaises(ValidationError):
            transition(version, self.sbo, 'reject', ' ')
        version = transition(version, self.sbo, 'reject', 'Уточнить обеспечение')
        self.assertEqual(version.status, 'REJECTED')
        estimator_check(version, self.planner)
        version = transition(version, self.manager, 'submit')
        self.assertEqual(version.approval_round, 2)
        self.assertFalse(current_review(version).decisions.filter(section='SBO').exists())
        self.assertTrue(old.decisions.filter(section='SBO', action='APPROVE').exists())

    def test_smg_parallel_sbo_task_and_required_final_confirmation(self):
        schedule = self.smg(); review = smg_review(schedule)
        self.assertEqual(WorkTask.objects.filter(schedule_review=review, kind='APPROVAL', status='OPEN').count(), 5)
        schedule = self.other_departments(schedule, True)
        with self.assertRaises(ValidationError):
            smg_transition(self.approvers['CEO'], schedule, 'approve')
        self.client.force_login(self.sbo)
        response = self.client.get(reverse('planning:daily_schedule_detail', args=[schedule.pk]))
        self.assertTrue(next(a for a in response.context['smg_actions'] if a['action'] == 'approve_sbo')['allowed'])
        self.assertFalse(response.context['smg_final_allowed'])
        task = WorkTask.objects.get(schedule_review=review, target_role='SBO_HEAD')
        self.assertEqual(self.client.post(reverse('planning:task_detail', args=[task.pk]), {'action': 'approve'}).status_code, 302)
        self.assertTrue(review.decisions.filter(section='SBO', actor=self.sbo, action='APPROVE').exists())
        schedule = smg_transition(self.approvers['CEO'], schedule, 'approve')
        self.assertEqual(schedule.status, 'APPROVED')

    def test_smg_sbo_can_approve_before_other_services_and_return(self):
        schedule = self.smg()
        with self.assertRaises(PermissionDenied):
            smg_transition(self.approvers['SECURITY_HEAD'], schedule, 'approve_sbo')
        schedule = smg_transition(self.sbo, schedule, 'approve_sbo')
        with self.assertRaises(ValidationError):
            smg_transition(self.sbo, schedule, 'reject', '')
        schedule = smg_transition(self.sbo, schedule, 'reject', 'Уточнить СМГ')
        self.assertEqual(schedule.status, 'DRAFT')

    def migrate_tasks(self):
        migration = import_module('apps.planning.migrations.0049_sbo_approval')
        migration.add_pending_tasks(apps, SimpleNamespace(connection=connection))

    def test_upgrade_adds_only_pending_sbo_tasks_and_defers_existing_ceo_tasks(self):
        version = self.other_departments(self.gpr())
        schedule = self.other_departments(self.smg(), True)
        review, daily = current_review(version), smg_review(schedule)
        for key, owner_field, owner, field, target in [('approval', 'version', version, 'review', review), ('smg-approval', 'schedule', schedule, 'schedule_review', daily)]:
            WorkTask.objects.filter(**{field: target}, target_role='SBO_HEAD').delete()
            WorkTask.objects.create(company=self.company, key=f'{key}:{target.pk}:CEO', kind='APPROVAL', title='Утвердить', target_role='CEO', **{owner_field: owner, field: target})
        before = list(review.decisions.values('pk', 'section', 'action'))
        self.migrate_tasks(); self.migrate_tasks()
        for field, target in [('review', review), ('schedule_review', daily)]:
            self.assertEqual(WorkTask.objects.filter(**{field: target}, target_role='SBO_HEAD', status='OPEN').count(), 1)
            self.assertEqual(WorkTask.objects.get(**{field: target}, target_role='CEO').status, 'SUPERSEDED')
        self.assertEqual(list(review.decisions.values('pk', 'section', 'action')), before)
        transition(version, self.sbo, 'approve_sbo')
        self.assertEqual(WorkTask.objects.get(review=review, target_role='CEO').status, 'OPEN')

    def test_upgrade_and_runtime_preserve_finalized_plans_without_inventing_sbo(self):
        version = self.other_departments(self.gpr())
        review = current_review(version)
        # Historical fixture from the route before SBO was introduced.
        WorkTask.objects.filter(review=review,target_role='SBO_HEAD').delete()
        GlobalPlanDecision.objects.create(company=self.company,review=review,section='CEO',action='APPROVE',actor=self.approvers['CEO'],actor_name='CEO',actor_role='CEO')
        version.status='APPROVED';version.approved_by=self.approvers['CEO'];version.approved_at=timezone.now()
        from .approval_context import authorized_transition
        with authorized_transition(version.pk):version.save()
        snapshot = deepcopy(version.snapshot)
        self.migrate_tasks(); sync_company(self.company)
        version.refresh_from_db()
        self.assertEqual((version.status, version.snapshot), ('APPROVED', snapshot))
        self.assertFalse(review.decisions.filter(section='SBO').exists())
        self.assertNotIn('Служба СБО', [s['label'] for s in panel(version, self.sbo)['approval_sections']])
        self.client.force_login(self.approvers['CEO'])
        self.assertContains(self.client.get(reverse('planning:approval_list')), 'Не требовалось')

    def test_finalized_smg_without_sbo_keeps_snapshot_and_hides_new_pending_section(self):
        schedule=self.other_departments(self.smg(),True)
        review=smg_review(schedule)
        WorkTask.objects.filter(schedule_review=review,target_role='SBO_HEAD').delete()
        DailyScheduleDecision.objects.create(company=self.company,review=review,section='CEO',action='APPROVE',actor=self.approvers['CEO'],actor_name='CEO',actor_role='CEO')
        schedule.status='APPROVED';schedule.save(update_fields=['status'])
        snapshot=deepcopy(review.snapshot)
        self.migrate_tasks();sync_company(self.company)
        schedule.refresh_from_db();review.refresh_from_db()
        self.assertEqual((schedule.status,review.snapshot),('APPROVED',snapshot))
        self.assertFalse(review.decisions.filter(section='SBO').exists())
        self.assertFalse(WorkTask.objects.filter(schedule_review=review,target_role='SBO_HEAD').exists())
        self.client.force_login(self.approvers['CEO'])
        response=self.client.get(reverse('planning:daily_schedule_detail',args=[schedule.pk]))
        self.assertNotIn('approve_sbo',[action['action'] for action in response.context['smg_actions']])
