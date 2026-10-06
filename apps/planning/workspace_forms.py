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
    class Meta:
        model = WorkMonthAllocation
        fields = ["work", "month", "quantity", "load_profile"]
        labels = {"quantity": "Объём", "load_profile": "Профиль"}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        works = ProjectWork.objects.filter(
            company=self.company,
            section__construction_object=self.version.construction_object,
        )

        self.fields["work"].queryset = works
        self.fields["work"].label_from_instance = (
            lambda w: f"{w.code} — {w.name} ({w.unit}, {w.get_kind_display()})"
        )
        self.fields["quantity"].widget.attrs["min"] = "0"
        self.fields["quantity"].widget.attrs["step"] = "0.001"


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
            "equipment_ref",
            "count",
            "hours",
            "liters",
            "rate",
            "load_profile",
        ]


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
        queryset=ProjectWork.objects.all(), label="Работа для всех месяцев"
    )

    def __init__(self, *args, workspace, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["work"].queryset = self.fields["work"].queryset.filter(
            section__construction_object=workspace.construction_object
        )


RESOURCE_IDENTITIES = {
    'labor': ['brigade'],
    'equipment': ['equipment_type', 'equipment_number'],
    'fuel': ['fuel_type', 'equipment_ref'],
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
