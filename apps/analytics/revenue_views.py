import logging
from time import perf_counter
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
        started = perf_counter()
        logging.getLogger("gpr.report").warning("GPR revenue: preparing filters")
        params, form = dashboard_form(request)
        logging.getLogger("gpr.report").warning("GPR revenue: filters ready %.1fs", perf_counter()-started)
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
            def active_work(row):
                return any(cell.get("plan_quantity") or
                           (cell["date"] <= data["cutoff"] and cell.get("fact_quantity"))
                           or cell.get("plan") or
                           (cell["date"] <= data["cutoff"] and cell.get("fact"))
                           for cell in row["daily"])
            visible_objects = []
            for obj in data["objects"]:
                obj["rows"] = [row for row in obj["rows"] if active_work(row)]
                obj["groups"] = {name: visible for name, rows in obj["groups"].items()
                                 if (visible := [row for row in rows if active_work(row)])}
                if obj["rows"]:
                    visible_objects.append(obj)
            data["objects"] = visible_objects
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
            chart["selections"] = {}
            def selection(key, rows, label, owner, volume=False, unit="₽"):
                plan_key, fact_key = ("plan_quantity", "fact_quantity") if volume else ("plan", "fact")
                planned = actual = 0
                trend = {"labels": chart["labels"], "plan": [], "fact": [],
                         "label": label, "owner": str(owner), "unit": unit, "volume": volume}
                for index in range(len(chart["labels"])):
                    cells = [row["daily"][index] for row in rows]
                    plan_values = [cell[plan_key] for cell in cells if cell.get(plan_key) is not None]
                    fact_values = [cell[fact_key] for cell in cells if cell.get(fact_key) is not None]
                    planned += float(sum(plan_values))
                    actual += float(sum(fact_values))
                    trend["plan"].append(planned if plan_values else None)
                    trend["fact"].append(actual if fact_values and cells[0]["date"] <= data["cutoff"] else None)
                chart["selections"][key] = trend
            for obj in data["objects"]:
                selection(f"object-{obj['object'].pk}", [obj], obj["name"], obj["object"].pk)
                for index, (name, rows) in enumerate(obj["groups"].items()):
                    key = f"group-{obj['object'].pk}-{index}"
                    selection(key, rows, obj["name"] + " · " + name, obj["object"].pk)
                    for row in rows:
                        row["group_key"] = key
                        row["chart_key"] = f"work-{obj['object'].pk}-{row['id']}"
                        volume = not row["has_price"]
                        selection(row["chart_key"], [row], obj["name"] + " · " + row["name"], obj["object"].pk, volume, row["unit"] if volume else "₽")
            def share_entry(key, name):
                trend = chart["selections"][key]
                return {"key": key, "name": name,
                        "plan": next((v for v in reversed(trend["plan"]) if v is not None), 0),
                        "fact": next((v for v in reversed(trend["fact"]) if v is not None), 0)}
            chart["shares"] = []
            for obj in data["objects"]:
                object_key = f"object-{obj['object'].pk}"
                chart["shares"].append(share_entry(object_key, obj["name"]))
                object_shares = []
                for rows in obj["groups"].values():
                    group_key = rows[0]["group_key"]
                    object_shares.append(share_entry(group_key, rows[0]["group"]))
                    chart["selections"][group_key]["shares"] = [
                        share_entry(row["chart_key"], row["name"]) for row in rows if row["has_price"]]
                    for row in rows:
                        chart["selections"][row["chart_key"]]["shares"] = [share_entry(row["chart_key"], row["name"])]
                chart["selections"][object_key]["shares"] = object_shares
            if context.get("detail"):
                trend = context["detail"]["days"]
                chart["detail"] = {
                    "labels": [d["date"].strftime("%d.%m.%Y") for d in trend],
                    "plan": [float(d["cumulative_plan"]) for d in trend],
                    "fact": [float(d["cumulative_fact"]) for d in trend],
                }
            logging.getLogger("gpr.report").warning("GPR revenue: charts ready %.1fs; selections=%s", perf_counter()-started, len(chart["selections"]))
            context["chart"] = chart
            export = params.copy()
            export.pop("work", None)
            context["export_url"] = (
                reverse("dashboard:revenue_export") + "?" + export.urlencode()
            )
        logging.getLogger("gpr.report").warning("GPR revenue: rendering page %.1fs", perf_counter()-started)
        response = render(request, "analytics/revenue.html", context)
        logging.getLogger("gpr.report").warning("GPR revenue: response ready %.1fs; bytes=%s", perf_counter()-started, len(response.content))
        return response


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
                "Раздел", "Единица", "Объём: план периода", "Объём: план на дату", "Объём: факт на дату",
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
                                "group", "unit", "volume_plan", "volume_plan_to_date", "volume_fact",
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
