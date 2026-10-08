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

    def test_trailing_unit_dot_removed_before_preview_and_creation(self):
        response = self.preview(
            file=upload(work_names=[("New dotted work", "  кв.м.  ", 2)])
        )
        entry = next(
            row
            for row in response.context["sheets"][0]["entries"]
            if row["kind"] == "work"
        )
        self.assertEqual(entry["unit"], "кв.м")
        self.client.post(
            self.url, {"action": "confirm", "preview": response.context["preview"]}
        )
        row = self.version.work_allocations.get(work__name="New dotted work")
        self.assertEqual(row.work.unit, "кв.м")
        from apps.works.models import MeasurementUnit

        self.assertTrue(
            MeasurementUnit.objects.filter(company=self.company, symbol="кв.м").exists()
        )
        self.assertFalse(
            MeasurementUnit.objects.filter(
                company=self.company, symbol="кв.м."
            ).exists()
        )

    def test_dotted_units_match_existing_works_and_subworks(self):
        response = self.preview(
            file=upload(
                work_names=[("Simple", "m.", 2), ("A", "m.", 10), ("B", "m3.", 12)]
            )
        )
        self.assertFalse(response.context["sheets"][0]["errors"])
        self.client.post(
            self.url, {"action": "confirm", "preview": response.context["preview"]}
        )
        self.assertEqual(
            self.version.work_allocations.get(work=self.simple, month=JAN).quantity, 5
        )
        self.assertEqual(
            self.version.work_allocations.get(work=self.composite, month=JAN).quantity,
            5,
        )
        from apps.works.models import ProjectWork

        self.assertEqual(
            ProjectWork.objects.filter(company=self.company, name="Simple").count(), 1
        )

    def batch_preview(self, second_start=FEB, second_end=date(2026, 2, 28), **extra):
        return self.client.post(self.url, {
            "files-TOTAL_FORMS": "2", "files-INITIAL_FORMS": "0",
            "files-0-start": JAN, "files-0-end": date(2026, 1, 31),
            "files-0-resource_rule": "maximum", "files-0-file": upload(),
            "files-1-start": second_start, "files-1-end": second_end,
            "files-1-resource_rule": "maximum", "files-1-file": upload(), **extra,
        })

    def test_batch_separate_periods_preview_and_confirmation(self):
        response = self.batch_preview()
        self.assertEqual(len(response.context["sheets"]), 2)
        self.assertFalse(response.context["blocked"])
        self.assertFalse(self.version.work_allocations.filter(work__name="Imported work").exists())
        self.client.post(self.url, {"action": "confirm", "preview": response.context["preview"]})
        self.assertEqual(self.version.work_allocations.get(work__name="Imported work", month=JAN).quantity, 5)
        self.assertEqual(self.version.work_allocations.get(work__name="Imported work", month=FEB).quantity, 4)

    def test_batch_conflict_blocks_all_writes_even_with_manual_confirmation(self):
        response = self.batch_preview(JAN, date(2026, 1, 31))
        self.assertTrue(response.context["blocked"])
        self.assertContains(response, "Конфликт файлов")
        result = self.client.post(self.url, {"action": "confirm", "preview": response.context["preview"]})
        self.assertEqual(result.status_code, 200)
        self.assertFalse(self.version.work_allocations.filter(work__name="Imported work").exists())
        self.assertFalse(self.version.resource_allocations.exists())

    def test_batch_deleted_file_and_invalid_period(self):
        response = self.batch_preview(second_start=date(2025, 1, 1))
        self.assertTrue(response.context["formset"].errors[1])
        response = self.batch_preview(second_start=date(2025, 1, 1), **{"files-1-DELETE": "on"})
        self.assertEqual(len(response.context["sheets"]), 1)
        self.assertFalse(response.context["blocked"])

    def test_batch_table_and_empty_submission(self):
        response = self.client.get(self.url)
        self.assertContains(response, "Добавить файл")
        self.assertContains(response, "Удалить")
        response = self.client.post(self.url, {"files-TOTAL_FORMS": "0", "files-INITIAL_FORMS": "0"})
        self.assertTrue(response.context["formset"].non_form_errors())

    def test_batch_rolls_back_first_file_when_second_apply_fails(self):
        from unittest.mock import patch
        from django.core.exceptions import ValidationError
        from .meeting_import import apply_meeting_batch
        response = self.batch_preview()
        from django.core import signing
        from .meeting_import import SALT
        payload = signing.loads(response.context["preview"], salt=SALT)
        original = apply_meeting_import
        calls = 0
        def failing_apply(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise ValidationError("Second file failed")
            return original(*args, **kwargs)
        with patch("apps.planning.meeting_import.apply_meeting_import", side_effect=failing_apply):
            with self.assertRaises(ValidationError):
                apply_meeting_batch(self.planner, self.obj.project_id, payload["batches"], self.version)
        self.assertFalse(self.version.work_allocations.filter(work__name="Imported work").exists())
        self.assertFalse(self.version.resource_allocations.exists())

    def test_same_work_name_in_different_sections_is_imported_separately(self):
        from apps.works.models import ProjectWork
        first_name = self.simple.section.name
        for _ in range(2):
            response = self.preview(file=upload(
                work_names=[('Simple', 'm', 2), ('Simple', 'm', 7)],
                sections=[first_name, 'Another section']))
            self.assertFalse(response.context['blocked'])
            rows = [r for r in response.context['sheets'][0]['entries'] if r['kind'] == 'work']
            self.assertEqual(rows[0]['target_id'], self.simple.pk)
            self.assertEqual(rows[1]['section'], 'Another section')
            self.assertNotEqual(rows[0]['target_id'], rows[1]['target_id'])
            confirmed = self.client.post(self.url, {'action': 'confirm', 'preview': response.context['preview']})
            self.assertEqual(confirmed.status_code, 302)
            other = ProjectWork.objects.get(section__construction_object=self.obj,
                                           section__name='Another section', name='Simple')
            self.assertEqual(self.version.work_allocations.get(work=self.simple, month=JAN).quantity, 5)
            self.assertEqual(self.version.work_allocations.get(work=other, month=JAN).quantity, 10)
        self.assertEqual(ProjectWork.objects.filter(section__construction_object=self.obj, name='Simple').count(), 2)

    def test_same_work_twice_in_one_section_still_blocks_import(self):
        response = self.preview(file=upload(
            work_names=[('Simple', 'm', 2), ('Simple', 'm', 7)],
            sections=[self.simple.section.name, self.simple.section.name]))
        self.assertTrue(response.context['blocked'])
        self.assertContains(response, 'повторная позиция')

    def test_subwork_in_another_section_does_not_match_existing_composite(self):
        response = self.preview(file=upload(work_names=[('A', 'm', 2)], sections=['New section']))
        self.assertFalse(response.context['blocked'])
        row = next(r for r in response.context['sheets'][0]['entries'] if r['kind'] == 'work')
        self.assertIsNone(row['target_id'])
        self.assertEqual(row['target_type'], 'work')

    def test_batch_same_work_name_in_different_sections_is_not_a_conflict(self):
        response = self.batch_preview(JAN, date(2026, 1, 31), **{
            'files-0-file': upload(work_names=[('Simple', 'm', 2)], sections=[self.simple.section.name]),
            'files-1-file': upload(work_names=[('Simple', 'm', 7)], sections=['New section']),
        })
        # Resource rows target the same monthly cells; exclude them to isolate work identity.
        from django.core import signing
        from .meeting_import import SALT, apply_meeting_batch, batch_conflicts
        payload = signing.loads(response.context['preview'], salt=SALT)
        for batch in payload['batches']:
            for sheet in batch['sheets']:
                sheet['entries'] = [r for r in sheet['entries'] if r['kind'] == 'work']
        self.assertFalse(batch_conflicts(self.company, self.obj.project, payload['batches'], self.version))
        apply_meeting_batch(self.planner, self.obj.project_id, payload['batches'], self.version)
        rows = self.version.work_allocations.filter(work__name='Simple', month=JAN)
        self.assertEqual(set(rows.values_list('quantity', flat=True)), {Decimal(5), Decimal(10)})
