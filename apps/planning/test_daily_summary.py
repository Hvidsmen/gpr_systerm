from datetime import date
from decimal import Decimal as D
from django.test import TestCase
from django.urls import reverse
from . import tests as fixtures
from .models import PlanVersion, MonthlyPlan, DailyPlan
from .daily_summary import daily_summary
from apps.accounts.models import Company
from apps.projects.models import Project, ConstructionObject, Section
from apps.works.models import ProjectWork, ProjectWorkItem


class DailySummaryTests(TestCase):
    setUpTestData = classmethod(fixtures.PlanningWorkflowTests.setUpTestData.__func__)

    def setUp(self):
        self.client.force_login(self.worker)

    def test_month_page_displays_selected_distribution_and_other_versions(self):
        from .services import PlanGeneratorService

        PlanGeneratorService.generate(self.version)
        second = PlanVersion.objects.create(
            company=self.company,
            monthly_plan=self.plan,
            version_number=2,
            created_by=self.worker,
        )
        url = reverse("planning:plan_detail", args=[self.plan.pk])
        response = self.client.get(url)
        self.assertEqual(response.context["selected_version"], second)
        self.assertContains(response, "ещё не сформирован")
        response = self.client.get(url, {"version": self.version.pk})
        self.assertContains(response, "Итоговое распределение по дням")
        self.assertEqual(response.context["total_planned_quantity"], D(10))
        self.assertEqual(response.context["distribution_rows"][1]["total"], D(20))
        self.assertEqual(
            self.client.get(
                reverse("planning:version_detail", args=[self.version.pk])
            ).status_code,
            200,
        )

    def test_missing_distribution_differs_from_generated_zero(self):
        empty = daily_summary(self.version)
        self.assertIsNone(empty["total_planned_quantity"])
        DailyPlan.objects.create(
            company=self.company,
            plan_version=self.version,
            work_item=self.item,
            date=self.today,
            workday_number=1,
            planned_quantity=0,
        )
        zero = daily_summary(self.version)
        self.assertTrue(zero["distribution_generated"])
        self.assertEqual(zero["total_planned_quantity"], D(0))
        self.assertEqual(zero["distribution_rows"][0]["values"], [D(0)])

    def test_all_dates_and_cumulative_norm_context_are_shown(self):
        start = date(2026, 1, 1)
        self.plan.start_date = start
        self.plan.end_date = date(2026, 1, 3)
        self.plan.year = 2026
        self.plan.month = 1
        self.plan.save()
        item2 = ProjectWorkItem.objects.create(
            company=self.company,
            project_work=self.work,
            name="Second",
            unit="m3",
            quantity_per_unit=3,
            weight=0,
            load_profile=self.item.load_profile,
        )
        previous = MonthlyPlan.objects.create(
            company=self.company,
            project_work=self.work,
            year=2025,
            month=12,
            start_date=date(2025, 12, 1),
            end_date=date(2025, 12, 31),
            planned_quantity=4,
        )
        old = PlanVersion.objects.create(
            company=self.company,
            monthly_plan=previous,
            version_number=1,
            status="APPROVED",
        )
        for item, qty in [(self.item, 10), (item2, 12)]:
            DailyPlan.objects.create(
                company=self.company,
                plan_version=old,
                work_item=item,
                date=date(2025, 12, 31),
                workday_number=1,
                planned_quantity=qty,
            )
        DailyPlan.objects.create(
            company=self.company,
            plan_version=self.version,
            work_item=item2,
            date=start,
            workday_number=1,
            planned_quantity=3,
        )
        summary = daily_summary(self.version)
        self.assertEqual(len(summary["distribution_dates"]), 3)
        self.assertEqual(summary["distribution_rows"][0]["values"], [D(1), D(0), D(0)])
        self.assertEqual(summary["distribution_rows"][2]["values"], [D(3), D(0), D(0)])
        self.assertIsNone(summary["days"][0]["fact"])

    def test_foreign_or_unrelated_versions_and_malformed_selection_rejected(self):
        other = MonthlyPlan.objects.create(
            company=self.company,
            project_work=self.work,
            year=2027,
            month=1,
            start_date=date(2027, 1, 1),
            end_date=date(2027, 1, 31),
            planned_quantity=1,
        )
        version = PlanVersion.objects.create(
            company=self.company, monthly_plan=other, version_number=1
        )
        url = reverse("planning:plan_detail", args=[self.plan.pk])
        for selected in [version.pk, "abc", -1]:
            self.assertEqual(
                self.client.get(url, {"version": selected}).status_code, 404
            )
        company = Company.objects.create(name="Foreign")
        project = Project.objects.create(company=company, name="Secret")
        obj = ConstructionObject.objects.create(
            company=company, project=project, name="Secret"
        )
        section = Section.objects.create(
            company=company, construction_object=obj, name="Secret"
        )
        work = ProjectWork.objects.create(
            company=company, section=section, name="Secret", unit="m"
        )
        plan = MonthlyPlan.objects.create(
            company=company,
            project_work=work,
            year=2026,
            month=1,
            start_date=date(2026, 1, 1),
            end_date=date(2026, 1, 31),
            planned_quantity=1,
        )
        self.assertEqual(
            self.client.get(
                reverse("planning:plan_detail", args=[plan.pk])
            ).status_code,
            404,
        )
