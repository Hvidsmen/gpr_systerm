from datetime import date
from decimal import Decimal
from django.test import TestCase
from django.urls import reverse
from . import test_workspace as fixtures
from .test_meeting_import import upload
from .meeting_import import apply_meeting_import, parse_meeting_workbook
from .models import PlanningWorkspace, GlobalPlanVersion, WorkMonthAllocation
from .workspace_services import WorkspaceService
from .global_services import GlobalPlanService

JAN, FEB = fixtures.JAN, fixtures.FEB


class MeetingVersionImportTests(TestCase):
    setUpTestData = classmethod(fixtures.WorkspaceTests.setUpTestData.__func__)
    create = fixtures.WorkspaceTests.create

    def setUp(self):
        self.workspace = self.create()
        self.version = self.workspace.baseline_version
        self.url = reverse("planning:workspace_meeting_import", args=[self.version.pk])
        self.client.force_login(self.planner)

    def preview(self, **extra):
        return self.client.post(
            self.url,
            {
                "start": JAN,
                "end": date(2026, 1, 31),
                "resource_rule": "maximum",
                "file": upload(),
                **extra,
            },
        )

    def test_editor_link_has_current_version_and_form_needs_no_project(self):
        response = self.client.get(
            reverse("planning:workspace_edit", args=[self.version.pk])
        )
        self.assertContains(response, self.url)
        response = self.client.get(self.url)
        self.assertNotIn("project", response.context["form"].fields)
        self.assertContains(response, "Версия:")
        self.assertContains(response, "данные записываются в эту же версию")

    def test_preview_no_writes_confirm_same_version_and_repeat_updates(self):
        original = WorkMonthAllocation.objects.count()
        preview = self.preview()
        self.assertEqual(preview.status_code, 200)
        self.assertEqual(len(preview.context["sheets"]), 1)
        self.assertEqual(WorkMonthAllocation.objects.count(), original)
        self.assertContains(preview, "Записать в текущую версию")
        counts = (PlanningWorkspace.objects.count(), GlobalPlanVersion.objects.count())
        token = preview.context["preview"]
        confirmed = self.client.post(self.url, {"action": "confirm", "preview": token})
        self.assertRedirects(
            confirmed,
            reverse("planning:workspace_edit", args=[self.version.pk])
            + "?month=2026-01-01",
        )
        self.assertEqual(
            counts,
            (PlanningWorkspace.objects.count(), GlobalPlanVersion.objects.count()),
        )
        row = self.version.work_allocations.get(work__name="Imported work", month=JAN)
        self.assertEqual(row.quantity, Decimal(5))
        row.quantity = 99
        row.save()
        preview = self.preview()
        self.client.post(
            self.url, {"action": "confirm", "preview": preview.context["preview"]}
        )
        row.refresh_from_db()
        self.assertEqual(row.quantity, Decimal(5))
        self.assertEqual(
            self.version.work_allocations.filter(
                work__name="Imported work", month=JAN
            ).count(),
            1,
        )
        self.assertEqual(
            self.version.work_allocations.get(work=self.simple, month=FEB).quantity, 200
        )
        self.assertFalse(
            self.version.work_allocations.filter(
                work__name="Imported work", month=FEB
            ).exists()
        )
        self.assertEqual(self.version.resource_allocations.count(), 2)

    def test_updates_existing_work_but_preserves_other_months_and_positions(self):
        sheets = parse_meeting_workbook(
            upload(work_names=[("Simple", "m", 2)]), JAN, JAN
        )
        apply_meeting_import(
            self.planner,
            self.obj.project_id,
            sheets,
            {"0"},
            JAN,
            JAN,
            target_version=self.version,
        )
        self.assertEqual(
            self.version.work_allocations.get(work=self.simple, month=JAN).quantity, 2
        )
        self.assertEqual(
            self.version.work_allocations.get(work=self.simple, month=FEB).quantity, 200
        )

    def test_foreign_object_sheet_is_not_imported(self):
        response = self.preview(file=upload(name="other-object"))
        self.assertTrue(response.context["form"].errors)
        self.assertContains(response, "В файле должен быть один лист объекта")
        self.assertEqual(self.version.resource_allocations.count(), 0)

    def test_confirmation_cannot_be_reused_or_sent_to_another_version(self):
        preview = self.preview()
        other = WorkspaceService.create(
            self.planner, self.obj, "Second", JAN, date(2026, 1, 31)
        ).baseline_version
        url = reverse("planning:workspace_meeting_import", args=[other.pk])
        response = self.client.post(
            url, {"action": "confirm", "preview": preview.context["preview"]}
        )
        self.assertRedirects(response, url)
        self.assertFalse(other.work_allocations.exists())
        self.client.post(
            self.url, {"action": "confirm", "preview": preview.context["preview"]}
        )
        count = self.version.work_allocations.count()
        self.client.post(
            self.url, {"action": "confirm", "preview": preview.context["preview"]}
        )
        self.assertEqual(self.version.work_allocations.count(), count)

    def test_period_and_readonly_status_checked_again_at_confirmation(self):
        response = self.preview(start=date(2025, 12, 31))
        self.assertTrue(response.context["form"].errors)
        preview = self.preview()
        GlobalPlanService.transition(self.version, self.planner, "submit")
        response = self.client.post(
            self.url, {"action": "confirm", "preview": preview.context["preview"]}
        )
        self.assertEqual(response.status_code, 403)
        self.assertFalse(self.version.resource_allocations.exists())

    def test_forecast_period_cannot_edit_locked_months(self):
        from .approval_test_helpers import departments

        approved = GlobalPlanService.transition(self.version, self.planner, "submit")
        approved = GlobalPlanService.transition(
            departments(approved, self.approvers), self.approvers["CEO"], "approve"
        )
        version = WorkspaceService.forecast(
            self.workspace, self.planner, FEB, "BASELINE"
        )
        url = reverse("planning:workspace_meeting_import", args=[version.pk])
        form = self.client.get(url).context["form"]
        self.assertEqual(form.initial["start"], FEB)
        self.assertEqual(form.initial["end"], date(2026, 2, 28))
        response = self.client.post(
            url,
            {
                "start": JAN,
                "end": date(2026, 1, 31),
                "file": upload(),
                "resource_rule": "maximum",
            },
        )
        self.assertTrue(response.context["form"].errors)

    def test_composite_updates_current_version_with_source_item_values(self):
        sheets = parse_meeting_workbook(
            upload(work_names=[("A", "m", 10), ("B", "m3", 12)]), JAN, date(2026, 1, 31)
        )
        apply_meeting_import(
            self.planner,
            self.obj.project_id,
            sheets,
            {"0"},
            JAN,
            date(2026, 1, 31),
            target_version=self.version,
        )
        row = self.version.work_allocations.get(work=self.composite, month=JAN)
        self.assertEqual(row.quantity, Decimal(5))
        self.assertEqual(
            row.item_quantities,
            {str(self.a.pk): "13.000000", str(self.b.pk): "15.000000"},
        )

    def test_wrong_role_and_company_cannot_open_import(self):
        from apps.accounts.models import Company, User, Role

        other = Company.objects.create(name="Foreign meeting")
        user = User.objects.create_user(
            username="foreign-meeting",
            company=other,
            role=Role.objects.get(code="PLANNER"),
        )
        self.client.force_login(user)
        self.assertEqual(self.client.get(self.url).status_code, 404)
        self.client.force_login(self.manager)
        self.assertEqual(self.client.get(self.url).status_code, 403)

    def test_only_current_object_sheet_is_parsed(self):
        from io import BytesIO
        from openpyxl import load_workbook
        from django.core.files.uploadedfile import SimpleUploadedFile

        book = load_workbook(BytesIO(upload().read()))
        other = book.copy_worksheet(book.active)
        other.title = "Another object"
        other["P4"] = "#REF!"
        stream = BytesIO()
        book.save(stream)
        response = self.preview(
            file=SimpleUploadedFile("meeting.xlsx", stream.getvalue())
        )
        self.assertEqual(len(response.context["sheets"]), 1)
        self.assertFalse(response.context["sheets"][0]["errors"])
