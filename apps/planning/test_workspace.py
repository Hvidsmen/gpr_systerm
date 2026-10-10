from .approval_test_helpers import approval_users, departments
from datetime import date
from decimal import Decimal
from copy import deepcopy
from django.core.exceptions import ValidationError, PermissionDenied
from django.db import transaction
from django.test import TestCase
from django.urls import reverse
from apps.accounts.models import Company, User, Role
from apps.projects.models import Project, ConstructionObject, Section
from apps.resources.models import Brigade, EquipmentType
from apps.works.models import ProjectWork, ProjectWorkItem
from apps.production.models import (
    DailyFact,
    LaborFact,
    EquipmentFact,
    FuelFact,
    LaborPlan,
)
from .models import (
    PlanningWorkspace,
    WorkMonthAllocation,
    ResourceMonthAllocation,
    GlobalPlanVersion,
    ProductionCalendar,
    CalendarDay,
    LoadProfile,
    LoadProfileItem,
)
from .workspace_services import (
    WorkspaceService,
    months_between,
    build_workspace_snapshot,
)
from .global_services import GlobalPlanService, comparison

D = Decimal
JAN = date(2026, 1, 1)
FEB = date(2026, 2, 1)
MAR = date(2026, 3, 1)


class WorkspaceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.company = Company.objects.create(name="Period planning")
        cls.approvers = approval_users(cls.company)
        role = Role.objects.get(code="MANAGER")
        cls.manager = User.objects.create_user(
            username="period-manager", company=cls.company, role=role
        )
        cls.planner = User.objects.create_user(
            username="period-planner", company=cls.company, role=Role.objects.get(code="PLANNER")
        )
        project = Project.objects.create(company=cls.company, code="p", name="p")
        cls.obj = ConstructionObject.objects.create(
            company=cls.company, project=project, code="o", name="o"
        )
        section = Section.objects.create(
            company=cls.company, construction_object=cls.obj, code="s", name="s"
        )
        cls.simple = ProjectWork.objects.create(
            company=cls.company,
            section=section,
            code="simple",
            name="Simple",
            unit="m",
            unit_price=10,
        )
        cls.composite = ProjectWork.objects.create(
            company=cls.company,
            section=section,
            code="composite",
            name="Composite",
            unit="шт",
            unit_price=100,
            kind="COMPOSITE",
        )
        cls.a = ProjectWorkItem.objects.create(
            company=cls.company,
            project_work=cls.composite,
            name="A",
            unit="m",
            quantity_per_unit=2,
            weight=40,
        )
        cls.b = ProjectWorkItem.objects.create(
            company=cls.company,
            project_work=cls.composite,
            name="B",
            unit="m3",
            quantity_per_unit=3,
            weight=60,
        )
        cls.brigade = Brigade.objects.create(
            company=cls.company, code="b", name="Brigade"
        )
        cls.equipment = EquipmentType.objects.create(
            company=cls.company, name="Excavator"
        )
        calendar = ProductionCalendar.objects.create(
            company=cls.company,
            code="calendar",
            name="Calendar",
            year=2026,
            is_default=True,
        )
        for month in [JAN, FEB, MAR]:
            for day in [5, 6]:
                CalendarDay.objects.create(
                    company=cls.company,
                    calendar=calendar,
                    date=month.replace(day=day),
                    is_working=True,
                )

    def create(self, works=None):
        workspace = WorkspaceService.create(
            self.planner, self.obj, "Three-month plan", JAN, date(2026, 3, 31)
        )
        for work in works or [self.simple]:
            for month, qty in [(JAN, 300), (FEB, 200), (MAR, 500)]:
                WorkMonthAllocation.objects.create(
                    company=self.company,
                    version=workspace.baseline_version,
                    work=work,
                    month=month,
                    quantity=qty,
                )
        return workspace

    def approve(self, version):
        version = GlobalPlanService.transition(version, self.planner, "submit")
        return GlobalPlanService.transition(departments(version, self.approvers), self.approvers["CEO"], "approve")

    def fact(self, work, item, day, qty):
        return DailyFact.objects.create(
            company=self.company,
            project_work=work,
            work_item=item,
            date=day,
            actual_quantity=D(qty),
        )

    def choose(self, workspace, scenario="REMAINING", qty=220):
        version = WorkspaceService.forecast(workspace, self.planner, FEB, scenario)
        for row in version.work_allocations.all():
            row.quantity = qty
            row.save()
        return version

    def amounts(self, version, work):
        spec = next(s for s in version.snapshot["works"] if s["id"] == work.pk)
        return [
            sum(
                D(r["quantity"])
                for r in spec["daily"]
                if r["date"][:7] == month.isoformat()[:7]
            )
            for month in [JAN, FEB, MAR]
        ]

    def test_clean_workspace_does_not_copy_existing_lower_or_resource_plans(self):
        LaborPlan.objects.create(
            company=self.company,
            construction_object=self.obj,
            brigade=self.brigade,
            date=JAN,
            planned_workers=7,
        )
        workspace = WorkspaceService.create(
            self.planner, self.obj, "Clean", JAN, date(2026, 3, 31)
        )
        self.assertEqual(workspace.baseline_version.version_kind, "BASELINE")
        self.assertFalse(workspace.baseline_version.work_allocations.exists())
        self.assertFalse(workspace.baseline_version.resource_allocations.exists())
        self.assertEqual(workspace.baseline_version.snapshot, {})

    def test_base_months_generate_daily_simple_and_composite_plans(self):
        workspace = self.create([self.simple, self.composite])
        base = self.approve(workspace.baseline_version)
        self.assertEqual(self.amounts(base, self.simple), [D(300), D(200), D(500)])
        self.assertEqual(self.amounts(base, self.composite), [D(300), D(200), D(500)])
        spec = next(s for s in base.snapshot["works"] if s["id"] == self.composite.pk)
        self.assertEqual(
            sum(D(r["quantity"]) for r in spec["plans"] if r["item_id"] == self.a.pk),
            2000,
        )
        self.assertEqual(sum(D(r["value"]) for r in spec["plans"]), 100000)
        self.assertEqual(base.start_date, JAN)
        self.assertEqual(base.end_date, date(2026, 3, 31))

    def test_scenario_one_matches_250_220_530_without_changing_baseline(self):
        workspace = self.create()
        base = self.approve(workspace.baseline_version)
        frozen = deepcopy(base.snapshot)
        self.fact(self.simple, None, JAN.replace(day=5), 250)
        version = WorkspaceService.refresh(self.choose(workspace), self.planner)
        self.assertEqual(self.amounts(version, self.simple), [D(250), D(220), D(530)])
        base.refresh_from_db()
        self.assertEqual(base.snapshot, frozen)
        self.assertEqual(version.start_date, workspace.start_date)
        self.assertEqual(version.end_date, workspace.end_date)

    def test_scenario_two_keeps_base_before_and_after_and_shows_total_deviation(self):
        workspace = self.create()
        self.approve(workspace.baseline_version)
        self.fact(self.simple, None, JAN.replace(day=5), 250)
        version = WorkspaceService.refresh(
            self.choose(workspace, "BASELINE"), self.planner
        )
        self.assertEqual(self.amounts(version, self.simple), [D(300), D(220), D(500)])
        self.assertEqual(sum(self.amounts(version, self.simple)), 1020)
        spec = version.snapshot["works"][0]
        base = workspace.baseline_version
        base.refresh_from_db()
        self.assertEqual(
            [r for r in spec["plans"] if r["date"][:7] == "2026-03"],
            [
                r
                for r in base.snapshot["works"][0]["plans"]
                if r["date"][:7] == "2026-03"
            ],
        )

    def test_composite_past_is_cumulative_main_fact_not_sum_of_subwork_quantities(self):
        workspace = self.create([self.composite])
        self.approve(workspace.baseline_version)
        self.fact(self.composite, self.a, JAN.replace(day=5), 500)
        self.fact(self.composite, self.b, JAN.replace(day=6), 750)
        version = WorkspaceService.refresh(self.choose(workspace), self.planner)
        self.assertEqual(
            self.amounts(version, self.composite), [D(250), D(220), D(530)]
        )

    def test_fact_before_period_is_context_and_not_subtracted_from_period_budget(self):
        workspace = self.create()
        self.approve(workspace.baseline_version)
        self.fact(self.simple, None, date(2025, 12, 30), 50)
        self.fact(self.simple, None, JAN.replace(day=5), 250)
        version = WorkspaceService.refresh(self.choose(workspace), self.planner)
        self.assertEqual(self.amounts(version, self.simple), [D(250), D(220), D(530)])
        self.assertEqual(len(version.snapshot["works"][0]["context_plans"]), 1)

    def test_approval_freezes_fact_basis_and_revision_uses_corrected_facts(self):
        workspace = self.create()
        self.approve(workspace.baseline_version)
        fact = self.fact(self.simple, None, JAN.replace(day=5), 250)
        version = self.approve(self.choose(workspace))
        frozen = deepcopy(version.snapshot)
        fact.actual_quantity = D(280)
        fact.save()
        version.refresh_from_db()
        self.assertEqual(version.snapshot, frozen)
        revision = GlobalPlanService.revision(version, self.planner)
        revision = WorkspaceService.refresh(revision, self.planner)
        self.assertEqual(self.amounts(revision, self.simple), [D(280), D(220), D(500)])
        self.assertEqual(revision.previous_version_id, version.pk)
        self.assertEqual(comparison(version)["works"][0]["fact"], D(280))

    def test_no_base_future_weights_requires_exact_manual_distribution(self):
        workspace = self.create()
        workspace.baseline_version.work_allocations.filter(month=JAN).update(
            quantity=1000
        )
        workspace.baseline_version.work_allocations.filter(month__in=[FEB, MAR]).update(
            quantity=0
        )
        self.approve(workspace.baseline_version)
        self.fact(self.simple, None, JAN.replace(day=5), 250)
        version = self.choose(workspace)
        with self.assertRaisesMessage(ValidationError, "Распределите вручную"):
            WorkspaceService.refresh(version, self.planner)
        row = WorkMonthAllocation.objects.create(
            company=self.company,
            version=version,
            work=self.simple,
            month=MAR,
            quantity=500,
        )
        with self.assertRaises(ValidationError):
            WorkspaceService.refresh(version, self.planner)
        row.quantity = 530
        row.save()
        version = WorkspaceService.refresh(version, self.planner)
        self.assertEqual(self.amounts(version, self.simple), [D(250), D(220), D(530)])

    def test_negative_remainder_becomes_zero_and_warns(self):
        workspace = self.create()
        self.approve(workspace.baseline_version)
        self.fact(self.simple, None, JAN.replace(day=5), 900)
        version = WorkspaceService.refresh(
            self.choose(workspace, qty=220), self.planner
        )
        self.assertEqual(self.amounts(version, self.simple), [D(900), D(220), D(0)])
        self.assertTrue(version.snapshot["warnings"])

    def test_remaining_months_keep_relative_base_proportions_and_exact_total(self):
        workspace = self.create()
        self.approve(workspace.baseline_version)
        version = WorkspaceService.forecast(workspace, self.planner, JAN, "REMAINING")
        row = version.work_allocations.get()
        row.quantity = 100
        row.save()
        version = WorkspaceService.refresh(version, self.planner)
        values = self.amounts(version, self.simple)
        self.assertEqual(values, [D(100), D("257.142"), D("642.858")])
        self.assertEqual(sum(values), 1000)

    def add_resources(self, workspace):
        for month, qty in [(JAN, 300), (FEB, 200), (MAR, 500)]:
            for kind, fields in [
                ("labor", {"brigade": self.brigade, "hours": qty, "count": 10}),
                (
                    "equipment",
                    {
                        "equipment_type": self.equipment,
                        "equipment_number": "A1",
                        "hours": qty,
                        "count": 2,
                    },
                ),
                ("fuel", {"fuel_type": "DIESEL", "equipment_ref": "A1", "liters": qty}),
            ]:
                ResourceMonthAllocation.objects.create(
                    company=self.company,
                    version=workspace.baseline_version,
                    month=month,
                    kind=kind,
                    rate=100,
                    **fields,
                )

    def test_resources_are_reforecast_in_hours_and_liters_not_headcount(self):
        workspace = self.create()
        self.add_resources(workspace)
        self.approve(workspace.baseline_version)
        LaborFact.objects.create(
            company=self.company,
            construction_object=self.obj,
            brigade=self.brigade,
            date=JAN.replace(day=5),
            actual_workers=3,
            actual_hours=250,
            hourly_rate=100,
        )
        EquipmentFact.objects.create(
            company=self.company,
            construction_object=self.obj,
            equipment_type=self.equipment,
            equipment_number="A1",
            date=JAN.replace(day=5),
            actual_count=1,
            machine_hours=250,
            hourly_rate=100,
        )
        FuelFact.objects.create(
            company=self.company,
            construction_object=self.obj,
            fuel_type="DIESEL",
            equipment_ref="A1",
            date=JAN.replace(day=5),
            actual_liters=250,
            price_per_liter=100,
        )
        version = self.choose(workspace)
        for row in version.resource_allocations.all():
            if row.kind == "fuel":
                row.liters = 220
            else:
                row.hours = 220
                row.count = 4
            row.save()
        version = WorkspaceService.refresh(version, self.planner)
        for kind, field in [
            ("labor", "planned_hours"),
            ("equipment", "planned_machine_hours"),
            ("fuel", "planned_liters"),
        ]:
            rows = version.snapshot["resources"][kind]
            self.assertEqual(
                [
                    sum(
                        D(r[field])
                        for r in rows
                        if r["date"][:7] == month.isoformat()[:7]
                    )
                    for month in [JAN, FEB, MAR]
                ],
                [D(250), D(220), D(530)],
            )
        future = [
            r
            for r in version.snapshot["resources"]["labor"]
            if r["date"][:7] == "2026-03"
        ]
        self.assertEqual([r["planned_workers"] for r in future], [10] * 31)

    def test_resources_in_scenario_two_preserve_other_months_exactly(self):
        workspace = self.create()
        self.add_resources(workspace)
        base = self.approve(workspace.baseline_version)
        version = WorkspaceService.refresh(
            self.choose(workspace, "BASELINE"), self.planner
        )
        for kind in ["labor", "equipment", "fuel"]:
            self.assertEqual(
                [
                    r
                    for r in version.snapshot["resources"][kind]
                    if r["date"][:7] != "2026-02"
                ],
                [
                    r
                    for r in base.snapshot["resources"][kind]
                    if r["date"][:7] != "2026-02"
                ],
            )

    def test_partial_month_uses_only_days_inside_period(self):
        workspace = WorkspaceService.create(
            self.planner, self.obj, "Partial", JAN.replace(day=6), JAN.replace(day=20)
        )
        WorkMonthAllocation.objects.create(
            company=self.company,
            version=workspace.baseline_version,
            work=self.simple,
            month=JAN,
            quantity=10,
        )
        version = WorkspaceService.refresh(workspace.baseline_version, self.planner)
        self.assertEqual(
            [r["date"] for r in version.snapshot["works"][0]["plans"]], ["2026-01-06"]
        )
        self.assertEqual(
            months_between(date(2025, 12, 25), date(2026, 2, 3)),
            [date(2025, 12, 1), JAN, FEB],
        )

    def test_load_profile_is_used_and_base_profile_is_frozen(self):
        profile = LoadProfile.objects.create(
            company=self.company, code="quarter", name="25/75"
        )
        for day, percentage in [(1, 25), (2, 75)]:
            LoadProfileItem.objects.create(
                company=self.company,
                profile=profile,
                workday_number=day,
                percentage=percentage,
            )
        self.simple.load_profile = profile
        self.simple.save()
        workspace = self.create()
        base = self.approve(workspace.baseline_version)
        rows = [
            r for r in base.snapshot["works"][0]["plans"] if r["date"][:7] == "2026-01"
        ]
        self.assertEqual([D(r["quantity"]) for r in rows], [75, 225])
        profile.items.update(percentage=50)
        version = WorkspaceService.refresh(
            self.choose(workspace, "BASELINE"), self.planner
        )
        feb = [
            r
            for r in version.snapshot["works"][0]["plans"]
            if r["date"][:7] == "2026-02"
        ]
        self.assertEqual([D(r["quantity"]) for r in feb], [55, 165])

    def test_approved_allocations_and_work_norms_are_locked(self):
        workspace = self.create([self.composite])
        self.approve(workspace.baseline_version)
        row = workspace.baseline_version.work_allocations.first()
        row.quantity = 99
        with self.assertRaises(ValidationError):
            row.save()
        with self.assertRaises(ValidationError), transaction.atomic():
            row.delete()
        self.a.quantity_per_unit = D(4)
        with self.assertRaises(ValidationError):
            self.a.clean()
        self.composite.allow_fractional = False
        with self.assertRaises(ValidationError):
            self.composite.clean()

    def test_only_ceo_can_approve_and_month_must_be_in_period(self):
        workspace = self.create()
        version = GlobalPlanService.transition(
            workspace.baseline_version, self.planner, "submit"
        )
        with self.assertRaises(PermissionDenied):
            GlobalPlanService.transition(version, self.planner, "approve")
        GlobalPlanService.transition(departments(version, self.approvers), self.approvers["CEO"], "approve")
        with self.assertRaises(ValidationError):
            WorkspaceService.forecast(
                workspace, self.planner, date(2026, 4, 1), "REMAINING"
            )
        with self.assertRaises(ValidationError):
            WorkspaceService.forecast(
                workspace, self.planner, FEB.replace(day=2), "REMAINING"
            )

    def test_editor_and_formset_ids_are_company_scoped(self):
        workspace = self.create()
        other = Company.objects.create(name="other")
        u = User.objects.create_user(username="other-period", company=other, role=Role.objects.get(code="ADMIN"))
        self.client.force_login(u)
        for name, pk in [
            ("planning:workspace_detail", workspace.pk),
            ("planning:workspace_edit", workspace.baseline_version_id),
        ]:
            self.assertEqual(self.client.get(reverse(name, args=[pk])).status_code, 404)
        self.assertEqual(
            self.client.post(
                reverse("planning:workspace_forecast", args=[workspace.pk]),
                {"month": FEB, "scenario": "REMAINING"},
            ).status_code,
            404,
        )
        self.client.force_login(self.planner)
        response = self.client.get(
            reverse("planning:workspace_edit", args=[workspace.baseline_version_id])
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['composite_items'][str(self.composite.pk)], [
            {'id': self.a.pk, 'name': 'A', 'unit': 'm', 'norm': '2.000'},
            {'id': self.b.pk, 'name': 'B', 'unit': 'm3', 'norm': '3.000'},
        ])
        self.assertNotIn(str(self.simple.pk), response.context['composite_items'])
        form = response.context["work_forms"].forms[0]
        self.assertTrue(
            all(
                r.version_id == workspace.baseline_version_id
                for r in form.fields["id"].queryset
            )
        )
        for name in ["planning:workspace_list", "planning:workspace_create"]:
            self.assertEqual(self.client.get(reverse(name)).status_code, 200)

    def test_monthly_input_formset_saves_and_previews_through_ui(self):
        workspace = self.create()
        version = workspace.baseline_version
        self.client.force_login(self.planner)
        url = (
            reverse("planning:workspace_edit", args=[version.pk]) + "?month=2026-02-01"
        )
        row = version.work_allocations.get(month=FEB)
        data = {
            "works-TOTAL_FORMS": 1,
            "works-INITIAL_FORMS": 1,
            "works-MIN_NUM_FORMS": 0,
            "works-MAX_NUM_FORMS": 1000,
            "works-0-id": row.pk,
            "works-0-work": self.simple.pk,
            "works-0-month": FEB.isoformat(),
            "works-0-quantity": 240,
            "works-0-load_profile": "",
            "resources-TOTAL_FORMS": 0,
            "resources-INITIAL_FORMS": 0,
            "resources-MIN_NUM_FORMS": 0,
            "resources-MAX_NUM_FORMS": 1000,
            "action": "preview",
        }
        response = self.client.post(url, data)
        self.assertEqual(response.status_code, 302)
        row.refresh_from_db()
        version.refresh_from_db()
        self.assertEqual(row.quantity, 240)
        self.assertEqual(self.amounts(version, self.simple), [D(300), D(240), D(500)])
        response = self.client.get(reverse("planning:global_detail", args=[version.pk]))
        self.assertContains(response, "План на весь период по месяцам")

    def test_ui_creation_and_forecast_start_with_editable_month(self):
        self.client.force_login(self.planner)
        response = self.client.post(
            reverse("planning:workspace_create"),
            {
                "name": "UI plan",
                "construction_object": self.obj.pk,
                "start_date": JAN,
                "end_date": date(2026, 3, 31),
            },
        )
        self.assertEqual(response.status_code, 302)
        workspace = PlanningWorkspace.objects.get(name="UI plan")
        response = self.client.post(
            reverse(
                "planning:workspace_add_work", args=[workspace.baseline_version_id]
            ),
            {"work": self.simple.pk},
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(workspace.baseline_version.work_allocations.count(), 3)
        workspace.baseline_version.work_allocations.update(quantity=100)
        self.approve(workspace.baseline_version)
        response = self.client.post(
            reverse("planning:workspace_forecast", args=[workspace.pk]),
            {"month": FEB, "scenario": "BASELINE"},
        )
        self.assertEqual(response.status_code, 302)
        version = workspace.versions.get(version_kind="FORECAST")
        self.assertEqual(version.planning_month, FEB)
        self.assertEqual(self.client.get(response.url).status_code, 200)

    def test_current_work_plan_and_resource_fact_entry_use_approved_workspace(self):
        from apps.works.progress import WorkProgressService

        workspace = self.create()
        self.add_resources(workspace)
        base = self.approve(workspace.baseline_version)
        self.assertEqual(
            sum(v["daily"] for v in WorkProgressService.planned(self.simple).values()),
            1000,
        )
        self.fact(self.simple, None, JAN.replace(day=5), 250)
        forecast = self.approve(self.choose(workspace))
        planned = WorkProgressService.planned(self.simple)
        self.assertEqual(
            sum(v["daily"] for day, v in planned.items() if day.month == 1), 250
        )
        foreman = User.objects.create_user(username="workspace-foreman", company=self.company, role=Role.objects.get(code="FOREMAN"))
        foreman.assigned_objects.add(self.obj)
        self.client.force_login(foreman)
        url = reverse("production:labor_fact_daily_input")
        response = self.client.get(
            url, {"construction_object": self.obj.pk, "date": FEB.replace(day=5)}
        )
        self.assertEqual(response.status_code, 200)
        plans = list(response.context["plans"])
        self.assertEqual(len(plans), 1)
        token = str(plans[0].pk)
        self.assertTrue(token.startswith(f"ws:{forecast.pk}:"))
        response = self.client.post(
            url,
            {
                "construction_object": self.obj.pk,
                "date": FEB.replace(day=5),
                "plan_ids": [token],
                "value_" + token: 7,
                "hours_" + token: 55,
            },
        )
        self.assertEqual(response.status_code, 302)
        fact = LaborFact.objects.get(
            construction_object=self.obj, date=FEB.replace(day=5)
        )
        self.assertEqual(fact.actual_workers, 7)
        self.assertEqual(fact.actual_hours, 55)

    def test_resources_ignore_profiles_and_include_every_calendar_day(self):
        workspace = self.create()
        profile = LoadProfile.objects.create(
            company=self.company, code="resource-profile", name="30/70"
        )
        for day, percentage in [(1, 30), (2, 70)]:
            LoadProfileItem.objects.create(
                company=self.company,
                profile=profile,
                workday_number=day,
                percentage=percentage,
            )
        ResourceMonthAllocation.objects.create(
            company=self.company,
            version=workspace.baseline_version,
            month=JAN,
            kind="labor",
            brigade=self.brigade,
            count=5,
            hours=80,
            rate=100,
            load_profile=profile,
        )
        version = WorkspaceService.refresh(workspace.baseline_version, self.planner)
        rows = version.snapshot["resources"]["labor"]
        self.assertEqual(len(rows), 31)
        self.assertEqual(rows[0]["date"], "2026-01-01")
        self.assertEqual(rows[-1]["date"], "2026-01-31")
        self.assertTrue(all(r["planned_workers"] == 5 for r in rows))
        self.assertEqual(sum(D(r["planned_hours"]) for r in rows), D(80))
        self.assertLessEqual(max(D(r["planned_hours"]) for r in rows) - min(D(r["planned_hours"]) for r in rows), D(".31"))

    def test_resource_daily_counts_and_fuel_include_weekends_without_calendar(self):
        workspace = self.create()
        self.add_resources(workspace)
        workspace.baseline_version.work_allocations.all().delete()
        CalendarDay.objects.all().delete()
        ProductionCalendar.objects.all().delete()
        version = WorkspaceService.refresh(workspace.baseline_version, self.planner)
        for kind, count_field, count in [("labor", "planned_workers", 10), ("equipment", "planned_count", 2)]:
            rows = [r for r in version.snapshot["resources"][kind] if r["date"].startswith("2026-01")]
            self.assertEqual(len(rows), 31)
            self.assertTrue(all(r[count_field] == count for r in rows))
        fuel = [r for r in version.snapshot["resources"]["fuel"] if r["date"].startswith("2026-02")]
        self.assertEqual(len(fuel), 28)
        self.assertEqual(sum(D(r["planned_liters"]) for r in fuel), D(200))
        self.assertTrue(any(r["date"] == "2026-02-01" for r in fuel))

    def test_fuel_balance_repeats_daily_and_expense_preserves_monthly_total(self):
        workspace = self.create()
        row = ResourceMonthAllocation.objects.create(company=self.company, version=workspace.baseline_version, month=JAN, kind="fuel", fuel_type="DIESEL", balance=D("700"), liters=D("3100"))
        version = WorkspaceService.refresh(workspace.baseline_version, self.planner)
        rows = version.snapshot["resources"]["fuel"]
        self.assertEqual(len(rows), 31)
        self.assertTrue(all(D(r["planned_balance"]) == D(700) for r in rows))
        self.assertTrue(all(D(r["planned_liters"]) == D(100) for r in rows))
        self.approve(version)
        FuelFact.objects.create(company=self.company, construction_object=self.obj, date=JAN, fuel_type="DIESEL", actual_balance=650, actual_liters=90)
        fuel_comparison = next(r for r in comparison(version)["resources"] if r["kind"] == "fuel" and r["date"] == JAN.isoformat())
        self.assertEqual(fuel_comparison["planned_balance"], D(700))
        self.assertEqual(fuel_comparison["actual_balance"], D(650))
        self.assertEqual(fuel_comparison["fact"], D(90))
        forecast = WorkspaceService.forecast(workspace, self.planner, JAN, "BASELINE")
        self.assertEqual(forecast.resource_allocations.get(kind="fuel").balance, D(700))
        row.balance = -1
        with self.assertRaises(ValidationError):
            row.full_clean()

    def test_daily_fuel_fact_entry_accepts_balance_and_expense_from_virtual_plan(self):
        workspace = self.create()
        ResourceMonthAllocation.objects.create(company=self.company, version=workspace.baseline_version, month=JAN, kind="fuel", fuel_type="DIESEL", balance=700, liters=3100)
        self.approve(workspace.baseline_version)
        foreman = User.objects.create_user(username="fuel-balance-foreman", company=self.company, role=Role.objects.get(code="FOREMAN"))
        foreman.assigned_objects.add(self.obj)
        self.client.force_login(foreman)
        url = reverse("production:fuel_fact_daily_input")
        response = self.client.get(url, {"construction_object": self.obj.pk, "date": JAN})
        self.assertEqual(response.status_code, 200)
        plan = list(response.context["plans"])[0]
        self.assertEqual(D(plan.planned_balance), D(700))
        token = str(plan.pk)
        self.assertContains(response, "balance_" + token)
        response = self.client.post(url, {"construction_object": self.obj.pk, "date": JAN, "plan_ids": [token], "balance_" + token: 650, "value_" + token: 90})
        self.assertEqual(response.status_code, 302)
        fact = FuelFact.objects.get()
        self.assertEqual(fact.actual_balance, D(650))
        self.assertEqual(fact.actual_liters, D(90))

    def test_fuel_zero_expense_still_has_daily_balance(self):
        workspace = self.create()
        ResourceMonthAllocation.objects.create(company=self.company, version=workspace.baseline_version, month=JAN, kind="fuel", fuel_type="DIESEL", balance=50, liters=0)
        version = WorkspaceService.refresh(workspace.baseline_version, self.planner)
        self.assertEqual(len(version.snapshot["resources"]["fuel"]), 31)
        self.assertTrue(all(D(r["planned_balance"]) == D(50) and D(r["planned_liters"]) == 0 for r in version.snapshot["resources"]["fuel"]))

    def test_resource_distribution_stays_inside_partial_period(self):
        from .workspace_services import resource_rows
        workspace = self.create()
        workspace.start_date = date(2026, 1, 10)
        workspace.end_date = date(2026, 1, 12)
        source = {"kind": "fuel", "liters": "30", "count": 0, "fuel_type": "DIESEL", "equipment_ref": "", "label": "Diesel", "rate": "0", "profile": ["100", "0"]}
        rows = resource_rows(workspace, source, JAN)
        self.assertEqual([r["date"] for r in rows], ["2026-01-10", "2026-01-11", "2026-01-12"])
        self.assertEqual([D(r["planned_liters"]) for r in rows], [D(10)] * 3)

    def test_unplanned_past_work_and_resource_facts_are_included_in_scenario_one(self):
        workspace = self.create()
        self.approve(workspace.baseline_version)
        self.fact(self.composite, self.a, JAN.replace(day=5), 2)
        self.fact(self.composite, self.b, JAN.replace(day=6), 3)
        FuelFact.objects.create(
            company=self.company,
            construction_object=self.obj,
            fuel_type="DIESEL",
            date=JAN.replace(day=5),
            actual_liters=40,
        )
        version = WorkspaceService.refresh(self.choose(workspace), self.planner)
        self.assertEqual(self.amounts(version, self.composite), [D(1), D(0), D(0)])
        self.assertEqual(
            sum(D(r["planned_liters"]) for r in version.snapshot["resources"]["fuel"]),
            40,
        )
        self.assertTrue(version.snapshot["warnings"])

    def test_locked_workspace_cannot_change_object_or_period(self):
        workspace = self.create()
        self.approve(workspace.baseline_version)
        workspace.end_date = date(2026, 4, 30)
        with self.assertRaises(ValidationError):
            workspace.save()
        self.assertEqual(
            PlanningWorkspace.objects.get(pk=workspace.pk).end_date, date(2026, 3, 31)
        )

    def test_last_month_with_remaining_quantity_requires_a_complete_plan(self):
        workspace = self.create()
        self.approve(workspace.baseline_version)
        self.fact(self.simple, None, JAN.replace(day=5), 250)
        version = WorkspaceService.forecast(workspace, self.planner, MAR, "REMAINING")
        with self.assertRaisesMessage(ValidationError, "не помещается в период"):
            WorkspaceService.refresh(version, self.planner)
        row = version.work_allocations.get()
        row.quantity = 750
        row.save()
        version = WorkspaceService.refresh(version, self.planner)
        self.assertEqual(self.amounts(version, self.simple), [D(250), D(0), D(750)])

    def test_forecast_rejects_forged_virtual_resource_plan_from_other_company(self):
        workspace = self.create()
        self.add_resources(workspace)
        base = self.approve(workspace.baseline_version)
        other = Company.objects.create(name="Foreign virtual")
        user = User.objects.create_user(username="foreign-virtual", company=other, role=Role.objects.get(code="ADMIN"))
        p = Project.objects.create(company=other, code="p", name="p")
        obj = ConstructionObject.objects.create(
            company=other, project=p, code="o", name="o"
        )
        self.client.force_login(user)
        response = self.client.post(
            reverse("production:labor_fact_daily_input"),
            {
                "construction_object": obj.pk,
                "date": JAN.replace(day=5),
                "plan_ids": [f"ws:{base.pk}:0"],
                f"value_ws:{base.pk}:0": 10,
            },
        )
        self.assertEqual(response.status_code, 404)
        self.assertFalse(LaborFact.objects.filter(company=other).exists())

    def test_forecast_resource_zero_future_weights_requires_manual_hours(self):
        workspace = self.create()
        ResourceMonthAllocation.objects.create(
            company=self.company,
            version=workspace.baseline_version,
            month=JAN,
            kind="labor",
            brigade=self.brigade,
            count=2,
            hours=100,
            rate=100,
        )
        self.approve(workspace.baseline_version)
        version = self.choose(workspace, "REMAINING", qty=1000)
        with self.assertRaisesMessage(ValidationError, "Распределите вручную"):
            WorkspaceService.refresh(version, self.planner)
        ResourceMonthAllocation.objects.create(
            company=self.company,
            version=version,
            month=MAR,
            kind="labor",
            brigade=self.brigade,
            count=2,
            hours=100,
            rate=100,
        )
        version = WorkspaceService.refresh(version, self.planner)
        self.assertEqual(
            sum(D(r["planned_hours"]) for r in version.snapshot["resources"]["labor"]),
            100,
        )

    def test_period_export_uses_frozen_monthly_plan_and_rejects_foreign_user(self):
        workspace = self.create()
        base = self.approve(workspace.baseline_version)
        self.client.force_login(self.planner)
        url = reverse("planning:workspace_export", args=[base.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "text/csv; charset=utf-8")
        text = response.content.decode("utf-8-sig")
        self.assertIn("01.2026;300", text)
        self.assertIn("03.2026;500", text)
        other = Company.objects.create(name="Export other")
        user = User.objects.create_user(username="export-other", company=other, role=Role.objects.get(code="ADMIN"))
        self.client.force_login(user)
        self.assertEqual(self.client.get(url).status_code, 404)

    def test_remainder_uses_frozen_month_specific_base_profile(self):
        workspace = self.create()
        profile = LoadProfile.objects.create(
            company=self.company, code="march-specific", name="March 25/75"
        )
        for day, percentage in [(1, 25), (2, 75)]:
            LoadProfileItem.objects.create(
                company=self.company,
                profile=profile,
                workday_number=day,
                percentage=percentage,
            )
        row = workspace.baseline_version.work_allocations.get(month=MAR)
        row.load_profile = profile
        row.save()
        self.approve(workspace.baseline_version)
        profile.items.update(percentage=50)
        self.fact(self.simple, None, JAN.replace(day=5), 250)
        version = WorkspaceService.refresh(self.choose(workspace), self.planner)
        future = [
            r
            for r in version.snapshot["works"][0]["plans"]
            if r["date"][:7] == "2026-03"
        ]
        self.assertEqual([D(r["quantity"]) for r in future], [D("132.5"), D("397.5")])

    def test_editor_amount_prices_follow_selected_month(self):
        from apps.works.models import WorkPrice

        workspace = self.create()
        for day, price in [(JAN, 15), (FEB, 25)]:
            WorkPrice.objects.create(
                company=self.company, work=self.simple, price=price,
                effective_from=day, created_by=self.planner,
            )
        self.client.force_login(self.planner)
        url = reverse("planning:workspace_edit", args=[workspace.baseline_version_id])
        january = self.client.get(url, {"month": JAN.isoformat()})
        february = self.client.get(url, {"month": FEB.isoformat()})
        self.assertEqual(Decimal(january.context["work_prices"][str(self.simple.pk)]), Decimal(15))
        self.assertEqual(Decimal(february.context["work_prices"][str(self.simple.pk)]), Decimal(25))
        self.assertContains(january, 'id="work-prices"')
        self.assertContains(january, 'planning/work_amounts.js')

    def test_editor_catalogue_queries_do_not_grow_per_resource_row(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext
        from .workspace_forms import ResourceAllocationSet
        workspace = self.create()
        version = workspace.baseline_version
        allocations = []
        for index in range(12):
            allocations.append(ResourceMonthAllocation.objects.create(
                company=self.company, version=version, month=JAN, kind="equipment",
                equipment_type=self.equipment, equipment_number=str(index), count=1,
            ))
        def render_queries(ids):
            with CaptureQueriesContext(connection) as captured:
                formset = ResourceAllocationSet(
                    prefix="resources", queryset=version.resource_allocations.filter(pk__in=ids),
                    form_kwargs={"user": self.planner, "version": version, "month": JAN},
                )
                for form in formset:
                    str(form["brigade"])
                    str(form["equipment_type"])
                str(formset.empty_form["equipment_type"])
            return len(captured)
        one = render_queries([allocations[0].pk])
        many = render_queries([row.pk for row in allocations])
        self.assertLessEqual(many, one + 1)

    def test_large_monthly_editor_saves_more_than_one_thousand_fields(self):
        workspace = self.create()
        version = workspace.baseline_version
        ResourceMonthAllocation.objects.bulk_create([
            ResourceMonthAllocation(company=self.company,version=version,month=JAN,kind='equipment',
                equipment_type=self.equipment,equipment_number=str(index),count=1)
            for index in range(100)
        ])
        self.client.force_login(self.planner)
        url=reverse('planning:workspace_edit',args=[version.pk])+'?month=2026-01-01'
        response=self.client.get(url)
        data={'action':'save'}
        for name in ['work_forms','resource_forms']:
            formset=response.context[name]
            for form in [formset.management_form,*formset.forms]:
                for field in form:
                    value=field.value()
                    data[field.html_name]=value if value is not None else ''
        self.assertGreater(len(data),1000)
        resource=response.context['resource_forms'].forms[0]
        data[resource['count'].html_name]='7'
        response=self.client.post(url,data)
        self.assertEqual(response.status_code,302)
        saved=ResourceMonthAllocation.objects.get(pk=resource.instance.pk)
        self.assertEqual(saved.count,7)
        self.assertEqual(version.resource_allocations.count(),100)
