from django import forms
from django.forms import BaseModelFormSet, modelformset_factory
from apps.production.forms import CompanyFormMixin
from apps.projects.models import ConstructionObject
from apps.works.models import ProjectWork
from .models import WorkMonthAllocation, ResourceMonthAllocation
from .workspace_services import months_between


class WorkspaceForm(CompanyFormMixin, forms.Form):
    name = forms.CharField(label="Название плана", max_length=255)
    construction_object = forms.ModelChoiceField(
        queryset=ConstructionObject.objects.all(), label="Строительный объект"
    )
    start_date = forms.DateField(
        label="Начало периода",
        widget=forms.DateInput(format="%Y-%m-%d", attrs={"type": "date"}),
    )
    end_date = forms.DateField(
        label="Конец периода",
        widget=forms.DateInput(format="%Y-%m-%d", attrs={"type": "date"}),
    )

    def clean(self):
        values = super().clean()
        if values.get("start_date") and values.get("end_date"):
            if values["start_date"] > values["end_date"]:
                self.add_error("end_date", "Конец периода раньше начала.")
            elif len(months_between(values["start_date"], values["end_date"])) > 120:
                self.add_error("end_date", "Период не должен превышать десять лет.")
        return values


class ForecastForm(forms.Form):
    month = forms.ChoiceField(label="Планируемый месяц")
    scenario = forms.ChoiceField(
        label="Сценарий",
        choices=[
            ("REMAINING", "1. Прошлое — факт, будущее — остаток по базовым пропорциям"),
            ("BASELINE", "2. Прошлое и будущее — базовый план"),
        ],
    )

    def __init__(self, *args, workspace, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["month"].choices = [
            (month.isoformat(), month.strftime("%m.%Y"))
            for month in months_between(workspace.start_date, workspace.end_date)
        ]


class AllocationMixin(CompanyFormMixin):
    def __init__(self, *args, version, month, **kwargs):
        self.version = version
        self.month = month
        super().__init__(*args, **kwargs)
        self.instance.version = version
        self.instance.company = version.company
        self.fields["month"].initial = month
        self.fields["month"].widget = forms.HiddenInput()

    def clean_month(self):
        month = self.cleaned_data["month"]
        if month != self.month:
            raise forms.ValidationError("Месяц не совпадает с выбранным в редакторе.")
        return month


class WorkAllocationForm(AllocationMixin, forms.ModelForm):
    reset_item_quantities = forms.BooleanField(required=False, widget=forms.HiddenInput())
    class Meta:
        model = WorkMonthAllocation
        fields = ["work", "month", "quantity", "load_profile"]
        labels = {"quantity": "Объём", "load_profile": "Профиль"}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        works = ProjectWork.objects.filter(merged_source__isnull=True,
            company=self.company,
            section__construction_object=self.version.construction_object,
        )

        self.fields["work"].queryset = works
        self.fields["work"].label_from_instance = (
            lambda w: f"{w.name} ({w.unit}, {w.get_kind_display()})"
        )
        self.fields["quantity"].widget.attrs["min"] = "0"
        self.fields["quantity"].widget.attrs["step"] = "0.001"

    def clean(self):
        data = super().clean()
        if data.get('reset_item_quantities') or 'quantity' in self.changed_data or 'work' in self.changed_data:
            self.instance.item_quantities = {}
        if data.get('reset_item_quantities') or any(name in self.changed_data for name in ['work','month','quantity','load_profile']):
            self.instance.daily_override = None
        return data

    def _update_errors(self, errors):
        if hasattr(errors, 'error_dict') and 'item_quantities' in errors.error_dict:
            from django.core.exceptions import ValidationError
            data = dict(errors.error_dict)
            data.setdefault('__all__', []).extend(data.pop('item_quantities'))
            errors = ValidationError(data)
        super()._update_errors(errors)


class ResourceAllocationForm(AllocationMixin, forms.ModelForm):
    class Meta:
        model = ResourceMonthAllocation
        fields = [
            "month",
            "kind",
            "brigade",
            "equipment_type",
            "equipment_number",
            "fuel_type",
            "count",
            "hours",
            "balance",
            "liters",
            "rate",
        ]

    def clean_balance(self):
        return self.cleaned_data.get("balance") or 0


class AllocationFormset(BaseModelFormSet):
    def add_fields(self, form, index):
        super().add_fields(form, index)
        if "id" in form.fields:
            form.fields["id"].queryset = self.get_queryset()


WorkAllocationSet = modelformset_factory(
    WorkMonthAllocation,
    form=WorkAllocationForm,
    formset=AllocationFormset,
    extra=0,
    can_delete=True,
)
ResourceAllocationSet = modelformset_factory(
    ResourceMonthAllocation,
    form=ResourceAllocationForm,
    formset=AllocationFormset,
    extra=0,
    can_delete=True,
)


class AddWorkForm(CompanyFormMixin, forms.Form):
    work = forms.ModelChoiceField(
        queryset=ProjectWork.objects.filter(merged_source__isnull=True), label="Работа для всех месяцев"
    )

    def __init__(self, *args, workspace, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["work"].queryset = self.fields["work"].queryset.filter(
            section__construction_object=workspace.construction_object
        )


RESOURCE_IDENTITIES = {
    'labor': ['brigade'],
    'equipment': ['equipment_type', 'equipment_number'],
    'fuel': ['fuel_type'],
}


class AddPeriodResourceForm(ResourceAllocationForm):
    def __init__(self, *args, kind, **kwargs):
        super().__init__(*args, **kwargs)
        self.instance.kind = kind
        keep = ['kind', 'month', *RESOURCE_IDENTITIES[kind]]
        for name in list(self.fields):
            if name not in keep:
                self.fields.pop(name)
        self.fields['kind'].initial = kind
        self.fields['kind'].disabled = True
        self.fields['kind'].widget = forms.HiddenInput()
        self.fields['month'].disabled = True
        for name in RESOURCE_IDENTITIES[kind][:1]:
            self.fields[name].required = True
