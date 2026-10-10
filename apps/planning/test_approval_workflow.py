from copy import deepcopy
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase
from django.urls import reverse
from apps.accounts.models import User, Role, Company
from .models import GlobalPlanVersion, GlobalPlanReview, GlobalPlanDecision, WorkMonthAllocation
from . import test_workspace as fixtures
from .test_workspace import JAN, FEB
from .approval_test_helpers import departments
from .approval_workflow import current_review, baseline_snapshot
from .global_services import GlobalPlanService
from .workspace_services import WorkspaceService, build_workspace_snapshot
from .deletion import delete_workspace, delete_version


class ApprovalWorkflowTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        fixtures.WorkspaceTests.setUpTestData.__func__(cls)
        cls.admin = User.objects.create_user(username='approval-admin', company=cls.company, role=Role.objects.get(code='ADMIN'))
        other = Company.objects.create(name='Other approval company')
        cls.foreign_ceo = User.objects.create_user(username='foreign-ceo', company=other, role=Role.objects.get(code='CEO'))

    create = fixtures.WorkspaceTests.create
    approve = fixtures.WorkspaceTests.approve

    def submit(self):
        return GlobalPlanService.transition(self.create().baseline_version, self.planner, 'submit')

    def test_approval_list_shows_ceo_stage_before_and_after_decision(self):
        version = self.submit()
        self.client.force_login(self.admin)
        url = reverse('planning:approval_list')
        self.assertContains(self.client.get(url), 'Генеральный директор')
        self.assertContains(self.client.get(url), 'После согласования служб')
        departments(version, self.approvers)
        self.assertContains(self.client.get(url), 'Ожидает утверждения')
        GlobalPlanService.transition(version, self.approvers['CEO'], 'approve')
        self.assertContains(self.client.get(url), 'Утверждено')

    def test_three_parallel_sections_are_required_and_independent(self):
        version = self.submit()
        for code, action in [('TECH_HEAD','approve_tech'), ('PRODUCTION_HEAD','approve_production'), ('HR_HEAD','approve_hr')]:
            with self.assertRaises(ValidationError):
                GlobalPlanService.transition(version, self.approvers['CEO'], 'approve')
            version = GlobalPlanService.transition(version, self.approvers[code], action)
            self.assertEqual(version.status, 'SUBMITTED')
        version = GlobalPlanService.transition(version, self.approvers['CEO'], 'approve')
        self.assertEqual(version.status, 'APPROVED')
        self.assertEqual(version.approved_by, self.approvers['CEO'])
        self.assertEqual(current_review(version).decisions.count(), 5)

    def test_wrong_roles_cannot_approve_other_sections_or_final_plan(self):
        version = self.submit()
        for user in [self.planner, self.manager, *self.approvers.values()]:
            for code, action in [('TECH_HEAD','approve_tech'), ('PRODUCTION_HEAD','approve_production'), ('HR_HEAD','approve_hr'), ('CEO','approve'), ('CEO','complete')]:
                if user == self.approvers[code]:
                    continue
                with self.subTest(user=user.username, action=action), self.assertRaises(PermissionDenied):
                    GlobalPlanService.transition(version, user, action)
        with self.assertRaises(PermissionDenied):
            GlobalPlanService.transition(version, self.foreign_ceo, 'approve')

    def test_duplicate_approval_does_not_duplicate_history(self):
        version = self.submit()
        version = GlobalPlanService.transition(version, self.approvers['HR_HEAD'], 'approve_hr')
        with self.assertRaises(ValidationError):
            GlobalPlanService.transition(version, self.approvers['HR_HEAD'], 'approve_hr')
        self.assertEqual(current_review(version).decisions.count(), 2)

    def test_every_head_can_return_whole_plan_with_required_reason(self):
        for code in ['PRODUCTION_HEAD', 'HR_HEAD', 'TECH_HEAD']:
            version = self.submit()
            with self.assertRaises(ValidationError):
                GlobalPlanService.transition(version, self.approvers[code], 'reject', '  ')
            version = GlobalPlanService.transition(version, self.approvers[code], 'reject', 'Изменить состав')
            self.assertEqual(version.status, 'REJECTED')
            row = version.work_allocations.first()
            row.quantity += 5
            row.save()
            self.assertEqual(current_review(version).decisions.last().comment, 'Изменить состав')

    def test_return_creates_fresh_round_preserving_previous_snapshot(self):
        version = self.submit()
        old = deepcopy(current_review(version).snapshot)
        version = GlobalPlanService.transition(version, self.approvers['HR_HEAD'], 'approve_hr')
        version = GlobalPlanService.transition(version, self.approvers['PRODUCTION_HEAD'], 'reject', 'Переделать объёмы')
        row = version.work_allocations.first()
        row.quantity = 100
        row.save()
        version = GlobalPlanService.transition(version, self.planner, 'submit')
        self.assertEqual(version.approval_round, 2)
        self.assertEqual(current_review(version).decisions.count(), 1)
        self.assertEqual(version.review_rounds.get(number=1).snapshot, old)
        self.assertNotEqual(current_review(version).snapshot, old)
        with self.assertRaises(ValidationError):
            GlobalPlanService.transition(version, self.approvers['CEO'], 'approve')

    def test_ceo_may_return_approved_plan_and_completion_is_terminal(self):
        version = self.approve(self.create().baseline_version)
        for code in ['PRODUCTION_HEAD', 'HR_HEAD', 'TECH_HEAD']:
            with self.assertRaises(ValidationError):
                GlobalPlanService.transition(version, self.approvers[code], 'reject', 'Причина')
        version = GlobalPlanService.transition(version, self.approvers['CEO'], 'reject', 'Новый расчёт')
        self.assertIsNone(version.approved_by_id)
        self.assertIsNone(version.approved_at)
        version = self.approve(version)
        version = GlobalPlanService.transition(version, self.approvers['CEO'], 'complete')
        for action in ['reject','approve','complete']:
            with self.assertRaises(ValidationError):
                GlobalPlanService.transition(version, self.approvers['CEO'], action, 'Причина')
        for action in ['submit','revision']:
            # A new forecast revision is separate; the completed baseline cannot be revised.
            if action == 'revision':
                with self.assertRaises(ValidationError):
                    GlobalPlanService.revision(version, self.planner)
            else:
                with self.assertRaises(ValidationError):
                    GlobalPlanService.transition(version, self.planner, action)
        version.title = 'Change completed'
        with self.assertRaises(ValidationError):
            version.save()
        row = version.work_allocations.first()
        row.quantity += 1
        with self.assertRaises(ValidationError):
            row.save()

    def test_direct_status_and_history_mutation_are_rejected(self):
        version = self.submit()
        version.status = 'APPROVED'
        with self.assertRaises(ValidationError):
            version.save()
        review = current_review(version)
        review.snapshot = {}
        with self.assertRaises(ValidationError):
            review.save()
        decision = review.decisions.first()
        decision.comment = 'Alter history'
        with self.assertRaises(ValidationError):
            decision.save()
        with self.assertRaises(ValidationError):
            decision.delete()

    def test_submitted_content_cannot_be_edited(self):
        version = self.submit()
        version.snapshot['works'] = []
        with self.assertRaises(ValidationError):
            version.save()
        row = version.work_allocations.first()
        row.quantity += 1
        with self.assertRaises(ValidationError):
            row.save()

    def test_ceo_queue_only_after_all_heads_and_approved_until_completion(self):
        version = self.submit()
        url = reverse('planning:approval_list')
        self.client.force_login(self.approvers['CEO'])
        self.assertEqual(self.client.get(url).context['rows'], [])
        version = departments(version, self.approvers)
        self.assertEqual([r['version'].pk for r in self.client.get(url).context['rows']], [version.pk])
        version = GlobalPlanService.transition(version, self.approvers['CEO'], 'approve')
        self.assertEqual(len(self.client.get(url).context['rows']), 1)
        version = GlobalPlanService.transition(version, self.approvers['CEO'], 'complete')
        self.assertEqual(self.client.get(url).context['rows'], [])

    def test_head_inbox_and_buttons_follow_own_section(self):
        version = self.submit()
        self.client.force_login(self.approvers['HR_HEAD'])
        url = reverse('planning:global_detail', args=[version.pk])
        response = self.client.get(url)
        self.assertContains(response, 'approve_hr')
        self.assertNotContains(response, 'approve_production')
        self.assertContains(response, 'Обязательная причина')
        self.assertEqual(len(self.client.get(reverse('planning:approval_list')).context['rows']), 1)
        self.assertEqual(self.client.post(reverse('planning:global_action', args=[version.pk, 'approve_hr'])).status_code, 302)
        self.assertEqual(self.client.get(reverse('planning:approval_list')).context['rows'], [])
        self.assertEqual(self.client.post(reverse('planning:global_action', args=[version.pk, 'approve_tech'])).status_code, 403)
        self.client.force_login(self.foreign_ceo)
        self.assertEqual(self.client.get(url).status_code, 404)
        self.assertEqual(self.client.post(reverse('planning:global_action', args=[version.pk,'reject']), {'comment':'Other company'}).status_code, 404)

    def test_existing_forecast_uses_its_frozen_baseline_during_and_after_rework(self):
        workspace = self.create()
        base = self.approve(workspace.baseline_version)
        workspace.refresh_from_db()
        forecast = WorkspaceService.forecast(workspace, self.planner, FEB, 'BASELINE')
        def calculated(version):
            value = build_workspace_snapshot(version)
            value.pop("built_at", None)
            return value
        original = calculated(forecast)
        frozen = deepcopy(baseline_snapshot(forecast))
        base = GlobalPlanService.transition(base, self.approvers['CEO'], 'reject', 'Изменить базу')
        row = base.work_allocations.get(month=JAN)
        row.quantity = 999
        row.save()
        WorkspaceService.refresh(base, self.planner)
        self.assertEqual(calculated(forecast), original)
        self.assertEqual(baseline_snapshot(forecast), frozen)
        base = self.approve(base)
        workspace.refresh_from_db()
        self.assertEqual(calculated(forecast), original)
        new = WorkspaceService.forecast(workspace, self.planner, FEB, 'BASELINE')
        self.assertNotEqual(new.baseline_review_id, forecast.baseline_review_id)
        self.assertNotEqual(calculated(new), original)

    def test_history_survives_user_deletion_and_admin_can_purge_whole_workspace(self):
        version = self.approve(self.create().baseline_version)
        reviewer = self.approvers['HR_HEAD']
        decision = current_review(version).decisions.get(section='HR')
        name = decision.actor_name
        reviewer.delete()
        decision.refresh_from_db()
        self.assertEqual(decision.actor_name, name)
        self.assertIsNone(decision.actor_id)
        version = GlobalPlanService.transition(version, self.approvers['CEO'], 'reject', 'Доработка')
        with self.assertRaises(ValidationError):
            delete_version(version, self.planner)
        with self.assertRaises(ValidationError):
            delete_workspace(version.workspace, self.planner)
        with self.assertRaises(ValidationError):
            delete_workspace(version.workspace, self.admin)
        delete_workspace(version.workspace, self.admin, confirm_history=True)
        self.assertFalse(GlobalPlanReview.objects.filter(version_id=version.pk).exists())

    def test_superuser_can_finalize_without_changing_assigned_role(self):
        version = departments(self.submit(), self.approvers)
        self.admin.is_superuser = True
        self.admin.role = Role.objects.get(code='HR_HEAD')
        self.admin.save()
        version = GlobalPlanService.transition(version, self.admin, 'approve')
        self.assertEqual(version.status, 'APPROVED')
        self.assertEqual(current_review(version).decisions.get(section='CEO').actor_role, 'ADMIN')

    def test_admin_can_approve_each_section_with_correct_audit_and_required_order(self):
        version = self.submit()
        for section, action in [('PRODUCTION', 'approve_production'), ('HR', 'approve_hr'), ('TECH', 'approve_tech')]:
            with self.assertRaises(ValidationError):
                GlobalPlanService.transition(version, self.admin, 'approve')
            version = GlobalPlanService.transition(version, self.admin, action)
            decision = current_review(version).decisions.get(section=section)
            self.assertEqual(decision.actor, self.admin)
            self.assertEqual(decision.actor_role, 'ADMIN')
            with self.assertRaises(ValidationError):
                GlobalPlanService.transition(version, self.admin, action)
        version = GlobalPlanService.transition(version, self.admin, 'approve')
        self.assertEqual(version.approved_by, self.admin)
        self.assertEqual(current_review(version).decisions.count(), 5)

    def test_admin_rework_and_completion_keep_history_and_terminal_status(self):
        version = self.submit()
        with self.assertRaises(ValidationError):
            GlobalPlanService.transition(version, self.admin, 'reject', '')
        version = GlobalPlanService.transition(version, self.admin, 'reject', 'Fix plan')
        self.assertEqual(version.status, 'REJECTED')
        version = self.approve(version)
        version = GlobalPlanService.transition(version, self.admin, 'reject', 'New estimate')
        self.assertEqual(version.status, 'REJECTED')
        version = self.approve(version)
        version = GlobalPlanService.transition(version, self.admin, 'complete')
        for action in ['submit','approve_production','approve_hr','approve_tech','approve','reject','complete']:
            with self.assertRaises(ValidationError):
                GlobalPlanService.transition(version, self.admin, action, 'Reason')

    def test_admin_buttons_follow_stage_and_do_not_cross_company(self):
        version = self.submit()
        self.client.force_login(self.admin)
        url = reverse('planning:global_detail', args=[version.pk])
        response = self.client.get(url)
        for action in ['approve_production', 'approve_hr', 'approve_tech', 'reject']:
            self.assertContains(response, reverse('planning:global_action', args=[version.pk, action]))
        self.assertNotContains(response, reverse('planning:global_action', args=[version.pk, 'approve'])+'"')
        for action in ['approve_production', 'approve_hr', 'approve_tech']:
            self.assertEqual(self.client.post(reverse('planning:global_action',args=[version.pk,action])).status_code,302)
        self.assertContains(self.client.get(url),reverse('planning:global_action',args=[version.pk,'approve']))
        foreign_admin = User.objects.create_user(username='other-admin',company=self.foreign_ceo.company,role=Role.objects.get(code='ADMIN'))
        self.client.force_login(foreign_admin)
        self.assertEqual(self.client.post(reverse('planning:global_action',args=[version.pk,'approve'])).status_code,404)
        with self.assertRaises(PermissionDenied):
            GlobalPlanService.transition(version,foreign_admin,'approve')
