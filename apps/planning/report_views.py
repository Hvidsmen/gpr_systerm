from datetime import date
from django.shortcuts import render
from django.urls import reverse
from django.utils import timezone
from django.views import View
from core.permissions import READ_ROLES, require_roles
from .models import ProjectPlanVersion
from .report_forms import MatrixReportForm, SECTIONS
from .report_services import build_matrix


class MatrixReport(View):
    def get(self, request):
        require_roles(request.user, READ_ROLES)
        params = request.GET.copy()
        year = timezone.localdate().year
        start, end = date(year, 1, 1), date(year, 12, 31)
        selected = params.get("consolidated")
        if selected and selected.isdigit():
            parent = ProjectPlanVersion.objects.filter(
                pk=selected, company=request.user.company, status="FIXED"
            ).first()
            if parent:
                start, end = parent.start_date, parent.end_date
        params.setdefault("start", start.isoformat())
        params.setdefault("end", end.isoformat())
        params.setdefault("mode", "latest")
        if "sections_set" not in params:
            params.setlist("sections", [kind for kind, _ in SECTIONS])
        params["sections_set"] = "1"
        form = MatrixReportForm(params, user=request.user)
        context = {
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
            report = build_matrix(request.user, form.cleaned_data)
            context.update(report)
            context["consolidated"] = form.cleaned_data.get("consolidated")
            back = params.copy()
            back.pop("month", None)
            context["monthly_url"] = (
                reverse("planning:report_matrix") + "?" + back.urlencode()
            )
            for column in context["columns"]:
                drill = params.copy()
                drill["month"] = column["date"].replace(day=1).isoformat()
                column["url"] = (
                    reverse("planning:report_matrix") + "?" + drill.urlencode()
                )
        return render(request, "planning/report_matrix.html", context)
