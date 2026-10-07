"""A dated matrix of individual, company-scoped fact records."""

from calendar import monthrange
from collections import OrderedDict
from datetime import timedelta
from decimal import Decimal

from django import forms
from django.utils import timezone
from core.permissions import scope_queryset
from apps.projects.models import ConstructionObject
from apps.works.models import ProjectWork


def default_period(day=None):
    today = day or timezone.localdate()
    return today.replace(day=1), today.replace(
        day=monthrange(today.year, today.month)[1]
    )


class FactMatrixFilterForm(forms.Form):
    construction_object = forms.ModelChoiceField(
        queryset=ConstructionObject.objects.none(),
        required=False,
        label="Строительный объект",
        empty_label="Все доступные объекты",
    )
    start = forms.DateField(
        label="Начало периода",
        widget=forms.DateInput(format="%Y-%m-%d", attrs={"type": "date"}),
    )
    end = forms.DateField(
        label="Конец периода",
        widget=forms.DateInput(format="%Y-%m-%d", attrs={"type": "date"}),
    )
    work = forms.ModelChoiceField(
        queryset=ProjectWork.objects.none(),
        required=False,
        label="Работа",
        empty_label="Все работы",
    )

    def __init__(self, *args, user, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["construction_object"].queryset = (
            scope_queryset(ConstructionObject.objects.all(), user)
            .select_related("project")
            .order_by("name", "pk")
        )
        self.fields["construction_object"].label_from_instance = (
            lambda obj: f"{obj.project.name} / {obj.name}"
        )
        works = (
            scope_queryset(ProjectWork.objects.all(), user)
            .select_related("section__construction_object")
            .order_by("name", "pk")
        )
        selected = self.data.get("construction_object")
        if selected:
            works = (
                works.filter(section__construction_object_id=selected)
                if str(selected).isdigit()
                else works.none()
            )
        self.fields["work"].queryset = works
        self.fields["work"].label_from_instance = (
            lambda work: f"{work.section.construction_object.name} / {work.name}"
        )
        for field in self.fields.values():
            field.widget.attrs["class"] = (
                "form-select form-select-sm"
                if isinstance(field, forms.ModelChoiceField)
                else "form-control form-control-sm"
            )

    def clean(self):
        values = super().clean()
        if values.get("start") and values.get("end"):
            if values["start"] > values["end"]:
                self.add_error("end", "Конец периода раньше начала.")
            elif (values["end"] - values["start"]).days > 365:
                self.add_error(
                    "end", "Для дневной матрицы выберите период до 366 дней."
                )
        return values


def fact_rows(facts, days):
    rows = OrderedDict()
    for fact in facts:
        identity = (fact.project_work_id, fact.work_item_id)
        if identity not in rows:
            work = fact.project_work
            rows[identity] = {
                "work": work,
                "item": fact.work_item,
                "object": work.section.construction_object,
                "unit": fact.work_item.unit if fact.work_item_id else work.unit,
                "by_day": {},
                "total": Decimal(0),
            }
        row = rows[identity]
        row["by_day"][fact.date] = fact
        row["total"] += fact.actual_quantity
    for row in rows.values():
        row["cells"] = [row["by_day"].get(day) for day in days]
    return list(rows.values())


def period_days(start, end):
    return [start + timedelta(days=index) for index in range((end - start).days + 1)]
