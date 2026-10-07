from copy import deepcopy
from datetime import date
from decimal import Decimal
from unittest.mock import patch
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase
from django.urls import reverse
from apps.accounts.models import Company
from . import test_report_matrix as fixtures
from .test_report_matrix import rows_in
from .models import ResourceMonthAllocation
from .report_services import Source, build_matrix


class GlobalMatrixTests(TestCase):
    setUpTestData = classmethod(fixtures.MatrixReportTests.setUpTestData.__func__)
    create = fixtures.MatrixReportTests.create
    approve = fixtures.MatrixReportTests.approve

    def setUp(self):
        self.version = self.create([self.simple, self.composite]).baseline_version
        self.resource = ResourceMonthAllocation.objects.create(
            company=self.company,
            version=self.version,
            month=date(2026, 1, 1),
            kind="labor",
            brigade=self.brigade,
            count=10,
        )
        self.url = reverse("planning:global_detail", args=[self.version.pk])
        self.client.force_login(self.planner)

    def rows(self, response, kind):
        section = next(
            s for s in response.context["objects"][0]["sections"] if s["kind"] == kind
        )
        return rows_in(section["groups"])

    def test_draft_matrix_uses_current_counts_and_does_not_save_preview(self):
        original = deepcopy(self.version.snapshot)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        labor = self.rows(response, "labor")[0]
        self.assertEqual(labor["unit"], "чел.")
        self.assertEqual(labor["cells"][0]["plan"], Decimal(10))
        self.assertContains(response, "Workers macro")
        self.assertContains(response, "Civil works")
        self.assertContains(response, "data-work-toggle")
        self.assertNotContains(response, "Часы: план / факт")
        self.resource.count = 7
        self.resource.save()
        response = self.client.get(self.url)
        self.assertEqual(
            self.rows(response, "labor")[0]["cells"][0]["plan"], Decimal(7)
        )
        self.version.refresh_from_db()
        self.assertEqual(self.version.snapshot, original)
        self.assertEqual(self.version.status, "DRAFT")

    def test_daily_drill_and_back_preserve_filters_and_version(self):
        response = self.client.get(self.url, {"labor_q": "Brigade"})
        url = response.context["columns"][0]["url"]
        self.assertIn(self.url, url)
        self.assertIn("labor_q=Brigade", url)
        daily = self.client.get(url)
        self.assertTrue(daily.context["daily"])
        self.assertEqual(len(daily.context["columns"]), 31)
        self.assertEqual(self.rows(daily, "labor")[0]["cells"][0]["plan"], Decimal(10))
        self.assertIn("labor_q=Brigade", daily.context["monthly_url"])
        self.assertNotIn("month=", daily.context["monthly_url"])

    def test_filters_and_all_hidden(self):
        response = self.client.get(
            self.url, {"works_q": "absent", "macro_group": self.macro.pk}
        )
        self.assertEqual(self.rows(response, "works"), [])
        self.assertEqual(len(self.rows(response, "labor")), 1)
        response = self.client.get(self.url, {"sections_set": "1"})
        self.assertEqual(response.context["objects"][0]["sections"], [])
        self.assertContains(response, "Все разделы скрыты")

    def test_bounds_and_foreign_group_are_rejected(self):
        from apps.resources.models import BrigadeMacroGroup

        other = Company.objects.create(name="Foreign matrix")
        group = BrigadeMacroGroup.objects.create(company=other, name="Foreign")
        for params in [
            {"start": "2025-12-31"},
            {"end": "2026-04-01"},
            {"month": "2026-04-01"},
            {"macro_group": group.pk},
        ]:
            response = self.client.get(self.url, params)
            self.assertTrue(response.context["form"].errors)
            self.assertNotContains(response, 'id="plan-fact-matrix"')

    def test_submitted_snapshot_stays_frozen(self):
        from .global_services import GlobalPlanService

        self.version = GlobalPlanService.transition(
            self.version, self.planner, "submit"
        )
        original = deepcopy(self.version.snapshot)
        with patch(
            "apps.planning.global_matrix.build_snapshot",
            side_effect=AssertionError("Must use frozen plan"),
        ):
            response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            self.rows(response, "labor")[0]["cells"][0]["plan"], Decimal(10)
        )
        self.version.refresh_from_db()
        self.assertEqual(self.version.snapshot, original)
        self.assertContains(response, "Согласование")

    def test_unavailable_preview_keeps_approval_controls(self):
        with patch(
            "apps.planning.global_matrix.build_snapshot",
            side_effect=ValidationError("Настройте календарь"),
        ):
            response = self.client.get(self.url)
        self.assertContains(response, "Настройте календарь")
        self.assertContains(response, "Отправить на согласование")
        self.assertNotContains(response, 'id="plan-fact-matrix"')

    def test_internal_source_override_cannot_cross_objects(self):
        with self.assertRaises(PermissionDenied):
            build_matrix(
                self.planner,
                {
                    "start": self.version.start_date,
                    "end": self.version.end_date,
                    "sections": [],
                },
                source_overrides={
                    999999: [
                        Source(
                            self.version,
                            {},
                            self.version.start_date,
                            self.version.end_date,
                        )
                    ]
                },
            )
