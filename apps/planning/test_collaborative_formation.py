from datetime import date

from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase

from apps.accounts.models import Role, User
from apps.resources.models import Brigade, BrigadeGroup

from . import estimate_review, preparation
from .approval_workflow import current_review
from .daily_schedule_approval import transition as smg_transition
from .global_services import GlobalPlanService
from .models import PlanLaborGroupPermission, WorkMonthAllocation
from .workspace_services import WorkspaceService
from . import test_workspace as fixtures


JAN = date(2026, 1, 1)


class CollaborativeFormationTests(TestCase):
    setUpTestData = classmethod(fixtures.WorkspaceTests.setUpTestData.__func__)

    def setUp(self):
        self.estimator = User.objects.create_user(
            username='formation-estimator', company=self.company,
            role=Role.objects.get(code='ESTIMATOR'),
        )
        self.site_chief = User.objects.create_user(
            username='formation-site-chief', company=self.company,
            role=Role.objects.get(code='SITE_CHIEF'),
        )
        self.version = WorkspaceService.create(
            self.planner, self.obj, 'Совместное формирование', JAN, date(2026, 1, 31),
        ).baseline_version
        WorkMonthAllocation.objects.create(
            company=self.company, version=self.version, work=self.simple,
            month=JAN, quantity=10,
        )

    def check_estimate(self, owner):
        task = owner.estimate_reviews.latest('pk')
        for work_id in estimate_review.work_ids(owner):
            task = estimate_review.review_work(self.estimator, task, work_id, True)
        estimate_review.finish(self.estimator, task, True)

    def prepare(self, owner=None):
        owner = owner or self.version
        preparation.handoff(
            self.planner, owner, self.manager, self.estimator,
            resource_editors=[self.site_chief],
        )
        preparation.ready(self.site_chief, owner)
        self.check_estimate(owner)

    def test_planner_launches_and_either_project_participant_closes_shared_stage(self):
        self.prepare()
        with self.assertRaises(PermissionDenied):
            GlobalPlanService.transition(self.version, self.manager, 'submit')
        submitted = GlobalPlanService.transition(self.version, self.planner, 'submit')
        self.assertEqual(submitted.status, 'SUBMITTED')
        submitted = GlobalPlanService.transition(
            submitted, self.site_chief, 'approve_project',
        )
        decision = current_review(submitted).decisions.get(section='PROJECT')
        self.assertEqual(decision.actor, self.site_chief)
        with self.assertRaises(ValidationError):
            GlobalPlanService.transition(submitted, self.manager, 'approve_project')
        for role, action in [
            ('PRODUCTION_HEAD', 'approve_production'), ('HR_HEAD', 'approve_hr'),
            ('TECH_HEAD', 'approve_tech'), ('SECURITY_HEAD', 'approve_security'),
            ('SBO_HEAD', 'approve_sbo'),
        ]:
            submitted = GlobalPlanService.transition(submitted, self.approvers[role], action)
        approved = GlobalPlanService.transition(submitted, self.approvers['CEO'], 'approve')
        self.assertEqual(approved.status, 'APPROVED')

    def test_technical_and_sbo_resource_scope_is_enforced_by_position_group(self):
        first = BrigadeGroup.objects.create(company=self.company, name='Разрешённая группа')
        second = BrigadeGroup.objects.create(company=self.company, name='Чужая группа')
        allowed = Brigade.objects.create(company=self.company, name='Разрешённая должность', group=first)
        denied = Brigade.objects.create(company=self.company, name='Чужая должность', group=second)
        tech = User.objects.create_user(username='formation-tech', company=self.company, role=Role.objects.get(code='TECH_HEAD'))
        sbo = User.objects.create_user(username='formation-sbo', company=self.company, role=Role.objects.get(code='SBO_HEAD'))
        for role in ['TECH_HEAD', 'SBO_HEAD']:
            PlanLaborGroupPermission.objects.create(company=self.company, role_code=role, group=first)

        from .formation_access import can_edit_resource
        self.assertTrue(can_edit_resource(tech, self.version, 'labor', allowed))
        self.assertFalse(can_edit_resource(tech, self.version, 'labor', denied))
        self.assertTrue(can_edit_resource(tech, self.version, 'equipment'))
        self.assertTrue(can_edit_resource(tech, self.version, 'fuel'))
        self.assertTrue(can_edit_resource(sbo, self.version, 'labor', allowed))
        self.assertFalse(can_edit_resource(sbo, self.version, 'labor', denied))
        self.assertFalse(can_edit_resource(sbo, self.version, 'equipment'))
        self.assertFalse(can_edit_resource(sbo, self.version, 'fuel'))

    def test_smg_submission_is_also_controlled_by_planner(self):
        from .independent_smg import create
        from .daily_schedule_table import add_entry

        schedule = create(self.planner, self.obj, JAN)
        add_entry(self.planner, schedule, {'kind': 'work', 'work': self.simple})
        self.prepare(schedule)
        with self.assertRaises(PermissionDenied):
            smg_transition(self.manager, schedule, 'submit')
        submitted = smg_transition(self.planner, schedule, 'submit')
        self.assertEqual(submitted.status, 'SUBMITTED')
