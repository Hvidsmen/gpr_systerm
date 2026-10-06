from django import forms
from django.core.exceptions import ValidationError
from core.permissions import scope_queryset

from apps.projects.models import ConstructionObject, Project, Section
from apps.works.models import ProjectWork, ProjectWorkItem
from .models import (
    DailyFact,
    LaborPlan,
    LaborFact,
    EquipmentPlan,
    EquipmentFact,
    FuelPlan,
    FuelFact,
)


class CompanyFormMixin:
    def __init__(self, *args, user=None, company=None, **kwargs):
        self.company = company or (user.company if user else None)
        super().__init__(*args, **kwargs)
        if self.company and hasattr(self, "instance"):
            self.instance.company = self.company
        for field in self.fields.values():
            field.widget.attrs.setdefault("class", "form-control")
            if isinstance(
                field, (forms.ModelChoiceField, forms.ModelMultipleChoiceField)
            ):
                if field.queryset is not None and any(
                    f.name == "company" for f in field.queryset.model._meta.fields
                ):
                    if not self.company:
                        field.queryset = field.queryset.none()
                    elif user:
                        field.queryset = scope_queryset(field.queryset, user)
                    else:
                        field.queryset = field.queryset.filter(company=self.company)


class DailyFactForm(CompanyFormMixin, forms.ModelForm):
    class Meta:
        model = DailyFact
        fields = [
            "project_work",
            "work_item",
            "date",
            "actual_quantity",
            "deviation_reason",
            "comment",
        ]
        widgets = {"date": forms.DateInput(format="%Y-%m-%d", attrs={"type": "date"})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["work_item"].label_from_instance = (
            lambda item: f"{item.project_work}: {item.name}"
        )

    def clean(self):
        data = super().clean()
        work, item = data.get("project_work"), data.get("work_item")
        if work:
            if work.kind == "SIMPLE" and item:
                self.add_error("work_item", "Простая работа не имеет подработ.")
            if work.kind == "COMPOSITE" and not item:
                self.add_error("work_item", "Для составной работы выберите подработу.")
            if item and item.project_work_id != work.pk:
                self.add_error("work_item", "Подработа принадлежит другой работе.")
        if data.get("actual_quantity") is not None and data["actual_quantity"] < 0:
            self.add_error("actual_quantity", "Факт не может быть отрицательным.")
        return data


RESOURCE_FIELDS = {
    "labor": ["brigade", "planned_workers", "planned_hours", "hourly_rate"],
    "equipment": [
        "equipment_type",
        "equipment_number",
        "planned_count",
        "planned_machine_hours",
        "hourly_rate",
    ],
    "fuel": ["fuel_type", "planned_liters", "price_per_liter"],
}
FACT_FIELDS = {
    "labor": ["brigade", "actual_workers", "actual_hours", "hourly_rate"],
    "equipment": [
        "equipment_type",
        "equipment_number",
        "actual_count",
        "machine_hours",
        "hourly_rate",
    ],
    "fuel": ["fuel_type", "actual_liters", "price_per_liter"],
}


class ResourceModelForm(CompanyFormMixin, forms.ModelForm):
    def validate_unique(self):
        if not isinstance(self.instance, (FuelPlan, FuelFact)):
            return super().validate_unique()
        exclude = self._get_validation_exclusions()
        exclude.discard('equipment_ref')
        try:
            self.instance.validate_unique(exclude=exclude)
        except ValidationError as error:
            self._update_errors(error)

    def clean(self):
        data = super().clean()
        for name, value in list(data.items()):
            if (
                isinstance(self.fields[name], (forms.IntegerField, forms.DecimalField))
                and value is not None
                and value < 0
            ):
                self.add_error(name, "Значение не может быть отрицательным.")
        return data


def resource_form(model, fields):
    meta = type(
        "Meta",
        (),
        {
            "model": model,
            "fields": ["construction_object", "date", *fields, "comment"],
            "widgets": {
                "date": forms.DateInput(format="%Y-%m-%d", attrs={"type": "date"})
            },
        },
    )
    return type(
        model.__name__ + "Form",
        (ResourceModelForm,),
        {"Meta": meta, "__module__": __name__},
    )


LaborPlanForm = resource_form(LaborPlan, RESOURCE_FIELDS["labor"])
EquipmentPlanForm = resource_form(EquipmentPlan, RESOURCE_FIELDS["equipment"])
FuelPlanForm = resource_form(FuelPlan, RESOURCE_FIELDS["fuel"])
LaborFactForm = resource_form(LaborFact, FACT_FIELDS["labor"])
EquipmentFactForm = resource_form(EquipmentFact, FACT_FIELDS["equipment"])
FuelFactForm = resource_form(FuelFact, FACT_FIELDS["fuel"])


class ResourceRangeMixin:
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields.pop("date")
        self.fields["date_start"] = forms.DateField(
            label="Дата начала",
            widget=forms.DateInput(format="%Y-%m-%d", attrs={"type": "date"}),
        )
        self.fields["date_end"] = forms.DateField(
            label="Дата окончания",
            widget=forms.DateInput(format="%Y-%m-%d", attrs={"type": "date"}),
        )

    def clean(self):
        data = super().clean()
        if (
            data.get("date_start")
            and data.get("date_end")
            and data["date_start"] > data["date_end"]
        ):
            raise ValidationError("Дата начала не может быть позже окончания.")
        return data

    def _post_clean(self):
        # Validate relation/numeric fields, excluding per-day unique checks until saving each date.
        from django.forms.models import construct_instance

        self.instance = construct_instance(
            self, self.instance, self._meta.fields, self._meta.exclude
        )
        try:
            self.instance.clean()
        except ValidationError as error:
            self._update_errors(error)


class LaborPlanRangeForm(ResourceRangeMixin, LaborPlanForm):
    pass


class EquipmentPlanRangeForm(ResourceRangeMixin, EquipmentPlanForm):
    pass


class FuelPlanRangeForm(ResourceRangeMixin, FuelPlanForm):
    pass


class ObjectDateForm(CompanyFormMixin, forms.Form):
    construction_object = forms.ModelChoiceField(
        queryset=ConstructionObject.objects.all(), label="Строительный объект"
    )
    date = forms.DateField(
        label="Дата", widget=forms.DateInput(format="%Y-%m-%d", attrs={"type": "date"})
    )


LaborFactDailyForm = EquipmentFactDailyForm = FuelFactDailyForm = ObjectDateForm


class FactInputFilterForm(CompanyFormMixin, forms.Form):
    date = forms.DateField(
        label="Дата", widget=forms.DateInput(format="%Y-%m-%d", attrs={"type": "date"})
    )
    construction_object = forms.ModelChoiceField(
        queryset=ConstructionObject.objects.all(), required=False, label="Объект"
    )
    project = forms.ModelChoiceField(
        queryset=Project.objects.all(), required=False, label="Проект"
    )
    section = forms.ModelChoiceField(
        queryset=Section.objects.all(), required=False, label="Раздел"
    )
