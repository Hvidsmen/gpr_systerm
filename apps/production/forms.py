from django import forms
from django.core.exceptions import ValidationError
from core.permissions import scope_queryset

from apps.projects.models import ConstructionObject, Project, Section
from apps.works.models import ProjectWork, ProjectWorkItem
from .models import (
    DailyFact,
    DayActuation,
    LaborPlan,
    LaborFact,
    EquipmentPlan,
    EquipmentFact,
    FuelPlan,
    FuelFact,
)


class CompanyFormMixin:
    def __init__(self, *args, user=None, company=None, production_read=False, **kwargs):
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
                        field.queryset = scope_queryset(field.queryset, user, production_read=production_read)
                    else:
                        field.queryset = field.queryset.filter(company=self.company)
                    if field.queryset.model._meta.label_lower in {'resources.equipmenttype', 'resources.brigade'}:
                        field.queryset = field.queryset.filter(merge_source__isnull=True)


class DailyFactForm(CompanyFormMixin, forms.ModelForm):
    actuation_reasons = forms.MultipleChoiceField(label='Причины актировки',required=False,widget=forms.CheckboxSelectMultiple)
    actuation_reasons_present = forms.CharField(required=False,initial='1',widget=forms.HiddenInput)

    class Meta:
        model = DailyFact
        fields = [
            "project_work",
            "work_item",
            "date",
            "actual_quantity",
            "comment",
            "actuation_type",
            "actuation_reason",
        ]
        widgets = {"date": forms.DateInput(format="%Y-%m-%d", attrs={"type": "date"})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from . import actuation_reasons as reasons
        self.actuation_user=kwargs.get('user')
        retained=self.instance.actuation_reason_ids or []
        self.initial_actuation_reason_ids=list(retained)
        cached=getattr(self.actuation_user,'_actuation_reason_choices',None)
        if cached is None:
            cached=reasons.choices(self.company)
            if self.actuation_user:self.actuation_user._actuation_reason_choices=cached
        known={key for key,label in cached}
        extra=[(str(r.pk),r.label) for r in reasons.ActuationReason.objects.filter(company=self.company,pk__in=[k for k in retained if str(k) not in known])]
        self.fields['actuation_reasons'].choices=[*cached,*extra]
        self.initial['actuation_reasons']=[str(pk) for pk in retained]
        self.fields['actuation_reason'].widget=forms.HiddenInput()
        # Old clients may omit the new fields; never silently remove an existing actuation.
        if self.is_bound:
            self.data = self.data.copy()
            for name in ('actuation_type', 'actuation_reason'):
                if self.add_prefix(name) not in self.data and self.instance.pk:
                    self.data[self.add_prefix(name)] = getattr(self.instance, name)
            if not self.data.get(self.add_prefix('actuation_reasons_present')) and self.add_prefix('actuation_reasons') not in self.data and retained:
                if hasattr(self.data,'setlist'):self.data.setlist(self.add_prefix('actuation_reasons'),[str(pk) for pk in retained])
                else:self.data[self.add_prefix('actuation_reasons')]=[str(pk) for pk in retained]
            if self.data.get(self.add_prefix('actuation_type')) in dict(DayActuation.TYPES):
                self.data[self.add_prefix('actual_quantity')] = '0'
        self.fields['actuation_type'].choices = [('', 'Без актировки'), *DayActuation.TYPES]
        self.fields['project_work'].queryset=self.fields['project_work'].queryset.filter(merged_source__isnull=True)
        self.fields["work_item"].label_from_instance = (
            lambda item: f"{item.project_work}: {item.name}"
        )

    def clean(self):
        data = super().clean()
        work, item = data.get("project_work"), data.get("work_item")
        if work:
            if work.kind == "SIMPLE" and item:
                self.add_error("work_item", "Самостоятельная работа не имеет работ в составе комплекса.")
            if work.kind == "COMPOSITE" and not item:
                self.add_error("work_item", "Для комплекса работ выберите работу в составе комплекса.")
            if item and item.project_work_id != work.pk:
                self.add_error("work_item", "Работа в составе комплекса принадлежит другой работе.")
        selected=list(dict.fromkeys(int(pk) for pk in data.get('actuation_reasons',[])))
        self.instance.actuation_reason_ids=selected
        self.proposed_actuation_reason=None
        if self.initial_actuation_reason_ids and not selected and self.data.get(self.add_prefix('actuation_reasons_present')):
            data['actuation_reason']=''
        if data.get('actuation_type'):
            if selected:
                from .actuation_reasons import reason_text
                data['actuation_reason']=reason_text(self.company,selected)
            elif not data.get('actuation_reason', '').strip():
                self.add_error('actuation_reasons', 'Выберите причину актировки или добавьте новую.')
            elif data['actuation_reason']!=self.initial.get('actuation_reason',''):
                # Legacy forms can still submit text; it goes through the same review queue.
                self.proposed_actuation_reason=data['actuation_reason']
        else:
            data['actuation_reason'] = ''
            self.instance.actuation_reason_ids=[]
        if data.get("actual_quantity") is not None and data["actual_quantity"] < 0:
            self.add_error("actual_quantity", "Факт не может быть отрицательным.")
        return data

    def save(self, commit=True):
        from django.db import transaction
        with transaction.atomic():
            instance=super().save(commit=False)
            if getattr(self,'proposed_actuation_reason',None):
                from .actuation_reasons import propose
                if not self.actuation_user:raise ValidationError('Добавьте причину актировки через форму пользователя.')
                reason=propose(self.actuation_user,self.proposed_actuation_reason)
                instance.actuation_reason_ids=[reason.pk];instance.actuation_reason=reason.name
            if commit:instance.save();self.save_m2m()
            return instance


RESOURCE_FIELDS = {
    "labor": ["brigade", "planned_workers", "planned_hours", "hourly_rate"],
    "equipment": [
        "equipment_type",
        "equipment_number",
        "planned_count",
        "planned_machine_hours",
        "hourly_rate",
    ],
    "fuel": ["fuel_type", "planned_balance", "planned_liters"],
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
    "fuel": ["fuel_type", "actual_balance", "actual_liters"],
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
        for name in ("planned_balance", "actual_balance"):
            if name in self.fields and data.get(name) is None:
                data[name] = 0
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


class DayActuationForm(forms.Form):
    actuated = forms.BooleanField(required=False, label='Актировка объекта за день')
    actuation_type = forms.ChoiceField(required=False, label='Тип актировки', choices=[('', 'Выберите тип'), ('DAY', 'Дневная'), ('NIGHT', 'Ночная'), ('FULL_DAY', 'Суточная')])
    reason = forms.CharField(required=False, label='Причина актировки', widget=forms.Textarea(attrs={'rows': 2, 'class': 'form-control'}))

    def clean(self):
        data = super().clean()
        if data.get('actuated'):
            for name in ('actuation_type', 'reason'):
                if not data.get(name):
                    self.add_error(name, 'Обязательное поле при актировке.')
        return data
