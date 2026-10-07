from datetime import timedelta
from decimal import Decimal
from django.test import TestCase, Client
from django.urls import reverse
from django.utils.http import urlencode
from apps.accounts.models import User, Role
from apps.projects.models import Section
from apps.works.models import ProjectWork, ProjectWorkItem
from .models import DailyFact
from . import test_day_workspace


class FactMatrixTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        test_day_workspace.FactDayWorkspaceTests.setUpTestData.__func__(cls)
        cls.admin = User.objects.create_user(
            username="matrix-admin",
            company=cls.company,
            role=Role.objects.get(code="ADMIN"),
        )
        cls.reader = User.objects.create_user(
            username="matrix-reader",
            company=cls.company,
            role=Role.objects.get(code="MANAGER"),
        )
        cls.foreign_section = Section.objects.create(
            company=cls.foreign,
            construction_object=cls.foreign_obj,
            name="Foreign section",
        )
        cls.foreign_work = ProjectWork.objects.create(
            company=cls.foreign,
            section=cls.foreign_section,
            name="Foreign work",
            unit="m",
        )
        cls.second_item = ProjectWorkItem.objects.create(
            company=cls.company,
            project_work=cls.composite,
            name="Second part",
            unit="км",
            quantity_per_unit=1,
        )

    def setUp(self):
        self.client.force_login(self.user)
        self.simple_fact = self.fact(self.work, self.day, 0)
        self.next_fact = self.fact(self.work, self.day + timedelta(days=1), 3)
        self.part_fact = self.fact(self.composite, self.day, 35, self.item)
        self.second_part = self.fact(
            self.composite, self.day, Decimal(".650"), self.second_item
        )
        self.other_fact = self.fact(self.other_work, self.day, 99)
        self.foreign_fact = DailyFact.objects.create(
            company=self.foreign,
            project_work=self.foreign_work,
            date=self.day,
            actual_quantity=100,
        )
        self.params = {
            "start": self.day.isoformat(),
            "end": (self.day + timedelta(days=2)).isoformat(),
        }

    def fact(self, work, day, quantity, item=None):
        return DailyFact.objects.create(
            company=self.company,
            project_work=work,
            work_item=item,
            date=day,
            actual_quantity=quantity,
            reported_by=self.user,
        )

    def matrix(self, **params):
        response = self.client.get(
            reverse("production:fact_list"), {**self.params, **params}
        )
        self.assertEqual(response.status_code, 200)
        return response

    def test_days_columns_and_separate_units_zero_missing_and_totals(self):
        response = self.matrix()
        self.assertEqual(len(response.context["matrix_days"]), 3)
        rows = response.context["matrix_rows"]
        self.assertEqual(len(rows), 3)
        simple = next(row for row in rows if row["work"].pk == self.work.pk)
        self.assertEqual(simple["cells"][0].actual_quantity, 0)
        self.assertEqual(simple["cells"][1].pk, self.next_fact.pk)
        self.assertIsNone(simple["cells"][2])
        self.assertEqual(simple["total"], 3)
        child = next(
            row for row in rows if row["item"] and row["item"].pk == self.second_item.pk
        )
        self.assertEqual(child["unit"], "км")
        self.assertEqual(child["total"], Decimal(".650"))
        self.assertContains(response, "Удалить факт")
        self.assertNotContains(response, "Foreign work")
        self.assertNotContains(response, "Other work")

    def test_object_and_work_filtering_and_foreman_choices(self):
        self.assertEqual(len(self.matrix(work=self.work.pk).context["matrix_rows"]), 1)
        response = self.matrix(construction_object=self.other_obj.pk)
        self.assertFalse(response.context["matrix_rows"])
        self.assertTrue(response.context["filter_form"].errors)
        self.assertEqual(
            set(
                self.matrix()
                .context["filter_form"]
                .fields["construction_object"]
                .queryset.values_list("pk", flat=True)
            ),
            {self.obj.pk},
        )
        self.client.force_login(self.admin)
        self.assertEqual(
            len(
                self.matrix(construction_object=self.other_obj.pk).context[
                    "matrix_rows"
                ]
            ),
            1,
        )
        self.assertFalse(
            self.matrix(construction_object=self.foreign_obj.pk).context["matrix_rows"]
        )

    def test_delete_is_confirmed_post_and_returns_to_same_matrix(self):
        query = urlencode({**self.params, "construction_object": self.obj.pk})
        from apps.works.progress import WorkProgressService

        self.assertEqual(WorkProgressService.completed(self.composite), Decimal(".650"))
        url = reverse("production:fact_delete", args=[self.part_fact.pk])
        confirmation = self.client.get(url, {"return_query": query})
        self.assertEqual(confirmation.status_code, 200)
        self.assertTrue(DailyFact.objects.filter(pk=self.part_fact.pk).exists())
        self.assertContains(confirmation, "Part")
        self.assertContains(confirmation, "35,000")
        response = self.client.post(url, {"return_query": query})
        self.assertRedirects(response, reverse("production:fact_list") + "?" + query)
        self.assertFalse(DailyFact.objects.filter(pk=self.part_fact.pk).exists())
        self.assertEqual(WorkProgressService.completed(self.composite), 0)
        self.assertTrue(DailyFact.objects.filter(pk=self.second_part.pk).exists())
        self.assertEqual(len(self.matrix().context["matrix_rows"]), 2)

    def test_foreman_cannot_delete_other_object_or_company_even_by_direct_post(self):
        for fact in [self.other_fact, self.foreign_fact]:
            url = reverse("production:fact_delete", args=[fact.pk])
            self.assertEqual(self.client.get(url).status_code, 404)
            self.assertEqual(self.client.post(url).status_code, 404)
            self.assertTrue(DailyFact.objects.filter(pk=fact.pk).exists())

    def test_readonly_roles_have_no_delete_controls_and_cannot_delete(self):
        self.client.force_login(self.reader)
        self.assertNotContains(self.matrix(), 'class="fact-delete"')
        url = reverse("production:fact_delete", args=[self.simple_fact.pk])
        self.assertEqual(self.client.get(url).status_code, 403)
        self.assertEqual(self.client.post(url).status_code, 403)
        self.assertTrue(DailyFact.objects.filter(pk=self.simple_fact.pk).exists())

    def test_csrf_and_period_validation(self):
        secure = Client(enforce_csrf_checks=True)
        secure.force_login(self.user)
        self.assertEqual(
            secure.post(
                reverse("production:fact_delete", args=[self.simple_fact.pk])
            ).status_code,
            403,
        )
        self.assertTrue(DailyFact.objects.filter(pk=self.simple_fact.pk).exists())
        self.assertFalse(self.matrix(start="invalid").context["matrix_rows"])
        self.assertFalse(
            self.matrix(start="2026-10-08", end="2026-10-01").context["matrix_rows"]
        )
        self.assertFalse(
            self.matrix(start="2020-01-01", end="2026-12-31").context["matrix_rows"]
        )

    def test_paginate_rows_not_individual_daily_records(self):
        for i in range(20):
            work = ProjectWork.objects.create(
                company=self.company,
                section=self.work.section,
                name=f"Row {i}",
                unit="m",
            )
            self.fact(work, self.day, 1)
            self.fact(work, self.day + timedelta(days=1), 2)
        response = self.matrix()
        self.assertTrue(response.context["is_paginated"])
        self.assertEqual(len(response.context["matrix_rows"]), 20)
        self.assertEqual(len(self.matrix(page=2).context["matrix_rows"]), 3)
        row = next(
            row
            for row in response.context["matrix_rows"]
            if row["work"].name.startswith("Row")
        )
        self.assertEqual(row["total"], 3)
        self.assertTrue(row["cells"][0] and row["cells"][1])
        self.assertContains(response, "&amp;page=2")

    def test_saved_past_fact_opens_its_month_in_matrix(self):
        response = self.client.post(
            reverse("production:fact_create"),
            {
                "project_work": self.work.pk,
                "date": "2025-12-15",
                "actual_quantity": 2,
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn("start=2025-12-01", response.url)
        self.assertIn("end=2025-12-31", response.url)
        self.assertContains(self.client.get(response.url), self.work.name)
