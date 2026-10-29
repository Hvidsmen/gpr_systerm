from django import forms
from django.forms import BaseModelFormSet, modelformset_factory
from apps.production.forms import CompanyFormMixin
from apps.projects.models import ConstructionObject
from apps.works.models import ProjectWork
from .models import WorkMonthAllocation, ResourceMonthAllocation
from .workspace_services import months_between
from apps.works.planning_access import planning_works,validate_plan_addition


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
            ("ENTERED", "3. Прошлое — факт, будущее — только введенные объемы (без переноса остатков)"),
        ],
    )

    def __init__(self, *args, workspace, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["month"].choices = [
            (month.isoformat(), month.strftime("%m.%Y"))
            for month in months_between(workspace.continuation_start or workspace.start_date, workspace.end_date)
        ]


class AllocationMixin(CompanyFormMixin):
    def __init__(self, *args, version, month, user=None, **kwargs):
        self.version = version
        self.month = month
        self.user=user
        super().__init__(*args, user=user, **kwargs)
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
    edited_item_quantities = forms.JSONField(required=False, widget=forms.HiddenInput())
    reset_item_quantities = forms.BooleanField(required=False, widget=forms.HiddenInput())
    class Meta:
        model = WorkMonthAllocation
        fields = ["work", "month", "quantity", "load_profile", "selected_item_ids"]
        widgets = {"selected_item_ids": forms.HiddenInput()}
        labels = {"quantity": "Объём", "load_profile": "Профиль"}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        works = ProjectWork.objects.filter(merged_source__isnull=True,
            company=self.company,
            section__construction_object=self.version.construction_object,
        )

        from django.db.models import Q
        available=planning_works(works,self.user,whole=True)
        self.fields["work"].queryset = works.filter(Q(pk__in=available.values("pk"))|Q(pk=self.instance.work_id if self.instance.pk else None)).select_related("complex_object")
        self.fields["work"].label_from_instance = (
            lambda w: f"{str(w.complex_object) + ' → ' if w.complex_object_id else ''}{w.name} ({w.unit}, {w.get_kind_display()})"
        )
        self.fields["quantity"].widget.attrs["min"] = "0"
        self.fields["quantity"].widget.attrs["step"] = "0.001"

    def clean(self):
        data = super().clean()
        if data.get('reset_item_quantities') or 'quantity' in self.changed_data or 'work' in self.changed_data:
            self.instance.item_quantities = {}
        data['selected_item_ids']=(data.get('selected_item_ids') or []) if self.add_prefix('selected_item_ids') in self.data else self.instance.selected_item_ids
        if 'work' in self.changed_data and self.instance.pk:data['selected_item_ids']=[]
        if data.get('work'):
            existing=WorkMonthAllocation.objects.get(pk=self.instance.pk) if self.instance.pk else None
            try:validate_plan_addition(data['work'],data['selected_item_ids'],self.user,existing)
            except forms.ValidationError as error:self.add_error(None,error)
        edited = data.get('edited_item_quantities')
        if edited is not None:
            if not isinstance(edited, dict) or not edited:
                self.add_error(None, 'Укажите объёмы всех работ в составе комплекса.')
            else:
                self.instance.item_quantities = edited
        if 'edited_item_quantities' in self.errors:
            self.add_error(None, 'Некорректные объёмы работ в составе комплекса.')
        if edited is not None or data.get('reset_item_quantities') or any(name in self.changed_data for name in ['work','month','quantity','load_profile','selected_item_ids']):
            self.instance.daily_override = None
        return data

    def _update_errors(self, errors):
        if hasattr(errors, 'error_dict') and any(key in errors.error_dict for key in ['item_quantities','selected_item_ids']):
            from django.core.exceptions import ValidationError
            data = dict(errors.error_dict)
            for key in ['item_quantities','selected_item_ids']:
                data.setdefault('__all__', []).extend(data.pop(key,[]))
            errors = ValidationError(data)
        super()._update_errors(errors)


class ResourceAllocationForm(AllocationMixin, forms.ModelForm):
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        from apps.resources.equipment_levels import gpr_types
        from django.db.models import Q
        field=self.fields['equipment_type']
        field.queryset=field.queryset.filter(Q(pk__in=gpr_types(field.queryset).values('pk'))|Q(pk=self.instance.equipment_type_id))
        from apps.resources.position_catalog import planning_brigades
        field=self.fields['brigade']
        current_brigade_id=self.instance.brigade_id if self.instance.pk else None
        field.queryset=field.queryset.filter(Q(pk__in=planning_brigades(self.company).values('pk'))|Q(pk=current_brigade_id))

    class Meta:
        model = ResourceMonthAllocation
        labels = {'brigade':'Должность'}
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
    def __init__(self, *args, **kwargs):
        self._choice_cache = {}
        query=kwargs.get('queryset')
        if query is not None:
            if query.model is WorkMonthAllocation:
                kwargs['queryset']=query.select_related('work__section','work__complex_object','load_profile').prefetch_related('work__items')
            else:
                kwargs['queryset']=query.select_related('brigade__group','equipment_type__category','equipment_type__parent__category')
        super().__init__(*args, **kwargs)

    def add_fields(self, form, index):
        super().add_fields(form, index)
        # Every row has the same company-scoped catalogues. Render their choices
        # once per request, while keeping each field's queryset for POST validation.
        for name, field in form.fields.items():
            if isinstance(field, forms.ModelChoiceField) and not field.widget.is_hidden:
                field.widget.attrs['data-native-select']='true'
                if name in {'work','brigade','equipment_type'} and form.instance.pk and not form.is_bound:
                    # The visible editor changes quantities; identity changes use
                    # its add/remove actions. Keep only this saved identity in the
                    # hidden select, retaining the full queryset for POST validation.
                    from django.forms.models import ModelChoiceIteratorValue
                    item=getattr(form.instance,name)
                    field.choices=[('',field.empty_label)] if field.empty_label is not None else []
                    if item is not None:
                        field.choices=[*field.choices,(ModelChoiceIteratorValue(item.pk,item),field.label_from_instance(item))]
                    continue
                # A saved row may retain a position without current employees.
                identity={'brigade':getattr(form.instance,'brigade_id',None),'work':getattr(form.instance,'work_id',None),'equipment_type':getattr(form.instance,'equipment_type_id',None)}
                key=(name,identity[name] if form.instance.pk else None) if name in identity else name
                if key not in self._choice_cache:
                    self._choice_cache[key] = list(field.choices)
                field.choices = self._choice_cache[key]
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

    def __init__(self, *args, workspace, user=None, **kwargs):
        super().__init__(*args, user=user, **kwargs)
        self.fields["work"].queryset = planning_works(self.fields["work"].queryset,user,whole=True).filter(
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
