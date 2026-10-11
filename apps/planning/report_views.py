from datetime import date, timedelta
from calendar import monthrange
from django.shortcuts import render
from django.urls import reverse
from django.utils import timezone
from django.views import View
from django.core.exceptions import ValidationError
from core.permissions import READ_ROLES, require_roles
from .models import ProjectPlanVersion
from .report_forms import MatrixReportForm, SECTIONS
from .report_services import build_matrix


class MatrixReport(View):
    rolling_days = None
    journal_kind = None
    title = "План / факт"

    def build_report(self, user, filters):
        if self.journal_kind:
            from .journal_services import journal_matrix
            return journal_matrix(user, filters, self.journal_kind)
        if self.rolling_days:
            report = build_matrix(user, {**filters, "daily": True})
            report["daily"] = True
            return report
        return build_matrix(user, filters)

    def get(self, request):
        require_roles(request.user, READ_ROLES)
        params = request.GET.copy()
        year = timezone.localdate().year
        start, end = date(year, 1, 1), date(year, 12, 31)
        if self.journal_kind:
            today = timezone.localdate()
            start = today.replace(day=1)
            end = today.replace(day=monthrange(today.year, today.month)[1])
        selected = params.get("consolidated")
        if selected and selected.isdigit():
            parent = ProjectPlanVersion.objects.filter(
                pk=selected, company=request.user.company, status="FIXED"
            ).first()
            if parent:
                start, end = parent.start_date, parent.end_date
        if self.rolling_days:
            end = timezone.localdate()
            start = end - timedelta(days=self.rolling_days - 1)
            params["start"], params["end"] = start.isoformat(), end.isoformat()
            params.pop("month", None)
            params.pop("consolidated", None)
        params.setdefault("start", start.isoformat())
        params.setdefault("end", end.isoformat())
        params.setdefault("mode", "latest")
        if "sections_set" not in params:
            params.setlist("sections", [kind for kind, _ in SECTIONS])
        params["sections_set"] = "1"
        form = MatrixReportForm(params, user=request.user, version_statuses=None if self.journal_kind == "plans" else () if self.journal_kind == "facts" else ("APPROVED", "COMPLETED"))
        if self.rolling_days:
            for name in ("start", "end"):
                form.fields[name].widget.attrs["readonly"] = True
        context = {
            "overview": bool(self.rolling_days), "title": self.title, "journal_kind": self.journal_kind, "reset_url": request.path,
            "form": form,
            "version_fields": [form[name] for name in form.version_fields],
            "section_switches": [
                {
                    "kind": kind,
                    "label": label,
                    "checked": kind in params.getlist("sections"),
                }
                for kind, label in SECTIONS
            ],
            "shown": params.getlist("sections"),
        }
        if form.is_valid():
            try:
                report = self.build_report(request.user, form.cleaned_data)
            except ValidationError as exc:
                form.add_error(None, exc)
                return render(request, "planning/report_matrix.html", context)
            if request.GET.get("export") == "xlsx":
                from .report_export import matrix_workbook
                return matrix_workbook({**report, "start": form.cleaned_data["start"], "end": form.cleaned_data["end"]})
            context.update(report)
            context["consolidated"] = form.cleaned_data.get("consolidated")
            export = params.copy()
            export["export"] = "xlsx"
            context["export_url"] = request.path + "?" + export.urlencode()
            back = params.copy()
            back.pop("month", None)
            context["monthly_url"] = (
                request.path + "?" + back.urlencode()
            )
            for column in context["columns"]:
                drill = params.copy()
                drill["month"] = column["date"].replace(day=1).isoformat()
                column["url"] = (
                    request.path + "?" + drill.urlencode()
                )
        return render(request, "planning/report_matrix.html", context)


class PlanJournal(MatrixReport):
    journal_kind = "plans"
    title = "Журналы планов"


class FactJournal(MatrixReport):
    journal_kind = "facts"
    title = "Журналы фактов"


class OverviewReport(MatrixReport):
    rolling_days = 7
    title = "Обзор · План/Факт за последние 7 дней"
