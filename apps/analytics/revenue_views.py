from calendar import monthrange
from datetime import date
from django.core.exceptions import PermissionDenied
from django.http import HttpResponse
from django.shortcuts import render
from django.urls import reverse
from django.utils import timezone
from django.views import View
from core.permissions import READ_ROLES, require_roles
from apps.planning.report_forms import MatrixReportForm
from apps.planning.models import ProjectPlanVersion
from .revenue import build_dashboard
from .revenue_detail import work_detail


def dashboard_form(request):
    params = request.GET.copy()
    today = timezone.localdate()
    default_start, default_end = today.replace(day=1), today.replace(
        day=monthrange(today.year, today.month)[1]
    )
    if params.get("consolidated", "").isdigit():
        parent = ProjectPlanVersion.objects.filter(
            pk=params["consolidated"], company=request.user.company, status="FIXED"
        ).first()
        if parent:
            default_start, default_end = parent.start_date, parent.end_date
    params.setdefault("start", default_start.isoformat())
    params.setdefault("end", default_end.isoformat())
    params.setdefault("mode", "latest")
    params.pop("month", None)
    params.setlist("sections", ["works", "labor", "equipment", "fuel"])
    form = MatrixReportForm(params, user=request.user)
    return params, form


class RevenueDashboard(View):
    def get(self, request):
        require_roles(request.user, READ_ROLES)
        params, form = dashboard_form(request)
        context = {"form": form, "versions": [form[n] for n in form.version_fields]}
        if form.is_valid():
            data = build_dashboard(request.user, form.cleaned_data)
            for obj in data["objects"]:
                drill = params.copy()
                drill.setlist("objects", [str(obj["object"].pk)])
                drill.pop("work", None)
                obj["url"] = reverse("dashboard:revenue") + "?" + drill.urlencode()
                for row in obj["rows"]:
                    query = params.copy()
                    query["work"] = str(row["id"])
                    row["url"] = (
                        reverse("dashboard:revenue")
                        + "?"
                        + query.urlencode()
                        + "#work-detail"
                    )
            work_id = params.get("work")
            if work_id:
                row = next((w for w in data["works"] if str(w["id"]) == work_id), None)
                if row is None:
                    raise PermissionDenied(
                        "Работа вне выбранных объектов или фильтров."
                    )
                context["detail"] = work_detail(
                    request.user, row, data["start"], data["cutoff"]
                )
            context.update(data)
            chart = {
                "labels": [c["date"].strftime("%d.%m.%Y") for c in data["daily"]],
                "plan": [],
                "fact": [],
                "objects": [],
                "works": [],
            }
            p = f = 0
            for cell in data["daily"]:
                if cell["plan"] is not None:
                    p += float(cell["plan"])
                if cell["fact"] is not None:
                    f += float(cell["fact"])
                chart["plan"].append(p if cell["plan"] is not None else None)
                chart["fact"].append(
                    f if cell["date"] <= data["cutoff"] and not data["future"] else None
                )
            for obj in data["objects"]:
                chart["objects"].append(
                    {
                        "name": obj["name"],
                        "plan": float(obj["plan"] or 0),
                        "fact": float(obj["fact"]),
                        "url": obj["url"],
                    }
                )
                for row in obj["rows"]:
                    chart["works"].append(
                        {
                            "name": row["name"] + " · " + obj["name"],
                            "plan": float(row["plan"] or 0),
                            "fact": float(row["fact"]),
                            "url": row["url"],
                        }
                    )
            if context.get("detail"):
                trend = context["detail"]["days"]
                chart["detail"] = {
                    "labels": [d["date"].strftime("%d.%m.%Y") for d in trend],
                    "plan": [float(d["cumulative_plan"]) for d in trend],
                    "fact": [float(d["cumulative_fact"]) for d in trend],
                }
            context["chart"] = chart
            export = params.copy()
            export.pop("work", None)
            context["export_url"] = (
                reverse("dashboard:revenue_export") + "?" + export.urlencode()
            )
        return render(request, "analytics/revenue.html", context)


class RevenueExport(View):
    def get(self, request):
        require_roles(request.user, READ_ROLES)
        _, form = dashboard_form(request)
        if not form.is_valid():
            return HttpResponse("Неверные фильтры", status=400)
        from openpyxl import Workbook
        from io import BytesIO

        data = build_dashboard(request.user, form.cleaned_data)
        workbook = Workbook()
        sheet = workbook.active
        sheet.title = "Выручка"
        sheet.append(
            [
                "Период",
                str(data["start"]),
                str(data["end"]),
                "Дата среза",
                str(data["cutoff"]),
            ]
        )
        sheet.append(
            [
                "Объект",
                "Работа",
                "План периода, ₽",
                "План на дату, ₽",
                "Факт на дату, ₽",
                "Выполнение на дату, %",
                "Выполнение периода, %",
                "Отклонение на дату, ₽",
            ]
        )
        for obj in data["objects"]:
            for row in [obj, *obj["rows"]]:
                sheet.append(
                    [
                        obj["name"],
                        row["name"] if row is not obj else "Итого по объекту",
                        *[
                            row.get(k)
                            for k in [
                                "plan",
                                "plan_to_date",
                                "fact",
                                "percent_date",
                                "percent_full",
                                "delta",
                            ]
                        ],
                    ]
                )
        resources = workbook.create_sheet("Ресурсы")
        resources.append(["Дата среза", str(data["cutoff"])])
        resources.append(["Объект", "Ресурс", "План", "Факт", "Отклонение", "Единица"])
        for obj in data["resource_objects"]:
            for row in obj["rows"]:
                resources.append(
                    [
                        obj["name"],
                        row["label"],
                        row["cell"]["plan"],
                        row["cell"]["fact"],
                        row["cell"]["delta"],
                        row["unit"],
                    ]
                )
        # Spreadsheet text remains literal even when user names begin with '='.
        for sheet in workbook:
            sheet.freeze_panes = "C3"
            for row in sheet:
                for cell in row:
                    if isinstance(cell.value, str):
                        cell.data_type = "s"
            for col in ["A", "B"]:
                sheet.column_dimensions[col].width = 40
        stream = BytesIO()
        workbook.save(stream)
        response = HttpResponse(
            stream.getvalue(),
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        response["Content-Disposition"] = 'attachment; filename="revenue.xlsx"'
        return response
