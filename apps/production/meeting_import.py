"""Daily facts from the meeting workbook, with a signed, scoped preview."""

from calendar import monthrange
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from uuid import uuid4

from django import forms
from django.contrib import messages
from django.core import signing
from django.core.exceptions import ValidationError
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views import View

from core.permissions import FACT_ROLES, require_roles, scope_queryset
from apps.projects.models import ConstructionObject, Section
from apps.resources.models import (
    Brigade,
    BrigadeGroup,
    EquipmentType,
    EquipmentCategory,
)
from apps.works.models import ProjectWork, ProjectWorkItem, MeasurementUnit
from apps.planning.models import LoadProfile, LoadProfileItem
from apps.planning.meeting_import import (
    parse_meeting_workbook,
    resolve_sheet,
    unique_match,
)
from .models import DailyFact, LaborFact, EquipmentFact

SALT = "production.meeting-facts.v1"


def objects_for(user):
    return scope_queryset(ConstructionObject.objects.select_related("project"), user)


class FactMeetingForm(forms.Form):
    construction_object = forms.ModelChoiceField(
        queryset=ConstructionObject.objects.none(), label="Строительный объект"
    )
    start = forms.DateField(
        label="Начало периода",
        widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
    )
    end = forms.DateField(
        label="Конец периода",
        widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
    )
    file = forms.FileField(label="Файл совещания (.xlsx)")
    existing = forms.ChoiceField(
        label="Уже введённые факты",
        choices=[
            ("keep", "Сохранить существующие"),
            ("replace", "Заменить значениями файла"),
        ],
    )

    def __init__(self, *args, user, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["construction_object"].queryset = objects_for(user)
        for field in self.fields.values():
            field.widget.attrs["class"] = (
                "form-select"
                if isinstance(field, forms.ChoiceField)
                else "form-control"
            )

    def clean(self):
        data = super().clean()
        if data.get("start") and data.get("end"):
            if data["end"] < data["start"] or (data["end"] - data["start"]).days > 730:
                raise ValidationError("Выберите период от одного дня до двух лет.")
        return data


def fact_identity(obj, row):
    day = date.fromisoformat(row["month"])
    if row["kind"] == "work":
        item = (
            ProjectWorkItem.objects.get(pk=row["target_id"], company=obj.company)
            if row["target_type"] == "item"
            else None
        )
        return (
            DailyFact,
            {
                "project_work_id": item.project_work_id if item else row["target_id"],
                "work_item_id": item.pk if item else None,
                "date": day,
            },
            "actual_quantity",
        )
    if row["kind"] == "labor":
        return (
            LaborFact,
            {"construction_object": obj, "brigade_id": row["target_id"], "date": day},
            "actual_workers",
        )
    return (
        EquipmentFact,
        {
            "construction_object": obj,
            "equipment_type_id": row["target_id"],
            "equipment_number": row["equipment_number"],
            "date": day,
        },
        "actual_count",
    )


def resolved_preview(user, obj, sheet):
    if sheet["errors"]:
        raise ValidationError(sheet["errors"])
    _, rows = resolve_sheet(user.company, obj.project, sheet, obj, require_all=False)
    if len(rows) > 20000:
        raise ValidationError("Слишком много фактов. Разделите период загрузки.")
    for row in rows:
        if row["kind"] == "work":
            row["quantity"] = str(
                Decimal(row["quantity"]).quantize(
                    Decimal(".001"), rounding=ROUND_HALF_UP
                )
            )
        if row["kind"] == "work" and Decimal(row["quantity"]) > Decimal(
            "999999999.999"
        ):
            raise ValidationError(
                f'Строка {row["row"]}: объём превышает допустимый размер факта.'
            )
        row["before"] = None
        if row["target_id"]:
            model, lookup, field = fact_identity(obj, row)
            current = model.objects.filter(company=user.company, **lookup).first()
            if current:
                row["before"] = {
                    "id": current.pk,
                    "quantity": str(getattr(current, field)),
                    "updated": str(current.updated_at),
                }
        row["operation"] = "Существующий факт" if row["before"] else "Новый факт"
    return rows


def create_missing(user, obj, row):
    if row["kind"] == "work":
        section = unique_match(
            Section.objects.filter(company=user.company, construction_object=obj),
            row["section"],
            "Раздел",
        )
        if section is None:
            section = Section.objects.create(
                company=user.company, construction_object=obj, name=row["section"]
            )
        profile = LoadProfile.objects.create(
            company=user.company, name="Равномерный — импорт факта"
        )
        LoadProfileItem.objects.create(
            company=user.company, profile=profile, workday_number=1, percentage=100
        )
        MeasurementUnit.objects.get_or_create(
            company=user.company, symbol=row["unit"], defaults={"name": row["unit"]}
        )
        target = ProjectWork(
            company=user.company,
            section=section,
            name=row["name"],
            unit=row["unit"],
            load_profile=profile,
        )
        target._price_actor = user
    else:
        labor = row["kind"] == "labor"
        extra = {}
        if row["section"]:
            group_model = BrigadeGroup if labor else EquipmentCategory
            group = unique_match(
                group_model.objects.filter(company=user.company),
                row["section"],
                "Группа",
            )
            if group is None:
                group = group_model.objects.create(
                    company=user.company, name=row["section"]
                )
            extra["group" if labor else "category"] = group
        target = (Brigade if labor else EquipmentType)(
            company=user.company, name=row["name"], **extra
        )
    target.full_clean()
    target.save()
    return target.pk


@transaction.atomic
def apply_facts(user, payload):
    require_roles(user, FACT_ROLES)
    obj = get_object_or_404(objects_for(user).select_for_update(), pk=payload["object"])
    rows = resolved_preview(user, obj, payload["sheet"])
    if rows != payload["rows"]:
        raise ValidationError(
            "Данные изменились после предварительного просмотра. Загрузите файл заново."
        )
    created_targets = {}
    written = skipped = 0
    for row in rows:
        day = date.fromisoformat(row["month"])
        if (
            not date.fromisoformat(payload["start"])
            <= day
            <= date.fromisoformat(payload["end"])
        ):
            raise ValidationError("Дата факта за пределами выбранного периода.")
        if row["before"] and payload["existing"] == "keep":
            skipped += 1
            continue
        if row["target_id"] is None:
            identity = (row["kind"], row["section"], row["name"], row["unit"])
            if identity not in created_targets:
                created_targets[identity] = create_missing(user, obj, row)
            row["target_id"] = created_targets[identity]
        model, lookup, field = fact_identity(obj, row)
        fact = (
            model.objects.select_for_update()
            .filter(company=user.company, **lookup)
            .first()
        )
        if fact is None:
            fact = model(company=user.company, **lookup)
        setattr(
            fact,
            field,
            Decimal(row["quantity"]) if model == DailyFact else int(row["quantity"]),
        )
        if model == DailyFact:
            fact.reported_by = user
        fact.full_clean(exclude=["actual_value"] if model == DailyFact else [])
        fact.save()
        written += 1
    return written, skipped


class FactMeetingImportView(View):
    template_name = "production/meeting_import.html"

    def get(self, request):
        require_roles(request.user, FACT_ROLES)
        start = timezone.localdate().replace(day=1)
        form = FactMeetingForm(
            user=request.user,
            initial={
                "start": start,
                "end": start.replace(day=monthrange(start.year, start.month)[1]),
                "construction_object": request.GET.get("construction_object"),
            },
        )
        return render(request, self.template_name, {"form": form})

    def post(self, request):
        require_roles(request.user, FACT_ROLES)
        if request.POST.get("action") == "confirm":
            try:
                payload = signing.loads(
                    request.POST.get("preview", ""), salt=SALT, max_age=3600
                )
                if (
                    payload["user"] != request.user.pk
                    or payload["company"] != request.user.company_id
                    or payload["nonce"] != request.session.get("fact_import_nonce")
                ):
                    raise signing.BadSignature()
                written, skipped = apply_facts(request.user, payload)
                request.session.pop("fact_import_nonce", None)
                messages.success(
                    request,
                    f"Факты загружены: {written}. Сохранены существующие: {skipped}.",
                )
                return redirect("production:fact_day_workspace")
            except signing.BadSignature:
                messages.error(
                    request,
                    "Предварительный просмотр устарел или уже использован. Загрузите файл заново.",
                )
            except ValidationError as error:
                messages.error(request, "; ".join(error.messages))
            return redirect("production:fact_meeting_import")
        form = FactMeetingForm(request.POST, request.FILES, user=request.user)
        if form.is_valid():
            obj = form.cleaned_data["construction_object"]
            try:
                sheet = parse_meeting_workbook(
                    form.cleaned_data["file"],
                    form.cleaned_data["start"],
                    form.cleaned_data["end"],
                    object_name=obj.name,
                    facts=True,
                )[0]
                rows = resolved_preview(request.user, obj, sheet)
                nonce = uuid4().hex
                request.session["fact_import_nonce"] = nonce
                payload = {
                    "user": request.user.pk,
                    "company": request.user.company_id,
                    "object": obj.pk,
                    "start": form.cleaned_data["start"].isoformat(),
                    "end": form.cleaned_data["end"].isoformat(),
                    "sheet": sheet,
                    "rows": rows,
                    "existing": form.cleaned_data["existing"],
                    "nonce": nonce,
                }
                return render(
                    request,
                    self.template_name,
                    {
                        "object": obj,
                        "start": form.cleaned_data["start"],
                        "end": form.cleaned_data["end"],
                        "rows": rows,
                        "warnings": sheet["warnings"],
                        "existing": form.cleaned_data["existing"],
                        "preview": signing.dumps(payload, salt=SALT, compress=True),
                    },
                )
            except ValidationError as error:
                form.add_error(None, error)
        return render(request, self.template_name, {"form": form})
