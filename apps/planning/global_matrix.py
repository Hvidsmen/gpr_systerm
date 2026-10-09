"""Read-only matrices for a specific global version, including draft previews."""

from django import forms
from django.core.exceptions import ValidationError
from django.urls import reverse
from apps.projects.models import ConstructionObject
from apps.works.models import WorkGroup
from apps.resources.models import BrigadeGroup, BrigadeMacroGroup, EquipmentCategory
from .global_services import build_snapshot
from .report_forms import MatrixReportForm, SECTIONS
from .report_services import Source, build_matrix


class GlobalMatrixForm(MatrixReportForm):
    def __init__(self, *args, user, version, **kwargs):
        # Use the shared fields/validation without report-wide version selectors.
        forms.Form.__init__(self, *args, **kwargs)
        self.version = version
        self.version_fields = []
        for name in ["project", "objects", "mode", "consolidated"]:
            del self.fields[name]
        for name, model in [
            ("work_group", WorkGroup),
            ("macro_group", BrigadeMacroGroup),
            ("brigade_group", BrigadeGroup),
            ("equipment_category", EquipmentCategory),
        ]:
            self.fields[name].queryset = model.objects.filter(company=user.company)
        for name, field in self.fields.items():
            if name not in ["month", "sections"]:
                field.widget.attrs["class"] = (
                    "form-select form-select-sm"
                    if isinstance(field, forms.ChoiceField)
                    else "form-control form-control-sm"
                )
        self.fields["start"].widget.attrs["min"] = version.start_date.isoformat()
        self.fields["end"].widget.attrs["max"] = version.end_date.isoformat()

    def clean(self):
        data = super().clean()
        if data.get("start") and data["start"] < self.version.start_date:
            self.add_error("start", "Выберите дату внутри периода версии.")
        if data.get("end") and data["end"] > self.version.end_date:
            self.add_error("end", "Выберите дату внутри периода версии.")
        return data


def detail_matrix(request, version):
    params = request.GET.copy()
    params.setdefault("start", version.start_date.isoformat())
    params.setdefault("end", version.end_date.isoformat())
    if "sections_set" not in params:
        params.setlist("sections", [kind for kind, _ in SECTIONS])
    params["sections_set"] = "1"
    form = GlobalMatrixForm(params, user=request.user, version=version)
    context = {
        "form": form,
        "shown": params.getlist("sections"),
        "section_switches": [
            {
                "kind": kind,
                "label": label,
                "checked": kind in params.getlist("sections"),
            }
            for kind, label in SECTIONS
        ],
    }
    if not form.is_valid():
        return context
    try:
        snapshot = (
            build_snapshot(version, validate_inputs=False)
            if version.status in ["DRAFT", "REJECTED"]
            else version.snapshot
        )
    except ValidationError as exc:
        context["matrix_error"] = "; ".join(exc.messages)
        return context
    filters = {
        **form.cleaned_data,
        "objects": ConstructionObject.objects.filter(
            company=request.user.company, pk=version.construction_object_id
        ),
    }
    context.update(
        build_matrix(
            request.user,
            filters,
            plan_only=True,
            source_overrides={
                version.construction_object_id: [
                    Source(version, snapshot, version.start_date, version.end_date)
                ],
            },
        )
    )
    context["matrix_warnings"] = snapshot.get("warnings", [])
    url = reverse("planning:global_detail", args=[version.pk])
    back = params.copy()
    back.pop("month", None)
    context["monthly_url"] = url + "?" + back.urlencode()
    for column in context["columns"]:
        drill = params.copy()
        drill["month"] = column["date"].replace(day=1).isoformat()
        column["url"] = url + "?" + drill.urlencode()
    return context
