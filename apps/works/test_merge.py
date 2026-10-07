from copy import deepcopy
from datetime import date
from decimal import Decimal as D
from django.test import TestCase
from django.urls import reverse
from django.core.exceptions import ValidationError
from apps.planning import test_workspace as fixtures
from apps.planning.models import (
    LoadProfile,
    LoadProfileItem,
    WorkMonthAllocation,
    GlobalPlanVersion,
)
from apps.planning.workspace_services import WorkspaceService, build_workspace_snapshot
from apps.planning.global_services import GlobalPlanService, comparison
from apps.planning.report_services import build_matrix
from apps.planning.test_report_matrix import rows_in
from apps.production.models import DailyFact
from .models import ProjectWork, WorkMergeSource
from .progress import WorkProgressService
from .merge_service import source_works, fingerprint, apply_merge

JAN, FEB = fixtures.JAN, fixtures.FEB


class WorkMergeTests(TestCase):
    setUpTestData = classmethod(fixtures.WorkspaceTests.setUpTestData.__func__)
    approve = fixtures.WorkspaceTests.approve

    def setUp(self):
        self.profile = LoadProfile.objects.create(company=self.company, name="Uniform")
        LoadProfileItem.objects.create(
            company=self.company, profile=self.profile, workday_number=1, percentage=100
        )
        self.simple.load_profile = self.profile
        self.simple.save()
        self.second = ProjectWork.objects.create(
            company=self.company,
            section=self.simple.section,
            name="Paint",
            unit="m3",
            load_profile=self.profile,
            unit_price=2,
        )
        self.works = [self.simple, self.second]
        self.data = {
            "name": "Assembly",
            "section": self.simple.section,
            "unit": "шт",
            "work_group": None,
            "allow_fractional": True,
            f"norm_{self.simple.pk}": D(2),
            f"norm_{self.second.pk}": D(3),
            f"profile_{self.simple.pk}": self.profile,
            f"profile_{self.second.pk}": self.profile,
        }
        self.client.force_login(self.planner)

    def merge(self):
        return apply_merge(
            self.planner, [w.pk for w in self.works], self.data, fingerprint(self.works)
        )

    def plan(self):
        workspace = WorkspaceService.create(
            self.planner, self.obj, "Merge plan", JAN, date(2026, 2, 28)
        )
        for month, a, b in [(JAN, 20, 24), (FEB, 8, 12)]:
            for work, qty in [(self.simple, a), (self.second, b)]:
                WorkMonthAllocation.objects.create(
                    company=self.company,
                    version=workspace.baseline_version,
                    work=work,
                    month=month,
                    quantity=qty,
                )
        return workspace.baseline_version

    def facts(self):
        rows = []
        for work, day, qty in [
            (self.simple, JAN, 18),
            (self.second, JAN, 18),
            (self.second, JAN.replace(day=2), 3),
        ]:
            rows.append(
                DailyFact.objects.create(
                    company=self.company,
                    project_work=work,
                    date=day,
                    actual_quantity=qty,
                    reported_by=self.planner,
                    comment="Original",
                )
            )
        return rows

    def test_fact_transfer_preserves_records_and_cumulative_completion(self):
        facts = self.facts()
        original = {
            f.pk: (
                f.actual_quantity,
                f.actual_value,
                f.date,
                f.reported_by_id,
                f.comment,
            )
            for f in facts
        }
        parent, revisions = self.merge()
        self.assertEqual(revisions, [])
        self.assertEqual(WorkProgressService.completed(parent), D(7))
        self.assertEqual(WorkProgressService.facts(parent)[JAN]["daily"], D(6))
        self.assertEqual(
            WorkProgressService.facts(parent)[JAN.replace(day=2)]["daily"], D(1)
        )
        for fact in facts:
            fact.refresh_from_db()
            self.assertEqual(fact.project_work, parent)
            self.assertIsNotNone(fact.work_item_id)
            self.assertEqual(
                (
                    fact.actual_quantity,
                    fact.actual_value,
                    fact.date,
                    fact.reported_by_id,
                    fact.comment,
                ),
                original[fact.pk],
            )
        self.assertEqual(WorkProgressService.completed(self.simple), 18)
        self.assertNotContains(self.client.get(reverse("works:work_list")), "Simple")
        self.assertEqual(
            self.client.get(reverse("works:work_detail", args=[parent.pk])).status_code,
            200,
        )

    def test_draft_plan_preserves_daily_subwork_volumes(self):
        version = self.plan()
        before = build_workspace_snapshot(version)
        parent, revisions = self.merge()
        self.assertEqual(revisions, [])
        self.assertFalse(version.work_allocations.filter(work__in=self.works).exists())
        after = build_workspace_snapshot(version)
        spec = next(s for s in after["works"] if s["id"] == parent.pk)
        expected = {
            w.pk: [
                (r["date"], r["quantity"])
                for s in before["works"]
                if s["id"] == w.pk
                for r in s["plans"]
            ]
            for w in self.works
        }
        for link in WorkMergeSource.objects.filter(item__project_work=parent):
            self.assertEqual(
                [
                    (r["date"], r["quantity"])
                    for r in spec["plans"]
                    if r["item_id"] == link.item_id
                ],
                expected[link.source_work_id],
            )
        self.assertEqual(
            version.work_allocations.get(work=parent, month=JAN).quantity, 8
        )
        self.assertEqual(sum(D(r["quantity"]) for r in spec["daily"]), 12)
        allocation = version.work_allocations.get(work=parent, month=JAN)
        allocation.item_quantities = {}
        allocation.quantity = 10
        allocation.save()
        self.assertIsNone(allocation.daily_override)
        self.assertEqual(
            sum(
                D(r["quantity"])
                for r in build_workspace_snapshot(version)["works"][0]["daily"]
            ),
            14,
        )

    def test_approved_plan_history_and_report_fact_remain_valid(self):
        version = self.approve(self.plan())
        facts = self.facts()
        original = deepcopy(version.snapshot)
        parent, revisions = self.merge()
        self.assertEqual(len(revisions), 1)
        target = revisions[0]
        self.assertEqual(target.status, "DRAFT")
        self.assertNotEqual(target.workspace_id, version.workspace_id)
        version.refresh_from_db()
        self.assertEqual(version.snapshot, original)
        self.assertEqual(version.status, "APPROVED")
        oldcomparison = comparison(version)
        self.assertEqual(
            next(
                r for r in oldcomparison["works"] if r["spec"]["id"] == self.simple.pk
            )["fact"],
            18,
        )
        report = build_matrix(
            self.planner,
            {
                "start": JAN,
                "end": date(2026, 1, 31),
                "sections": ["works"],
                "mode": "latest",
            },
        )
        rows = rows_in(report["objects"][0]["sections"][0]["groups"])
        self.assertEqual({r["label"] for r in rows}, {"Simple", "Paint"})
        self.assertEqual(
            next(r for r in rows if r["label"] == "Simple")["total"]["fact"], 18
        )
        draft = build_workspace_snapshot(target)
        self.assertEqual(draft["works"][0]["id"], parent.pk)
        approved = self.approve(target)
        report = build_matrix(
            self.planner,
            {
                "start": JAN,
                "end": date(2026, 1, 31),
                "sections": ["works"],
                "mode": "latest",
            },
        )
        rows = rows_in(report["objects"][0]["sections"][0]["groups"])
        self.assertEqual({r["label"] for r in rows}, {"Assembly"})
        self.assertEqual(rows[0]["total"]["fact"], 7)

    def test_ui_preview_then_confirm_and_replay_protection(self):
        self.facts()
        version = self.plan()
        url = reverse("works:work_merge")
        response = self.client.post(
            url, {"merge_stage": "select", "selected": [w.pk for w in self.works]}
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Норматив")
        data = {
            key: str(value.pk) if hasattr(value, "pk") else str(value)
            for key, value in self.data.items()
            if value is not None
        }
        data["allow_fractional"] = "on"
        response = self.client.post(
            url,
            {**data, "selected": [w.pk for w in self.works], "merge_stage": "preview"},
        )
        self.assertFalse(response.context["form"].errors)
        self.assertContains(response, "Подтвердить объединение")
        self.assertFalse(WorkMergeSource.objects.exists())
        token = response.context["preview"]
        confirmed = self.client.post(url, {"merge_stage": "confirm", "preview": token})
        self.assertEqual(confirmed.status_code, 302)
        self.assertEqual(WorkMergeSource.objects.count(), 2)
        again = self.client.post(url, {"merge_stage": "confirm", "preview": token})
        self.assertEqual(again.status_code, 302)
        self.assertEqual(WorkMergeSource.objects.count(), 2)

    def test_changed_facts_cancel_merge_atomically(self):
        expected = fingerprint(self.works)
        self.facts()
        with self.assertRaises(ValidationError):
            apply_merge(self.planner, [w.pk for w in self.works], self.data, expected)
        self.assertFalse(WorkMergeSource.objects.exists())
        self.assertFalse(ProjectWork.objects.filter(name="Assembly").exists())

    def test_fractional_toggle_and_norm_validation(self):
        self.facts()
        self.data[f"norm_{self.second.pk}"] = D(4)
        self.data["allow_fractional"] = False
        parent, _ = self.merge()
        self.assertEqual(WorkProgressService.completed(parent), 5)
        with self.assertRaises(ValidationError):
            source_works(self.planner, [w.pk for w in self.works])

    def test_existing_forecast_keeps_every_daily_subwork_volume(self):
        base = self.approve(self.plan())
        version = WorkspaceService.forecast(
            base.workspace, self.planner, FEB, "BASELINE"
        )
        before = build_workspace_snapshot(version)
        parent, revisions = self.merge()
        self.assertEqual(revisions, [])
        after = build_workspace_snapshot(version)
        spec = after["works"][0]
        self.assertEqual(spec["id"], parent.pk)
        for link in WorkMergeSource.objects.filter(item__project_work=parent):
            expected = [
                (r["date"], r["quantity"])
                for old in before["works"]
                if old["id"] == link.source_work_id
                for r in old["plans"]
            ]
            self.assertEqual(
                [
                    (r["date"], r["quantity"])
                    for r in spec["plans"]
                    if r["item_id"] == link.item_id
                ],
                expected,
            )
        version = self.approve(version)
        later = WorkspaceService.forecast(
            base.workspace, self.planner, FEB, "BASELINE", previous=version
        )
        self.assertEqual(
            list(later.work_allocations.values_list("work_id", flat=True)), [parent.pk]
        )
        self.assertEqual(build_workspace_snapshot(later)["works"][0]["id"], parent.pk)

    def test_approved_forecast_gets_new_draft_without_changing_source(self):
        base = self.approve(self.plan())
        version = self.approve(
            WorkspaceService.forecast(base.workspace, self.planner, FEB, "BASELINE")
        )
        before = deepcopy(version.snapshot)
        parent, revisions = self.merge()
        self.assertEqual(len(revisions), 1)
        target = revisions[0]
        self.assertEqual(target.version_kind, "FORECAST")
        self.assertEqual(target.workspace_id, version.workspace_id)
        after = build_workspace_snapshot(target)
        self.assertEqual(after["works"][0]["id"], parent.pk)
        version.refresh_from_db()
        self.assertEqual(version.snapshot, before)
        self.approve(target)

    def test_archived_source_cannot_receive_new_fact(self):
        parent, _ = self.merge()
        with self.assertRaises(ValidationError):
            DailyFact.objects.create(
                company=self.company,
                project_work=self.simple,
                date=JAN,
                actual_quantity=1,
            )
        self.assertEqual(DailyFact.objects.count(), 0)

    def test_invalid_norm_rolls_back_and_other_objects_are_rejected(self):
        self.data[f"norm_{self.simple.pk}"] = D(0)
        with self.assertRaises(ValidationError):
            self.merge()
        self.assertFalse(WorkMergeSource.objects.exists())
        self.assertFalse(ProjectWork.objects.filter(name="Assembly").exists())
        from apps.projects.models import ConstructionObject, Section

        other = ConstructionObject.objects.create(
            company=self.company, project=self.obj.project, name="Other"
        )
        section = Section.objects.create(
            company=self.company, construction_object=other, name="Other"
        )
        self.second.section = section
        self.second.save()
        with self.assertRaises(ValidationError):
            source_works(self.planner, [w.pk for w in self.works])

    def test_manager_and_foreman_cannot_merge(self):
        from apps.accounts.models import User, Role

        for code in ["MANAGER", "FOREMAN"]:
            user = User.objects.create_user(
                username="merge-" + code,
                company=self.company,
                role=Role.objects.get(code=code),
            )
            self.client.force_login(user)
            response = self.client.post(
                reverse("works:work_merge"),
                {"merge_stage": "select", "selected": [w.pk for w in self.works]},
            )
            self.assertEqual(response.status_code, 403)

    def test_legacy_daily_plans_and_global_version_are_preserved(self):
        from apps.planning.models import MonthlyPlan, PlanVersion, DailyPlan
        from apps.planning.global_services import build_snapshot

        old_versions = []
        for work, qty in [(self.simple, 20), (self.second, 24)]:
            plan = MonthlyPlan.objects.create(
                company=self.company,
                project_work=work,
                year=2026,
                month=1,
                start_date=JAN,
                end_date=date(2026, 1, 31),
                planned_quantity=qty,
            )
            version = PlanVersion.objects.create(
                company=self.company,
                monthly_plan=plan,
                version_number=1,
                status="APPROVED",
                created_by=self.planner,
            )
            DailyPlan.objects.create(
                company=self.company,
                plan_version=version,
                date=JAN,
                workday_number=1,
                planned_quantity=qty,
                planned_value=qty * work.unit_price,
            )
            old_versions.append(version)
        global_version = GlobalPlanService.create(
            self.planner, self.obj, JAN, date(2026, 1, 31)
        )
        global_version = self.approve(global_version)
        old_snapshot = deepcopy(global_version.snapshot)
        parent, revisions = self.merge()
        self.assertEqual(len(revisions), 1)
        target = revisions[0]
        self.assertEqual(target.version_kind, "LEGACY")
        self.assertEqual(build_snapshot(target)["works"][0]["id"], parent.pk)
        self.assertEqual(
            sum(
                D(row["quantity"])
                for row in build_snapshot(target)["works"][0]["daily"]
            ),
            8,
        )
        new_plan = MonthlyPlan.objects.get(project_work=parent)
        self.assertEqual(new_plan.versions.get().status, "DRAFT")
        self.assertEqual(new_plan.versions.get().daily_plans.count(), 2)
        global_version.refresh_from_db()
        self.assertEqual(global_version.snapshot, old_snapshot)
        self.assertEqual(
            [version.daily_plans.count() for version in old_versions], [1, 1]
        )
        self.approve(target)

    def test_different_month_profiles_keep_original_dates(self):
        version = self.plan()
        profile = LoadProfile.objects.create(company=self.company, name="Last day")
        LoadProfileItem.objects.create(
            company=self.company, profile=profile, workday_number=1, percentage=0
        )
        LoadProfileItem.objects.create(
            company=self.company, profile=profile, workday_number=2, percentage=100
        )
        row = version.work_allocations.get(work=self.second, month=JAN)
        row.load_profile = profile
        row.save()
        before = build_workspace_snapshot(version)
        parent, _ = self.merge()
        after = build_workspace_snapshot(version)["works"][0]
        item = WorkMergeSource.objects.get(source_work=self.second).item
        expected = [
            (r["date"], r["quantity"])
            for spec in before["works"]
            if spec["id"] == self.second.pk
            for r in spec["plans"]
        ]
        self.assertEqual(
            [
                (r["date"], r["quantity"])
                for r in after["plans"]
                if r["item_id"] == item.pk
            ],
            expected,
        )

    def test_moved_fact_remains_editable_and_deletable_in_journal(self):
        facts = self.facts()
        parent, _ = self.merge()
        response = self.client.get(
            reverse("production:fact_list"), {"start": JAN, "end": date(2026, 1, 31)}
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Assembly")
        self.assertNotContains(response, "Простая работа")
        # Administrator role owns fact deletion; planner may only read it.
        from apps.accounts.models import User, Role

        admin = User.objects.create_user(
            username="merge-admin",
            company=self.company,
            role=Role.objects.get(code="ADMIN"),
        )
        self.client.force_login(admin)
        fact = facts[-1]
        self.assertEqual(
            self.client.get(
                reverse("production:fact_update", args=[fact.pk])
            ).status_code,
            200,
        )
        response = self.client.post(reverse("production:fact_delete", args=[fact.pk]))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(WorkProgressService.completed(parent), 6)

    def test_foreign_work_and_section_are_rejected(self):
        from apps.accounts.models import Company, User, Role
        from apps.projects.models import Project, ConstructionObject, Section

        other = Company.objects.create(name="Foreign merge")
        project = Project.objects.create(company=other, name="Foreign")
        obj = ConstructionObject.objects.create(
            company=other, project=project, name="Foreign"
        )
        section = Section.objects.create(
            company=other, construction_object=obj, name="Foreign"
        )
        work = ProjectWork.objects.create(
            company=other, section=section, name="Foreign work", unit="шт"
        )
        with self.assertRaises(ValidationError):
            source_works(self.planner, [self.simple.pk, work.pk])
        self.data["section"] = section
        with self.assertRaises(ValidationError):
            self.merge()
        self.assertFalse(WorkMergeSource.objects.exists())

    def test_merged_origin_blocks_deletion_of_source_parent_and_items(self):
        parent, _ = self.merge()
        for work in [self.simple, parent]:
            self.assertEqual(
                self.client.post(
                    reverse("works:work_delete", args=[work.pk])
                ).status_code,
                403,
            )
        item = parent.items.first()
        self.assertEqual(
            self.client.post(
                reverse("works:work_item_delete", args=[item.pk])
            ).status_code,
            403,
        )
        self.assertEqual(parent.items.count(), 2)

    def test_new_draft_can_be_deleted_without_losing_merge_lineage(self):
        from apps.planning.deletion import delete_workspace
        from .models import WorkMergePlanRevision

        old = self.approve(self.plan())
        parent, revisions = self.merge()
        target = revisions[0]
        delete_workspace(target.workspace, self.planner)
        self.assertFalse(GlobalPlanVersion.objects.filter(pk=target.pk).exists())
        self.assertEqual(WorkMergeSource.objects.count(), 2)
        self.assertIsNone(
            WorkMergePlanRevision.objects.get(parent_work=parent).target_version_id
        )
        self.assertEqual(
            self.client.get(reverse("works:work_detail", args=[parent.pk])).status_code,
            200,
        )

    def test_remainder_forecast_retains_each_source_volume(self):
        base = self.approve(self.plan())
        version = WorkspaceService.forecast(
            base.workspace, self.planner, JAN, "REMAINING"
        )
        before = build_workspace_snapshot(version)
        parent, _ = self.merge()
        after = build_workspace_snapshot(version)
        for link in WorkMergeSource.objects.filter(item__project_work=parent):
            expected = [
                (r["date"], r["quantity"])
                for old in before["works"]
                if old["id"] == link.source_work_id
                for r in old["plans"]
            ]
            actual = [
                (r["date"], r["quantity"])
                for r in after["works"][0]["plans"]
                if r["item_id"] == link.item_id
            ]
            self.assertEqual(actual, expected)
        self.approve(version)

    def test_rounded_parent_price_keeps_weights_at_one_hundred_percent(self):
        for work in self.works:
            work.unit_price = D(".01")
            work.save()
            self.data[f"norm_{work.pk}"] = D(".7")
        parent, _ = self.merge()
        self.assertEqual(parent.unit_price, D(".01"))
        self.assertEqual(sum(parent.items.values_list("weight", flat=True)), D(100))
