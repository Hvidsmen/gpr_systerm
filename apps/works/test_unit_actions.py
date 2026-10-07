from django.test import TestCase, Client
from django.urls import reverse
from apps.accounts.models import User, Role
from . import test_catalogs as fixtures
from .models import MeasurementUnit, ProjectWork


class UnitActionTests(TestCase):
    setUpTestData = classmethod(fixtures.WorkCatalogTests.setUpTestData.__func__)

    def setUp(self):
        self.client.force_login(self.user)
        self.unit = MeasurementUnit.objects.create(
            company=self.company, symbol="рейс.", name="Рейс"
        )
        self.edit = reverse("works:unit_update", args=[self.unit.pk])
        self.delete = reverse("works:unit_delete", args=[self.unit.pk])

    def test_buttons_and_edit(self):
        response = self.client.get(reverse("works:unit_list"))
        self.assertContains(response, self.edit)
        self.assertContains(response, self.delete)
        self.assertEqual(self.client.get(self.edit).status_code, 200)
        self.assertRedirects(
            self.client.post(self.edit, {"symbol": "рейс", "name": "Рейсы"}),
            reverse("works:unit_list"),
        )
        self.unit.refresh_from_db()
        self.assertEqual((self.unit.symbol, self.unit.name), ("рейс", "Рейсы"))
        duplicate = self.client.post(self.edit, {"symbol": "м", "name": "Метр"})
        self.assertTrue(duplicate.context["form"].errors)
        self.unit.refresh_from_db()
        self.assertEqual(self.unit.symbol, "рейс")

    def test_delete_requires_post_and_csrf(self):
        self.assertEqual(self.client.get(self.delete).status_code, 200)
        self.assertTrue(MeasurementUnit.objects.filter(pk=self.unit.pk).exists())
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.user)
        self.assertEqual(client.post(self.delete).status_code, 403)
        self.assertTrue(MeasurementUnit.objects.filter(pk=self.unit.pk).exists())
        self.assertRedirects(self.client.post(self.delete), reverse("works:unit_list"))
        self.assertFalse(MeasurementUnit.objects.filter(pk=self.unit.pk).exists())

    def test_used_unit_cannot_be_deleted_or_relabelled_but_name_can_change(self):
        work = ProjectWork.objects.create(
            company=self.company,
            section=self.section,
            name="Work",
            unit=self.unit.symbol,
            load_profile=self.profile,
        )
        self.assertEqual(self.client.post(self.delete).status_code, 400)
        response = self.client.post(self.edit, {"symbol": "рейс", "name": "Рейс"})
        self.assertIn("symbol", response.context["form"].errors)
        self.unit.refresh_from_db()
        self.assertEqual(self.unit.symbol, "рейс.")
        self.assertRedirects(
            self.client.post(self.edit, {"symbol": "рейс.", "name": "Рейсы"}),
            reverse("works:unit_list"),
        )
        work.refresh_from_db()
        self.assertEqual(work.unit, "рейс.")

    def test_company_scope_for_get_and_post(self):
        unit = MeasurementUnit.objects.create(
            company=self.foreign, symbol="private", name="Private"
        )
        for route in ["unit_update", "unit_delete"]:
            url = reverse("works:" + route, args=[unit.pk])
            self.assertEqual(self.client.get(url).status_code, 404)
            self.assertEqual(
                self.client.post(url, {"symbol": "x", "name": "X"}).status_code, 404
            )
        self.assertTrue(MeasurementUnit.objects.filter(pk=unit.pk).exists())

    def test_readonly_role_cannot_change_catalog(self):
        user = User.objects.create_user(
            username="unit-reader",
            company=self.company,
            role=Role.objects.get(code="MANAGER"),
        )
        self.client.force_login(user)
        response = self.client.get(reverse("works:unit_list"))
        self.assertNotContains(response, self.edit)
        self.assertNotContains(response, self.delete)
        for url in [self.edit, self.delete]:
            self.assertEqual(self.client.get(url).status_code, 403)
            self.assertEqual(
                self.client.post(url, {"symbol": "x", "name": "X"}).status_code, 403
            )
