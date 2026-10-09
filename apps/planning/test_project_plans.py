from datetime import date
from copy import deepcopy
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.urls import reverse
from apps.accounts.models import User, Role, Company
from apps.projects.models import Project, ConstructionObject
from . import test_workspace as fixtures
from .models import ProjectPlanVersion, ProjectPlanMember
from .project_plan_services import ProjectPlanService
from .global_services import GlobalPlanService
from .workspace_services import WorkspaceService
from .project_plan_views import AssignmentForm

JAN, FEB = fixtures.JAN, fixtures.FEB


class ProjectPlanTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        fixtures.WorkspaceTests.setUpTestData.__func__(cls)
        cls.project = cls.obj.project
        cls.foreign_company = Company.objects.create(
            name="Foreign consolidated company"
        )
        cls.foreign_project = Project.objects.create(
            company=cls.foreign_company, name="Foreign consolidated project"
        )
        cls.foreign_user = User.objects.create_user(
            username="foreign-consolidated-planner",
            company=cls.foreign_company,
            role=Role.objects.get(code="PLANNER"),
        )
        cls.other_project = Project.objects.create(
            company=cls.company, name="Other project"
        )

    create = fixtures.WorkspaceTests.create
    approve = fixtures.WorkspaceTests.approve

    def parent(self, start=JAN, end=date(2026, 3, 31), project=None):
        return ProjectPlanService.create(
            self.planner, project or self.project, "Project GPR", start, end
        )

    def test_only_approved_versions_of_the_project_covering_period_can_be_added(self):
        parent = self.parent()
        draft = self.create().baseline_version
        with self.assertRaises(ValidationError):
            ProjectPlanService.assign(self.planner, parent, draft)
        approved = self.approve(draft)
        member = ProjectPlanService.assign(self.planner, parent, approved)
        self.assertEqual(member.construction_object, self.obj)
        with self.assertRaises(ValidationError):
            ProjectPlanService.assign(
                self.planner, self.parent(project=self.other_project), approved
            )
        with self.assertRaises(ValidationError):
            ProjectPlanService.assign(
                self.planner, self.parent(end=date(2026, 4, 30)), approved
            )
        with self.assertRaises(PermissionDenied):
            ProjectPlanService.assign(self.foreign_user, parent, approved)
        with self.assertRaises(PermissionDenied):
            ProjectPlanService.create(
                self.planner, self.foreign_project, "Foreign", JAN, FEB
            )

    def test_object_unique_in_forms_service_and_database_and_replacement_explicit(self):
        parent = self.parent()
        workspace = self.create()
        base = self.approve(workspace.baseline_version)
        workspace.refresh_from_db()
        forecast = self.approve(
            WorkspaceService.forecast(workspace, self.planner, FEB, "BASELINE")
        )
        member = ProjectPlanService.assign(self.planner, parent, base)
        with self.assertRaises(ValidationError):
            ProjectPlanService.assign(self.planner, parent, base)
        with self.assertRaises(ValidationError):
            ProjectPlanService.assign(self.planner, parent, forecast)
        with self.assertRaises(IntegrityError), transaction.atomic():
            ProjectPlanMember.objects.bulk_create(
                [
                    ProjectPlanMember(
                        company=self.company,
                        consolidated_version=parent,
                        construction_object=self.obj,
                        version=forecast,
                    )
                ]
            )
        replaced = ProjectPlanService.assign(
            self.planner, parent, forecast, replace=True
        )
        self.assertEqual(member.pk, replaced.pk)
        self.assertEqual(parent.members.count(), 1)
        self.assertEqual(replaced.version, forecast)

    def test_fixation_is_atomic_and_not_possible_with_empty_or_revoked_sources(self):
        parent = self.parent()
        with self.assertRaises(ValidationError):
            ProjectPlanService.fix(self.planner, parent)
        version = self.approve(self.create().baseline_version)
        ProjectPlanService.assign(self.planner, parent, version)
        version = GlobalPlanService.transition(
            version, self.approvers["CEO"], "reject", "Rework"
        )
        with self.assertRaises(ValidationError):
            ProjectPlanService.fix(self.planner, parent)
        parent.refresh_from_db()
        self.assertEqual(parent.status, "DRAFT")
        self.assertFalse(parent.members.first().snapshot)
        self.assertIsNone(parent.members.first().review_id)

    def test_fixed_snapshots_survive_rework_and_all_content_changes_are_denied(self):
        parent = self.parent()
        version = self.approve(self.create().baseline_version)
        member = ProjectPlanService.assign(self.planner, parent, version)
        parent = ProjectPlanService.fix(self.planner, parent)
        member.refresh_from_db()
        snapshot = deepcopy(member.snapshot)
        review_id = member.review_id
        self.assertEqual(parent.status, "FIXED")
        version = GlobalPlanService.transition(
            version, self.approvers["CEO"], "reject", "Rework"
        )
        allocation = version.work_allocations.first()
        allocation.quantity += 111
        allocation.save()
        self.approve(version)
        member.refresh_from_db()
        self.assertEqual(member.snapshot, snapshot)
        self.assertEqual(member.review_id, review_id)
        self.assertNotEqual(member.review.number, member.version.approval_round)
        with self.assertRaises(ValidationError):
            ProjectPlanService.assign(self.planner, parent, version, replace=True)
        with self.assertRaises(ValidationError):
            ProjectPlanService.remove(self.planner, parent, member.pk)
        parent.title = "Changed"
        with self.assertRaises(ValidationError):
            parent.save()
        with self.assertRaises(ValidationError), transaction.atomic():
            member.delete()
        with self.assertRaises(ValidationError), transaction.atomic():
            parent.delete()
        member.snapshot = {}
        with self.assertRaises(ValidationError):
            member.save()

    def test_reopen_edits_same_version_and_can_fix_again(self):
        parent = self.parent()
        version = self.approve(self.create().baseline_version)
        member = ProjectPlanService.assign(self.planner, parent, version)
        parent = ProjectPlanService.fix(self.planner, parent)
        self.client.force_login(self.planner)
        url = reverse("planning:project_plan_detail", args=[parent.pk])
        self.assertContains(self.client.get(url), "Редактировать состав")
        self.assertRedirects(self.client.post(url, {"action": "reopen"}), url)
        parent.refresh_from_db()
        member.refresh_from_db()
        self.assertEqual(parent.status, "DRAFT")
        self.assertIsNone(parent.fixed_at)
        self.assertIsNone(parent.fixed_by_id)
        self.assertEqual(member.snapshot, {})
        self.assertIsNone(member.review_id)
        ProjectPlanService.remove(self.planner, parent, member.pk)
        ProjectPlanService.assign(self.planner, parent, version)
        fixed = ProjectPlanService.fix(self.planner, parent)
        self.assertEqual(fixed.pk, parent.pk)
        self.assertEqual(fixed.version_number, 1)
        self.assertTrue(fixed.members.get().snapshot)

    def test_reopen_with_revoked_source_allows_removal_but_not_refixation(self):
        parent = self.parent()
        version = self.approve(self.create().baseline_version)
        member = ProjectPlanService.assign(self.planner, parent, version)
        parent = ProjectPlanService.fix(self.planner, parent)
        GlobalPlanService.transition(version, self.approvers["CEO"], "reject", "Rework")
        parent = ProjectPlanService.reopen(self.planner, parent)
        with self.assertRaises(ValidationError):
            ProjectPlanService.fix(self.planner, parent)
        ProjectPlanService.remove(self.planner, parent, member.pk)
        self.assertFalse(parent.members.exists())

    def test_reopen_rejects_foreign_user_and_direct_status_change(self):
        parent = self.parent()
        version = self.approve(self.create().baseline_version)
        ProjectPlanService.assign(self.planner, parent, version)
        parent = ProjectPlanService.fix(self.planner, parent)
        with self.assertRaises(PermissionDenied):
            ProjectPlanService.reopen(self.foreign_user, parent)
        parent.status = "DRAFT"
        parent.fixed_at = None
        parent.fixed_by = None
        with self.assertRaises(ValidationError):
            parent.save()

    def test_new_revision_copies_composition_but_has_independent_fixation(self):
        parent = self.parent()
        version = self.approve(self.create().baseline_version)
        ProjectPlanService.assign(self.planner, parent, version)
        parent = ProjectPlanService.fix(self.planner, parent)
        revision = ProjectPlanService.revision(self.planner, parent)
        self.assertEqual(revision.previous_version, parent)
        self.assertEqual(revision.version_number, parent.version_number + 1)
        self.assertEqual(revision.status, "DRAFT")
        self.assertFalse(revision.members.first().snapshot)
        ProjectPlanService.remove(self.planner, revision, revision.members.first().pk)
        self.assertEqual(parent.members.count(), 1)

    def test_view_role_and_company_boundaries_assignment_from_planning_workspace(self):
        parent = self.parent()
        workspace = self.create()
        version = self.approve(workspace.baseline_version)
        self.client.force_login(self.planner)
        response = self.client.get(
            reverse("planning:workspace_detail", args=[workspace.pk])
        )
        self.assertContains(response, "Сводная версия проекта")
        url = reverse("planning:workspace_project_assign", args=[workspace.pk])
        response = self.client.post(
            url, {"version": version.pk, "consolidated_version": parent.pk}
        )
        self.assertRedirects(
            response, reverse("planning:project_plan_detail", args=[parent.pk])
        )
        detail = reverse("planning:project_plan_detail", args=[parent.pk])
        self.client.force_login(self.approvers["CEO"])
        self.assertEqual(self.client.get(detail).status_code, 200)
        self.assertEqual(self.client.post(detail, {"action": "fix"}).status_code, 403)
        self.client.force_login(self.foreign_user)
        self.assertEqual(self.client.get(detail).status_code, 404)
        self.assertEqual(
            self.client.post(
                url, {"version": version.pk, "consolidated_version": parent.pk}
            ).status_code,
            404,
        )

    def test_assignment_form_does_not_allow_draft_plan_or_foreign_project(self):
        parent = self.parent()
        workspace = self.create()
        form = AssignmentForm(
            {
                "version": workspace.baseline_version_id,
                "consolidated_version": parent.pk,
            },
            user=self.planner,
            workspace=workspace,
        )
        self.assertFalse(form.is_valid())
        approved = self.approve(workspace.baseline_version)
        other = self.parent(project=self.other_project)
        form = AssignmentForm(
            {"version": approved.pk, "consolidated_version": other.pk},
            user=self.planner,
            workspace=workspace,
        )
        self.assertFalse(form.is_valid())
        form = AssignmentForm(
            {"version": approved.pk, "consolidated_version": parent.pk},
            user=self.planner,
            workspace=workspace,
        )
        self.assertTrue(form.is_valid(), form.errors)

    def test_failed_fixation_rolls_back_snapshots_of_all_objects(self):
        parent = self.parent()
        first = self.approve(self.create().baseline_version)
        other = ConstructionObject.objects.create(
            company=self.company, project=self.project, name="zzz second object"
        )
        workspace = WorkspaceService.create(
            self.planner, other, "Second", JAN, date(2026, 3, 31)
        )
        from .models import ResourceMonthAllocation

        ResourceMonthAllocation.objects.create(
            company=self.company,
            version=workspace.baseline_version,
            month=JAN,
            kind="labor",
            brigade=self.brigade,
            count=1,
        )
        second = self.approve(workspace.baseline_version)
        ProjectPlanService.assign(self.planner, parent, first)
        ProjectPlanService.assign(self.planner, parent, second)
        GlobalPlanService.transition(
            second, self.approvers["CEO"], "reject", "Rework second object"
        )
        with self.assertRaises(ValidationError):
            ProjectPlanService.fix(self.planner, parent)
        self.assertTrue(
            all(
                not member.snapshot and not member.review_id
                for member in parent.members.all()
            )
        )
        parent.refresh_from_db()
        self.assertEqual(parent.status, "DRAFT")
