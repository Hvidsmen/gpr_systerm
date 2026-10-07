from django.core import signing
from django.test import TestCase, Client
from django.urls import reverse
from apps.planning import test_workspace as fixtures
from apps.works.models import ProjectWork, MeasurementUnit, WorkGroup
from apps.resources.models import Brigade, EquipmentCategory, EquipmentType
from apps.accounts.models import User, Company, Role
from core.bulk_delete import SALT


class BulkDeleteTests(TestCase):
    setUpTestData = classmethod(fixtures.WorkspaceTests.setUpTestData.__func__)

    def setUp(self):
        self.client.force_login(self.planner)

    def endpoint(self, kind, app="works"):
        return reverse(app + ":bulk_delete", kwargs={"kind": kind})

    def choose(self, kind, records, app="works", **extra):
        return self.client.post(
            self.endpoint(kind, app), {"selected": [r.pk for r in records], **extra}
        )

    def confirm(self, response, kind, app="works", **extra):
        return self.client.post(
            self.endpoint(kind, app),
            {"confirm": "1", "selection": response.context["selection"], **extra},
        )

    def test_work_selection_preview_then_delete_with_children(self):
        response = self.choose(
            "works", [self.simple, self.composite], return_query="work=Simple&page=2"
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context["blocked"])
        self.assertContains(response, "подработ")
        self.assertEqual(
            response.context["back"], reverse("works:work_list") + "?work=Simple&page=2"
        )
        self.assertTrue(ProjectWork.objects.filter(pk=self.simple.pk).exists())
        result = self.confirm(response, "works", return_query="work=Simple&page=2")
        self.assertEqual(result.status_code, 302)
        self.assertFalse(
            ProjectWork.objects.filter(
                pk__in=[self.simple.pk, self.composite.pk]
            ).exists()
        )
        self.assertFalse(self.composite.items.exists())

    def test_allocated_work_blocks_whole_group(self):
        fixtures.WorkspaceTests.create(self)
        response = self.choose("works", [self.simple, self.composite])
        self.assertTrue(response.context["blocked"])
        result = self.confirm(response, "works")
        self.assertEqual(result.status_code, 400)
        self.assertEqual(
            ProjectWork.objects.filter(
                pk__in=[self.simple.pk, self.composite.pk]
            ).count(),
            2,
        )

    def test_used_units_and_protected_equipment_category_block(self):
        used = MeasurementUnit.objects.get(company=self.company, symbol="m")
        unused = MeasurementUnit.objects.create(
            company=self.company, symbol="new-unit", name="New"
        )
        response = self.choose("units", [used, unused])
        self.assertTrue(response.context["blocked"])
        self.assertEqual(self.confirm(response, "units").status_code, 400)
        self.assertTrue(MeasurementUnit.objects.filter(pk=unused.pk).exists())
        category = EquipmentCategory.objects.create(
            company=self.company, name="Category"
        )
        self.equipment.category = category
        self.equipment.save()
        response = self.choose("equipment_categories", [category], app="resources")
        self.assertTrue(response.context["blocked"])
        self.assertEqual(
            self.confirm(response, "equipment_categories", "resources").status_code, 400
        )
        self.assertTrue(EquipmentCategory.objects.filter(pk=category.pk).exists())

    def test_group_delete_reports_detached_links(self):
        group = WorkGroup.objects.create(company=self.company, name="Group")
        self.simple.work_group = group
        self.simple.save()
        response = self.choose("work_groups", [group])
        self.assertFalse(response.context["blocked"])
        self.assertTrue(response.context["detached"])
        self.assertContains(response, "Связи будут очищены")
        self.assertEqual(self.confirm(response, "work_groups").status_code, 302)
        self.simple.refresh_from_db()
        self.assertIsNone(self.simple.work_group_id)

    def test_foreign_and_missing_ids_do_not_delete_anything(self):
        other = Company.objects.create(name="Other bulk")
        unit = MeasurementUnit.objects.create(
            company=other, symbol="other", name="Other"
        )
        own = MeasurementUnit.objects.create(
            company=self.company, symbol="own", name="Own"
        )
        response = self.choose("units", [own, unit])
        self.assertEqual(response.status_code, 403)
        self.assertTrue(MeasurementUnit.objects.filter(pk=own.pk).exists())
        response = self.client.post(
            self.endpoint("units"), {"selected": [own.pk, 999999]}
        )
        self.assertEqual(response.status_code, 403)
        self.assertTrue(MeasurementUnit.objects.filter(pk=own.pk).exists())

    def test_token_binding_and_csrf(self):
        unit = MeasurementUnit.objects.create(
            company=self.company, symbol="unused", name="Unused"
        )
        response = self.choose("units", [unit])
        payload = signing.loads(response.context["selection"], salt=SALT)
        payload["kind"] = "work_groups"
        tampered = signing.dumps(payload, salt=SALT)
        response = self.client.post(
            self.endpoint("units"), {"confirm": 1, "selection": tampered}
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(MeasurementUnit.objects.filter(pk=unit.pk).exists())
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.planner)
        self.assertEqual(
            client.post(self.endpoint("units"), {"selected": [unit.pk]}).status_code,
            403,
        )
        self.assertEqual(self.client.get(self.endpoint("units")).status_code, 405)

    def test_readonly_user_cannot_bulk_delete(self):
        self.client.force_login(self.manager)
        response = self.client.get(reverse("works:work_list"))
        self.assertNotContains(response, "data-bulk-row")
        self.assertNotContains(response, "bulk-delete-form")
        response = self.choose("works", [self.simple])
        self.assertEqual(response.status_code, 403)

    def test_dependency_added_after_preview_blocks_confirmation(self):
        response = self.choose("works", [self.simple])
        self.assertFalse(response.context["blocked"])
        fixtures.WorkspaceTests.create(self)
        self.assertEqual(self.confirm(response, "works").status_code, 400)
        self.assertTrue(ProjectWork.objects.filter(pk=self.simple.pk).exists())

    def test_all_catalog_lists_render_selection_and_routes(self):
        urls = [
            "works:group_list",
            "works:unit_list",
            "works:work_list",
            "resources:brigade_list",
            "resources:equipment_type_list",
            "resources:brigade_group_list",
            "resources:brigade_macro_group_list",
            "resources:equipment_category_list",
            "resources:employee_list",
            "planning:profile_list",
            "planning:calendar_list",
        ]
        for name in urls:
            response = self.client.get(reverse(name))
            self.assertEqual(response.status_code, 200, name)
            self.assertContains(response, 'id="bulk-delete-form"')
        for name in [
            "works:work_list",
            "resources:brigade_list",
            "resources:equipment_type_list",
            "works:unit_list",
            "planning:calendar_list",
        ]:
            self.assertContains(self.client.get(reverse(name)), "data-bulk-row")
