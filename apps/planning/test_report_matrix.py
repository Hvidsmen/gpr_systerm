from copy import deepcopy
from datetime import date
from decimal import Decimal
from django.test import TestCase
from django.urls import reverse
from apps.accounts.models import Company, User, Role
from apps.projects.models import Project, ConstructionObject
from apps.production.models import DailyFact, LaborFact, EquipmentFact, FuelFact
from apps.resources.models import BrigadeGroup, BrigadeMacroGroup, EquipmentCategory
from apps.works.models import WorkGroup
from . import test_workspace as fixtures
from .models import ResourceMonthAllocation
from .workspace_services import WorkspaceService
from .global_services import GlobalPlanService
from .project_plan_services import ProjectPlanService
from .report_services import build_matrix
from .report_forms import MatrixReportForm

D, JAN, FEB = Decimal, fixtures.JAN, fixtures.FEB


def rows_in(nodes):
    return [
        row for node in nodes for row in [*node["rows"], *rows_in(node["children"])]
    ]


class MatrixReportTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        fixtures.WorkspaceTests.setUpTestData.__func__(cls)
        cls.work_group = WorkGroup.objects.create(
            company=cls.company, name="Civil works"
        )
        cls.simple.work_group = cls.work_group
        cls.simple.save()
        cls.composite.work_group = cls.work_group
        cls.composite.save()
        cls.group = BrigadeGroup.objects.create(
            company=cls.company, name="Workers group"
        )
        cls.macro = BrigadeMacroGroup.objects.create(
            company=cls.company, name="Workers macro"
        )
        cls.brigade.group, cls.brigade.macro_group = cls.group, cls.macro
        cls.brigade.save()
        cls.category = EquipmentCategory.objects.create(
            company=cls.company, name="Excavators"
        )
        cls.equipment.category = cls.category
        cls.equipment.save()

    create = fixtures.WorkspaceTests.create
    approve = fixtures.WorkspaceTests.approve

    def data(self, **kwargs):
        return {
            "start": JAN,
            "end": date(2026, 1, 31),
            "mode": "latest",
            "sections": ["works", "labor", "equipment", "fuel"],
            **kwargs,
        }

    def section(self, report, kind):
        return next(s for s in report["objects"][0]["sections"] if s["kind"] == kind)

    def row(self, report, kind, label):
        return next(
            row
            for row in rows_in(self.section(report, kind)["groups"])
            if row["label"] == label
        )

    def resource_plan(self):
        workspace = self.create([self.simple, self.composite])
        for kind, kwargs in [
            ("labor", {"brigade": self.brigade, "count": 10}),
            (
                "equipment",
                {
                    "equipment_type": self.equipment,
                    "equipment_number": "77",
                    "count": 4,
                },
            ),
            ("fuel", {"fuel_type": "DIESEL", "balance": 100, "liters": 31}),
        ]:
            ResourceMonthAllocation.objects.create(
                company=self.company,
                version=workspace.baseline_version,
                month=JAN,
                kind=kind,
                **kwargs,
            )
        return self.approve(workspace.baseline_version)

    def test_monthly_resource_averages_include_zero_facts_and_keep_missing_distinct(
        self,
    ):
        self.resource_plan()
        for day, count in [(5, 0), (6, 6)]:
            LaborFact.objects.create(
                company=self.company,
                construction_object=self.obj,
                brigade=self.brigade,
                date=JAN.replace(day=day),
                actual_workers=count,
            )
        for day, count in [(5, 5), (6, 0)]:
            EquipmentFact.objects.create(
                company=self.company,
                construction_object=self.obj,
                equipment_type=self.equipment,
                equipment_number="77",
                date=JAN.replace(day=day),
                actual_count=count,
            )
        report = build_matrix(self.planner, self.data())
        cell = self.row(report, "labor", self.brigade.name)["cells"][0]
        self.assertEqual(cell["plan"], 10)
        self.assertEqual(cell["fact"], 3)
        self.assertEqual(cell["fact_days"], 2)
        self.assertEqual(cell["days"], 31)
        self.assertEqual(cell["delta"], -7)
        self.assertEqual(
            self.row(report, "equipment", "Excavator 77")["cells"][0]["fact"], D("2.5")
        )
        daily = build_matrix(self.planner, self.data(month=JAN))
        cells = self.row(daily, "labor", self.brigade.name)["cells"]
        self.assertIsNone(cells[0]["fact"])
        self.assertEqual(cells[4]["fact"], 0)
        self.assertEqual(cells[3]["plan"], 10)  # weekend, not only workdays

    def test_fuel_expense_summed_and_stock_last_known_not_summed(self):
        self.resource_plan()
        for day, expense, stock in [(5, 5, 90), (6, 2, 80)]:
            FuelFact.objects.create(
                company=self.company,
                construction_object=self.obj,
                date=JAN.replace(day=day),
                fuel_type="DIESEL",
                actual_liters=expense,
                actual_balance=stock,
            )
        report = build_matrix(self.planner, self.data())
        expense = self.row(report, "fuel", "Расход")["cells"][0]
        stock = self.row(report, "fuel", "Остаток")["cells"][0]
        self.assertEqual(expense["plan"], 31)
        self.assertEqual(expense["fact"], 7)
        self.assertEqual(stock["plan"], 100)
        self.assertEqual(stock["fact"], 80)
        self.assertEqual(stock["stock_date"], JAN.replace(day=6))
        self.assertEqual(self.row(report, "fuel", "Остаток")["total"]["fact"], 80)

    def test_composite_actual_uses_history_before_period_and_subwork_values(self):
        self.resource_plan()
        DailyFact.objects.create(
            company=self.company,
            project_work=self.composite,
            work_item=self.a,
            date=date(2025, 12, 31),
            actual_quantity=2,
        )
        DailyFact.objects.create(
            company=self.company,
            project_work=self.composite,
            work_item=self.b,
            date=JAN.replace(day=5),
            actual_quantity=3,
        )
        report = build_matrix(self.planner, self.data())
        row = self.row(report, "works", "Composite")
        self.assertEqual(row["cells"][0]["fact"], 1)
        self.assertEqual(row["cells"][0]["plan"], 300)
        self.assertEqual(row["children"][1]["cells"][0]["fact"], 3)
        self.assertIsNone(row["children"][0]["cells"][0]["fact"])
        self.assertEqual(row["children"][0]["cells"][0]["plan"], 600)

    def test_latest_and_explicit_versions_no_overlap_double_counting(self):
        workspace = self.create()
        base = self.approve(workspace.baseline_version)
        workspace.refresh_from_db()
        forecast = WorkspaceService.forecast(workspace, self.planner, JAN, "BASELINE")
        row = forecast.work_allocations.get(month=JAN)
        row.quantity = 111
        row.save()
        forecast = self.approve(forecast)
        latest = build_matrix(self.planner, self.data())
        self.assertEqual(self.row(latest, "works", "Simple")["cells"][0]["plan"], 111)
        explicit = build_matrix(
            self.planner, self.data(**{f"version_{self.obj.pk}": base})
        )
        self.assertEqual(self.row(explicit, "works", "Simple")["cells"][0]["plan"], 300)
        baseline = build_matrix(self.planner, self.data(mode="baseline"))
        self.assertEqual(self.row(baseline, "works", "Simple")["cells"][0]["plan"], 300)

    def test_fixed_composition_report_keeps_snapshot_even_after_source_reapproval(self):
        version = self.resource_plan()
        parent = ProjectPlanService.create(
            self.planner, self.obj.project, "Fixed January", JAN, date(2026, 1, 31)
        )
        ProjectPlanService.assign(self.planner, parent, version)
        parent = ProjectPlanService.fix(self.planner, parent)
        original = build_matrix(self.planner, self.data(consolidated=parent))
        original_value = self.row(original, "works", "Simple")["cells"][0]["plan"]
        version = GlobalPlanService.transition(
            version, self.approvers["CEO"], "reject", "Change volumes"
        )
        row = version.work_allocations.get(month=JAN, work=self.simple)
        row.quantity = 999
        row.save()
        rejected = build_matrix(self.planner, self.data(consolidated=parent))
        self.assertEqual(
            self.row(rejected, "works", "Simple")["cells"][0]["plan"], original_value
        )
        self.assertTrue(rejected["objects"][0]["warnings"])
        self.approve(version)
        final = build_matrix(self.planner, self.data(consolidated=parent))
        self.assertEqual(
            self.row(final, "works", "Simple")["cells"][0]["plan"], original_value
        )
        self.assertTrue(final["objects"][0]["warnings"])
        self.assertEqual(
            self.row(build_matrix(self.planner, self.data()), "works", "Simple")[
                "cells"
            ][0]["plan"],
            999,
        )

    def test_filters_and_section_hiding_are_independent(self):
        self.resource_plan()
        report = build_matrix(
            self.planner,
            self.data(
                sections=["works"],
                works_q="Composite",
                work_group=self.work_group,
                work_kind="COMPOSITE",
            ),
        )
        self.assertEqual(
            [s["kind"] for s in report["objects"][0]["sections"]], ["works"]
        )
        self.assertEqual(
            [r["label"] for r in rows_in(self.section(report, "works")["groups"])],
            ["Composite"],
        )
        report = build_matrix(self.planner, self.data(labor_q="missing"))
        self.assertEqual(self.section(report, "labor")["groups"], [])
        report = build_matrix(
            self.planner,
            self.data(
                macro_group=self.macro,
                brigade_group=self.group,
                equipment_category=self.category,
                fuel_type="PETROL_95",
            ),
        )
        self.assertTrue(self.section(report, "labor")["groups"])
        self.assertTrue(self.section(report, "equipment")["groups"])
        self.assertFalse(self.section(report, "fuel")["groups"])

    def test_fact_only_rows_show_missing_plan_and_partial_period_is_clipped(self):
        LaborFact.objects.create(
            company=self.company,
            construction_object=self.obj,
            brigade=self.brigade,
            date=JAN.replace(day=6),
            actual_workers=8,
        )
        report = build_matrix(
            self.planner,
            self.data(start=JAN.replace(day=5), end=JAN.replace(day=7), month=JAN),
        )
        self.assertEqual(len(report["columns"]), 3)
        cell = self.row(report, "labor", self.brigade.name)["cells"][1]
        self.assertIsNone(cell["plan"])
        self.assertEqual(cell["fact"], 8)
        self.assertIsNone(cell["delta"])

    def test_views_drill_links_preserve_all_filters_and_hide_sections(self):
        self.resource_plan()
        self.client.force_login(self.planner)
        response = self.client.get(
            reverse("planning:report_matrix"),
            {
                "start": "2026-01-01",
                "end": "2026-03-31",
                "objects": [self.obj.pk],
                "works_q": "Composite",
                "sections": ["works"],
                "sections_set": "1",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-matrix-section="works"')
        self.assertNotContains(response, 'data-matrix-section="labor"')
        url = response.context["columns"][0]["url"]
        self.assertIn("works_q=Composite", url)
        self.assertIn("month=2026-01-01", url)
        daily = self.client.get(url)
        self.assertEqual(len(daily.context["columns"]), 31)
        self.assertContains(daily, "К месяцам")
        self.assertIn("works_q=Composite", daily.context["monthly_url"])
        self.assertNotIn("month=", daily.context["monthly_url"])

    def test_foreign_filters_and_wrong_object_versions_are_rejected(self):
        version = self.resource_plan()
        foreign = Company.objects.create(name="Foreign reports")
        project = Project.objects.create(company=foreign, name="Foreign report project")
        obj = ConstructionObject.objects.create(
            company=foreign, project=project, name="Foreign report object"
        )
        data = {
            "start": "2026-01-01",
            "end": "2026-01-31",
            "mode": "latest",
            "project": project.pk,
            "objects": [obj.pk],
        }
        form = MatrixReportForm(data, user=self.planner)
        self.assertFalse(form.is_valid())
        self.assertIn("project", form.errors)
        self.assertIn("objects", form.errors)
        self.client.force_login(self.approvers["CEO"])
        self.assertEqual(
            self.client.get(reverse("planning:report_matrix")).status_code, 200
        )
        self.client.force_login(
            User.objects.create_user(
                username="matrix-master",
                company=self.company,
                role=Role.objects.get(code="FOREMAN"),
            )
        )
        self.assertEqual(
            self.client.get(reverse("planning:report_matrix")).status_code, 403
        )

    def test_consolidated_default_period_and_invalid_month_and_period_errors(self):
        version = self.resource_plan()
        parent = ProjectPlanService.create(
            self.planner, self.obj.project, "Fixed January", JAN, date(2026, 1, 31)
        )
        ProjectPlanService.assign(self.planner, parent, version)
        parent = ProjectPlanService.fix(self.planner, parent)
        self.client.force_login(self.planner)
        response = self.client.get(
            reverse("planning:report_matrix"), {"consolidated": parent.pk}
        )
        self.assertEqual(response.context["start"], JAN)
        self.assertEqual(response.context["end"], date(2026, 1, 31))
        self.assertEqual(len(response.context["columns"]), 1)
        for extra in [
            {"month": "2026-02-01"},
            {"start": "2026-03-01", "end": "2026-01-01"},
            {"end": "2026-02-28"},
        ]:
            response = self.client.get(
                reverse("planning:report_matrix"), {"consolidated": parent.pk, **extra}
            )
            self.assertTrue(response.context["form"].errors)

    def test_multiple_objects_and_project_filter_do_not_mix_versions_or_facts(self):
        base = self.resource_plan()
        project = Project.objects.create(
            company=self.company, name="Another matrix project"
        )
        obj = ConstructionObject.objects.create(
            company=self.company, project=project, name="Second matrix object"
        )
        workspace = WorkspaceService.create(
            self.planner, obj, "Other object", JAN, date(2026, 1, 31)
        )
        ResourceMonthAllocation.objects.create(
            company=self.company,
            version=workspace.baseline_version,
            month=JAN,
            kind="labor",
            brigade=self.brigade,
            count=22,
        )
        other_version = self.approve(workspace.baseline_version)
        LaborFact.objects.create(
            company=self.company,
            construction_object=obj,
            brigade=self.brigade,
            date=JAN,
            actual_workers=23,
        )
        report = build_matrix(self.planner, self.data())
        self.assertEqual(
            {entry["object"].pk for entry in report["objects"]}, {self.obj.pk, obj.pk}
        )
        other = build_matrix(self.planner, self.data(project=project))
        self.assertEqual(len(other["objects"]), 1)
        self.assertEqual(
            self.row(other, "labor", self.brigade.name)["cells"][0]["plan"], 22
        )
        self.assertEqual(
            self.row(other, "labor", self.brigade.name)["cells"][0]["fact"], 23
        )
        form = MatrixReportForm(
            {
                "start": "2026-01-01",
                "end": "2026-01-31",
                "mode": "latest",
                f"version_{self.obj.pk}": other_version.pk,
            },
            user=self.planner,
        )
        self.assertFalse(form.is_valid())
        self.assertIn(f"version_{self.obj.pk}", form.errors)

    def test_period_average_is_weighted_by_actual_days_and_not_monthly_averages(self):
        workspace = self.create()
        for month, count in [(JAN, 10), (FEB, 20)]:
            ResourceMonthAllocation.objects.create(
                company=self.company,
                version=workspace.baseline_version,
                month=month,
                kind="labor",
                brigade=self.brigade,
                count=count,
            )
        self.approve(workspace.baseline_version)
        for day, count in [(JAN, 0), (JAN.replace(day=2), 6), (FEB, 9)]:
            LaborFact.objects.create(
                company=self.company,
                construction_object=self.obj,
                brigade=self.brigade,
                date=day,
                actual_workers=count,
            )
        report = build_matrix(self.planner, self.data(end=date(2026, 2, 28)))
        row = self.row(report, "labor", self.brigade.name)
        self.assertEqual(row["cells"][0]["fact"], 3)
        self.assertEqual(row["cells"][1]["fact"], 9)
        self.assertEqual(row["total"]["fact"], 5)
        self.assertEqual(row["total"]["plan"], D(870) / 59)
        self.assertEqual(row["total"]["fact_days"], 3)
        self.assertEqual(row["total"]["days"], 59)

    def test_frozen_catalog_grouping_and_all_sections_hidden(self):
        version = self.resource_plan()
        parent = ProjectPlanService.create(
            self.planner, self.obj.project, "Fixed names", JAN, date(2026, 1, 31)
        )
        ProjectPlanService.assign(self.planner, parent, version)
        parent = ProjectPlanService.fix(self.planner, parent)
        old_name = self.brigade.name
        self.brigade.name = "New brigade name"
        self.brigade.save()
        self.group.name = "New group name"
        self.group.save()
        fixed = build_matrix(self.planner, self.data(consolidated=parent))
        self.assertEqual(self.row(fixed, "labor", old_name)["cells"][0]["plan"], 10)
        group = self.section(fixed, "labor")["groups"][0]["children"][0]
        self.assertEqual(group["label"], "Workers group")
        self.client.force_login(self.planner)
        response = self.client.get(
            reverse("planning:report_matrix"),
            {"start": "2026-01-01", "end": "2026-01-31", "sections_set": "1"},
        )
        self.assertContains(response, "Все разделы скрыты")
        self.assertNotContains(response, "data-matrix-section=")

    def test_remaining_scenario_past_plan_equals_fact_with_missing_resource_days(self):
        workspace = self.create()
        ResourceMonthAllocation.objects.create(
            company=self.company,
            version=workspace.baseline_version,
            month=JAN,
            kind="labor",
            brigade=self.brigade,
            count=10,
        )
        base = self.approve(workspace.baseline_version)
        for day, count in [(5, 0), (6, 6)]:
            LaborFact.objects.create(
                company=self.company,
                construction_object=self.obj,
                brigade=self.brigade,
                date=JAN.replace(day=day),
                actual_workers=count,
            )
        forecast = WorkspaceService.forecast(
            base.workspace, self.planner, FEB, "REMAINING"
        )
        self.approve(forecast)
        report = build_matrix(self.planner, self.data())
        cell = self.row(report, "labor", self.brigade.name)["cells"][0]
        self.assertEqual(cell["plan"], 3)
        self.assertEqual(cell["fact"], 3)
        self.assertEqual(cell["delta"], 0)
        self.assertEqual(cell["plan_days"], 2)
        daily = build_matrix(self.planner, self.data(month=JAN))
        cell = self.row(daily, "labor", self.brigade.name)["cells"][0]
        self.assertIsNone(cell["plan"])
        self.assertIsNone(cell["fact"])
