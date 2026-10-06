from datetime import date, timedelta
from decimal import Decimal
from copy import deepcopy
from django.core.exceptions import ValidationError, PermissionDenied
from django.test import TestCase
from django.db import transaction
from django.urls import reverse
from apps.accounts.models import Company, User, Role
from apps.projects.models import Project, ConstructionObject, Section
from apps.works.models import ProjectWork, ProjectWorkItem
from apps.works.progress import WorkProgressService
from apps.resources.models import Brigade, EquipmentType
from apps.production.models import (
    DailyFact,
    LaborPlan,
    LaborFact,
    EquipmentPlan,
    EquipmentFact,
    FuelPlan,
    FuelFact,
    LegacyResourceRecord,
)
from apps.production.forms import (
    LaborPlanForm,
    LaborFactForm,
    EquipmentPlanForm,
    EquipmentFactForm,
    FuelPlanForm,
    FuelFactForm,
    DailyFactForm,
)
from .models import (
    MonthlyPlan,
    PlanVersion,
    DailyPlan,
    ProductionCalendar,
    CalendarDay,
    GlobalPlanVersion,
)
from .services import PlanGeneratorService
from .global_services import GlobalPlanService, comparison

D = Decimal


class GlobalPlanningTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.company = Company.objects.create(name="Planning")
        manager_role = Role.objects.create(code="MANAGER", name="Manager")
        cls.manager = User.objects.create_user(
            username="global-manager", company=cls.company, role=manager_role
        )
        cls.worker = User.objects.create_user(
            username="global-worker", company=cls.company
        )
        project = Project.objects.create(company=cls.company, code="p", name="Project")
        cls.obj = ConstructionObject.objects.create(
            company=cls.company, project=project, code="o", name="Object"
        )
        cls.section = Section.objects.create(
            company=cls.company, construction_object=cls.obj, code="s", name="Section"
        )
        cls.work = ProjectWork.objects.create(
            company=cls.company,
            section=cls.section,
            code="w",
            name="Composite",
            unit="шт",
            unit_price=100,
            kind="COMPOSITE",
        )
        cls.a = ProjectWorkItem.objects.create(
            company=cls.company,
            project_work=cls.work,
            name="A",
            unit="м",
            quantity_per_unit=2,
            weight=50,
        )
        cls.b = ProjectWorkItem.objects.create(
            company=cls.company,
            project_work=cls.work,
            name="B",
            unit="м³",
            quantity_per_unit=3,
            weight=50,
        )
        cls.simple = ProjectWork.objects.create(
            company=cls.company,
            section=cls.section,
            code="simple",
            name="Simple",
            unit="м",
            unit_price=10,
        )
        cls.brigade = Brigade.objects.create(
            company=cls.company, code="b", name="Brigade"
        )
        cls.equipment = EquipmentType.objects.create(
            company=cls.company, name="Excavator"
        )
        cls.start = date(2026, 1, 31)
        cls.end = date(2026, 2, 2)
        calendar = ProductionCalendar.objects.create(
            company=cls.company, code="c", name="Calendar", year=2026, is_default=True
        )
        for day in [cls.start, cls.start + timedelta(days=1), cls.end]:
            CalendarDay.objects.create(
                company=cls.company, calendar=calendar, date=day, is_working=True
            )

    def fact(self, work, item, day, qty):
        return DailyFact.objects.create(
            company=self.company,
            project_work=work,
            work_item=item,
            date=day,
            actual_quantity=D(str(qty)),
        )

    def approved(self, work, start, end, rows, number=1):
        monthly = MonthlyPlan.objects.filter(
            project_work=work, start_date=start
        ).first()
        if not monthly:
            monthly = MonthlyPlan.objects.create(
                company=self.company,
                project_work=work,
                year=start.year,
                month=start.month,
                start_date=start,
                end_date=end,
                planned_quantity=1,
            )
        version = PlanVersion.objects.create(
            company=self.company,
            monthly_plan=monthly,
            version_number=number,
            status="APPROVED",
        )
        for i, (day, item, qty) in enumerate(rows):
            DailyPlan.objects.create(
                company=self.company,
                plan_version=version,
                date=day,
                work_item=item,
                planned_quantity=D(str(qty)),
                planned_value=0,
                workday_number=i + 1,
            )
        return version

    def seed_staggered(self):
        self.fact(self.work, self.a, self.start, 3)  # 1.5 units A, no B
        self.fact(self.work, self.b, self.start + timedelta(days=1), 2.4)  # .8 complete
        self.fact(self.work, self.b, self.end, 2.1)  # 1.5 complete

    def test_fractional_daily_completion_is_cumulative_across_months(self):
        self.seed_staggered()
        series = WorkProgressService.facts(self.work)
        self.assertEqual(
            [v["daily"] for v in series.values()], [D("0"), D(".8"), D(".7")]
        )
        self.assertEqual(WorkProgressService.completed(self.work), D("1.5"))

    def test_whole_units_floor_cumulative_before_taking_daily_difference(self):
        self.work.allow_fractional = False
        self.work.save()
        self.seed_staggered()
        series = WorkProgressService.facts(self.work)
        self.assertEqual(
            [v["daily"] for v in series.values()], [D("0"), D("0"), D("1")]
        )
        self.assertEqual(WorkProgressService.completed(self.work), 1)

    def test_backdated_edit_recomputes_later_dates(self):
        self.seed_staggered()
        fact = self.work.daily_facts.get(
            work_item=self.b, date=self.start + timedelta(days=1)
        )
        fact.actual_quantity = D("3")
        fact.save()
        series = WorkProgressService.facts(self.work)
        self.assertEqual(series[self.start + timedelta(days=1)]["daily"], D("1"))
        self.assertEqual(series[self.end]["daily"], D(".5"))

    def test_missing_subwork_limits_main_completion_to_zero(self):
        self.fact(self.work, self.a, self.start, 100)
        self.assertEqual(WorkProgressService.completed(self.work), 0)

    def test_simple_work_has_direct_plan_and_fact(self):
        monthly = MonthlyPlan.objects.create(
            company=self.company,
            project_work=self.simple,
            year=2026,
            month=1,
            start_date=self.start,
            end_date=self.end,
            planned_quantity=D("1.001"),
        )
        version = PlanVersion.objects.create(
            company=self.company, monthly_plan=monthly, version_number=1
        )
        self.assertEqual(PlanGeneratorService.generate(version), 3)
        self.assertFalse(version.daily_plans.exclude(work_item=None).exists())
        self.assertEqual(
            sum(r.planned_quantity for r in version.daily_plans.all()), D("1.001")
        )
        self.assertEqual(
            sum(r.planned_value for r in version.daily_plans.all()), D("10.01")
        )
        self.fact(self.simple, None, self.start, D(".3"))
        self.fact(self.simple, None, self.end, D(".7"))
        self.assertEqual(WorkProgressService.completed(self.simple), 1)

    def test_fact_form_requires_item_only_for_composite(self):
        common = {"date": self.start, "actual_quantity": 1}
        for work, item, valid in [
            (self.simple, None, True),
            (self.simple, self.a.pk, False),
            (self.work, None, False),
            (self.work, self.a.pk, True),
        ]:
            form = DailyFactForm(
                {**common, "project_work": work.pk, "work_item": item or ""},
                user=self.worker,
            )
            self.assertEqual(form.is_valid(), valid, form.errors)

    def test_resource_forms_require_object_and_remove_work_links(self):
        for cls in [
            LaborPlanForm,
            LaborFactForm,
            EquipmentPlanForm,
            EquipmentFactForm,
            FuelPlanForm,
            FuelFactForm,
        ]:
            form = cls(user=self.worker)
            self.assertTrue(form.fields["construction_object"].required)
            self.assertFalse(
                {"project", "project_work", "plan_version"} & set(form.fields)
            )

    def test_global_comparison_includes_fact_before_period(self):
        self.seed_staggered()
        self.approved(self.work, self.start, self.start, [(self.start, self.a, 3)])
        version = self.approved(
            self.work,
            self.start + timedelta(days=1),
            self.end,
            [(self.start + timedelta(days=1), self.b, 2.4), (self.end, self.b, 2.1)],
        )
        global_version = GlobalPlanService.create(
            self.worker, self.obj, self.start + timedelta(days=1), self.end
        )
        row = comparison(global_version)["works"][0]
        self.assertEqual(row["plan"], D("1.5"))
        self.assertEqual(row["fact"], D("1.5"))
        self.assertEqual([d["plan"] for d in row["days"]], [D(".8"), D(".7")])
        self.assertEqual(len(global_version.snapshot["works"][0]["versions"]), 2)

    def resource_plans(self):
        LaborPlan.objects.create(
            company=self.company,
            construction_object=self.obj,
            date=self.start,
            brigade=self.brigade,
            planned_workers=2,
            planned_hours=16,
            hourly_rate=100,
        )
        EquipmentPlan.objects.create(
            company=self.company,
            construction_object=self.obj,
            date=self.start,
            equipment_type=self.equipment,
            planned_count=1,
            planned_machine_hours=8,
            hourly_rate=200,
        )
        FuelPlan.objects.create(
            company=self.company,
            construction_object=self.obj,
            date=self.start,
            planned_liters=40,
            price_per_liter=50,
        )

    def test_global_has_separate_approval_and_frozen_all_resource_plans(self):
        self.resource_plans()
        version = GlobalPlanService.create(self.worker, self.obj, self.start, self.end)
        self.assertEqual(version.status, "DRAFT")
        # Edits before submitting are collected into the snapshot.
        LaborPlan.objects.filter(company=self.company).update(planned_workers=3)
        version = GlobalPlanService.transition(version, self.worker, "submit")
        frozen = deepcopy(version.snapshot)
        self.assertEqual(frozen["resources"]["labor"][0]["planned_workers"], 3)
        LaborPlan.objects.filter(company=self.company).update(planned_workers=10)
        with self.assertRaises(PermissionDenied):
            GlobalPlanService.transition(version, self.worker, "approve")
        version = GlobalPlanService.transition(version, self.manager, "approve")
        self.assertEqual(version.snapshot, frozen)
        revision = GlobalPlanService.revision(version, self.worker)
        self.assertEqual(
            revision.snapshot["resources"]["labor"][0]["planned_workers"], 10
        )
        self.assertEqual(revision.previous_version_id, version.pk)
        version.refresh_from_db()
        self.assertEqual(version.snapshot, frozen)

    def test_global_freezes_work_specification_and_latest_approved_lower_version(self):
        old = self.approved(
            self.work,
            self.start,
            self.end,
            [(self.start, self.a, 2), (self.end, self.b, 3)],
        )
        latest = self.approved(
            self.work,
            self.start,
            self.end,
            [(self.start, self.a, 4), (self.end, self.b, 6)],
            number=2,
        )
        PlanVersion.objects.create(
            company=self.company, monthly_plan=latest.monthly_plan, version_number=3
        )
        version = GlobalPlanService.create(self.worker, self.obj, self.start, self.end)
        self.assertEqual(
            list(version.source_versions.values_list("pk", flat=True)), [latest.pk]
        )
        version = GlobalPlanService.transition(version, self.worker, "submit")
        version = GlobalPlanService.transition(version, self.manager, "approve")
        with self.assertRaises(ValidationError), transaction.atomic():
            version.source_versions.add(old)
        version.snapshot["works"][0]["name"] = "Tampered"
        with self.assertRaises(ValidationError):
            version.save()

    def test_global_rejects_foreign_sources_objects_and_duplicate_lower_versions(self):
        other = Company.objects.create(name="Foreign")
        p = Project.objects.create(company=other, code="p", name="p")
        o = ConstructionObject.objects.create(
            company=other, project=p, code="o", name="o"
        )
        with self.assertRaises(PermissionDenied):
            GlobalPlanService.create(self.worker, o, self.start, self.end)
        v1 = self.approved(self.simple, self.start, self.end, [(self.start, None, 1)])
        v2 = self.approved(
            self.simple, self.start, self.end, [(self.start, None, 2)], number=2
        )
        with self.assertRaises(ValidationError):
            GlobalPlanService.create(
                self.worker, self.obj, self.start, self.end, versions=[v1, v2]
            )
        self.assertEqual(GlobalPlanVersion.objects.count(), 0)  # creation rolled back

    def test_pending_migration_prevents_incomplete_global_snapshot(self):
        self.resource_plans()
        LegacyResourceRecord.objects.create(
            company=self.company,
            source_model="LaborPlan",
            source_pk=99,
            payload={"planned_workers": 4},
            reason="Unknown object",
        )
        with self.assertRaises(ValidationError):
            GlobalPlanService.create(self.worker, self.obj, self.start, self.end)

    def test_resource_facts_without_work_are_compared_in_global_version(self):
        self.resource_plans()
        version = GlobalPlanService.create(self.worker, self.obj, self.start, self.end)
        LaborFact.objects.create(
            company=self.company,
            construction_object=self.obj,
            date=self.start,
            brigade=self.brigade,
            actual_workers=1,
            actual_hours=8,
        )
        EquipmentFact.objects.create(
            company=self.company,
            construction_object=self.obj,
            date=self.start,
            equipment_type=self.equipment,
            actual_count=2,
            machine_hours=12,
        )
        FuelFact.objects.create(
            company=self.company,
            construction_object=self.obj,
            date=self.end,
            actual_liters=50,
        )
        rows = comparison(version)["resources"]
        self.assertEqual(len(rows), 4)
        fuel = [r for r in rows if r["kind"] == "fuel"]
        self.assertEqual(sum(r["plan"] for r in fuel), 40)
        self.assertEqual(sum(r["fact"] for r in fuel), 50)

    def test_global_pages_and_actions_are_company_scoped(self):
        self.resource_plans()
        version = GlobalPlanService.create(self.worker, self.obj, self.start, self.end)
        other = Company.objects.create(name="Other")
        u = User.objects.create_user(username="other-global", company=other)
        self.client.force_login(u)
        self.assertEqual(
            self.client.get(
                reverse("planning:global_detail", args=[version.pk])
            ).status_code,
            404,
        )
        self.assertEqual(
            self.client.post(
                reverse("planning:global_action", args=[version.pk, "submit"])
            ).status_code,
            404,
        )
        self.client.force_login(self.worker)
        for route in [
            "planning:global_list",
            "planning:global_create",
            "production:legacy_resources",
        ]:
            self.assertEqual(self.client.get(reverse(route)).status_code, 200)
        self.assertEqual(
            self.client.get(
                reverse("planning:global_detail", args=[version.pk])
            ).status_code,
            200,
        )

    def test_norms_and_fractional_setting_cannot_change_existing_history(self):
        self.fact(self.work, self.a, self.start, 1)
        self.work.allow_fractional = False
        with self.assertRaises(ValidationError):
            self.work.clean()
        self.a.quantity_per_unit = D("3")
        with self.assertRaises(ValidationError):
            self.a.clean()
        item = ProjectWorkItem(
            company=self.company,
            project_work=self.work,
            name="New",
            unit="m",
            quantity_per_unit=1,
        )
        with self.assertRaises(ValidationError):
            item.clean()

    def test_manual_migration_restore_preserves_archive_and_can_explicitly_merge(self):
        payload = {
            "brigade_id": self.brigade.pk,
            "date": self.start.isoformat(),
            "planned_workers": 2,
            "planned_hours": "16",
            "hourly_rate": "100",
            "comment": "Original",
        }
        archive = LegacyResourceRecord.objects.create(
            company=self.company,
            source_model="LaborPlan",
            source_pk=999,
            payload=payload,
            reason="Conflict",
        )
        existing = LaborPlan.objects.create(
            company=self.company,
            construction_object=self.obj,
            date=self.start,
            brigade=self.brigade,
            planned_workers=3,
            planned_hours=24,
            hourly_rate=100,
        )
        self.client.force_login(self.worker)
        url = reverse("production:legacy_resolve", args=[archive.pk])
        self.assertEqual(self.client.get(url).status_code, 200)
        data = {
            "construction_object": self.obj.pk,
            "brigade": self.brigade.pk,
            "date": self.start,
            "planned_workers": 2,
            "planned_hours": 16,
            "hourly_rate": 100,
            "comment": "Original",
        }
        response = self.client.post(url, data)
        self.assertEqual(response.status_code, 200)
        archive.refresh_from_db()
        self.assertFalse(archive.resolved)
        response = self.client.post(url, {**data, "merge_existing": "on"})
        self.assertEqual(response.status_code, 302)
        existing.refresh_from_db()
        archive.refresh_from_db()
        self.assertEqual(existing.planned_workers, 5)
        self.assertEqual(existing.planned_hours, 40)
        self.assertEqual(archive.payload, payload)
        self.assertTrue(archive.resolved)
        self.assertEqual(archive.restored_pk, existing.pk)
