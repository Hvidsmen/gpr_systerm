"""Physical machine catalogue filters and the latest recorded operating state."""
import re
from django import forms
from django.db import connections
from django.db.models import Case, CharField, OuterRef, Q, Subquery, Value, When
from django.db.models.functions import Coalesce
from django.utils import timezone
from .models import Equipment, EquipmentCategory, EquipmentType

STATES = [('READY', 'Исправна'), ('SCRAP', 'Под списание / на запчасти'), ('WRITTEN_OFF', 'Списана'), ('WORKING', 'Работает'), ('IDLE', 'Исправна, не задействована'), ('REPAIR', 'В ремонте'), ('UNKNOWN', 'Нет сведений')]


class UnitFilterForm(forms.Form):
    q = forms.CharField(label='Поиск машины', required=False, max_length=250,
        widget=forms.TextInput(attrs={'type': 'search', 'placeholder': 'Госномер, инвентарный номер, VIN, название или модель'}))
    parent = forms.ModelChoiceField(label='Техника для планирования', queryset=EquipmentType.objects.none(), required=False, empty_label='Вся техника')
    model = forms.ModelChoiceField(label='Модель', queryset=EquipmentType.objects.none(), required=False, empty_label='Все модели')
    category = forms.ModelChoiceField(label='Группа техники', queryset=EquipmentCategory.objects.none(), required=False, empty_label='Все группы')
    state = forms.ChoiceField(label='Последнее состояние', choices=[('', 'Все состояния'), *STATES], required=False)
    is_active = forms.ChoiceField(label='Доступность', choices=[('', 'Все машины'), ('True', 'Доступна для назначения'), ('False', 'Отключена')], required=False)
    site = forms.CharField(label='Участок', required=False, max_length=150)
    as_of = forms.DateField(label='Состояние на дату', required=False,
        widget=forms.DateInput(attrs={'type': 'date'}, format='%Y-%m-%d'))

    def __init__(self, *args, company, **kwargs):
        super().__init__(*args, **kwargs)
        if self.is_bound and not self.data.get('as_of'):
            self.data=self.data.copy();self.data['as_of']=timezone.localdate().isoformat()
        self.fields['parent'].queryset = EquipmentType.objects.filter(company=company, parent__isnull=True, merge_source__isnull=True)
        models = EquipmentType.objects.filter(company=company, parent__company=company, parent__isnull=False, merge_source__isnull=True).select_related('parent')
        parent = self.data.get('parent', '')
        if parent:
            models = models.filter(parent_id=parent) if str(parent).isdigit() else models.none()
        self.fields['model'].queryset = models
        self.fields['model'].label_from_instance = lambda model: f'{model.parent.name} · {model.brand_model or model.name}'
        from .group_order import ordered
        self.fields['category'].queryset = ordered(EquipmentCategory.objects.filter(company=company),kind='equipment')
        self.fields['as_of'].initial = timezone.localdate()
        for field in self.fields.values():
            field.widget.attrs['class'] = 'form-select form-select-sm' if isinstance(field.widget, forms.Select) else 'form-control form-control-sm'

    def clean_as_of(self):
        return self.cleaned_data.get('as_of') or timezone.localdate()


def with_unit_state(queryset, company, day=None):
    from apps.production.equipment_operations_models import EquipmentStateChange
    day = day or timezone.localdate()
    history = EquipmentStateChange.objects.filter(company=company, equipment_id=OuterRef('pk'),
        assignment__company=company, assignment__cancelled_at__isnull=True,
        is_cancelled=False, effective_date__lte=day).order_by('-effective_date', '-shift', '-pk')
    from apps.production.transport_models import EquipmentTransportEvent,EquipmentCampStay
    technical=EquipmentTransportEvent.objects.filter(company=company,equipment_id=OuterRef('pk'),kind='CONDITION',cancelled_at=None,date__lte=day).order_by('-date','-pk')
    disposal=EquipmentTransportEvent.objects.filter(company=company,equipment_id=OuterRef('pk'),kind='WRITEOFF',cancelled_at=None,date__lte=day).order_by('-date','-pk')
    stays=EquipmentCampStay.objects.filter(company=company,equipment_id=OuterRef('pk'),cancelled_at=None,arrived_on__lte=day).filter(Q(departed_on=None)|Q(departed_on__gt=day)).order_by('-arrived_on','-pk')
    return queryset.annotate(
        transport_camp_name=Subquery(stays.values('camp__name')[:1]),
        _transport_condition=Subquery(technical.values('condition')[:1]),
        _transport_condition_date=Subquery(technical.values('date')[:1]),
        _transport_disposal_date=Subquery(disposal.values('date')[:1]),
    ).annotate(
        unit_state=Case(When(_transport_disposal_date__isnull=False,then=Value('WRITTEN_OFF')),default=Coalesce('_transport_condition',Subquery(history.values('state')[:1]),Value('UNKNOWN')),output_field=CharField()),
        unit_state_date=Coalesce('_transport_disposal_date','_transport_condition_date',Subquery(history.values('effective_date')[:1])),
        unit_state_shift=Case(When(_transport_condition__isnull=False,then=Value(None)),When(_transport_disposal_date__isnull=False,then=Value(None)),default=Subquery(history.values('shift')[:1])),
        planning_type_name=Coalesce('type__parent__name', 'type__name'),
        group_name=Coalesce(Case(When(type__parent__isnull=False,then='type__parent__category__name'),default='type__category__name',output_field=CharField()),Value('Без группы')),
    ).annotate(unit_state_label=Case(*[When(unit_state=code, then=Value(label)) for code, label in STATES],
        default=Value('Нет сведений'), output_field=CharField()))


def unit_queryset(company, day=None):
    return with_unit_state(Equipment.objects.filter(company=company).select_related(
        'type__parent__category', 'type__category', 'catalogue_group','operator_position'), company, day)


def filter_units(queryset, form):
    if not form.is_valid():
        return queryset.none()
    data = form.cleaned_data
    if data['parent']:
        queryset = queryset.filter(Q(type=data['parent']) | Q(type__parent=data['parent']))
    if data['model']:
        queryset = queryset.filter(type=data['model'])
    if data['category']:
        queryset = queryset.filter(Q(type__parent__isnull=True,type__category=data['category'])|Q(type__parent__category=data['category']))
    if data['state']:
        queryset = queryset.filter(unit_state=data['state'])
    if data['is_active']:
        queryset = queryset.filter(is_active=data['is_active'] == 'True')
    lookup = 'iregex' if connections[queryset.db].vendor == 'sqlite' else 'icontains'
    for term in data['q'].split():
        value = re.escape(term) if lookup == 'iregex' else term
        match = Q()
        for name in ['plate_number', 'inventory_number', 'vin', 'name', 'brand_model', 'type__name', 'type__parent__name']:
            match |= Q(**{f'{name}__{lookup}': value})
        queryset = queryset.filter(match)
    if data['site']:
        value = re.escape(data['site']) if lookup == 'iregex' else data['site']
        queryset = queryset.filter(**{f'site_name__{lookup}': value})
    return queryset


def present_unit(unit):
    unit.planning_type = unit.type.parent if unit.type.parent_id else unit.type
    unit.model_reference = unit.type if unit.type.parent_id else None
    unit.display_model = unit.brand_model or (unit.type.brand_model or unit.type.name if unit.type.parent_id else unit.name)
    unit.identifier = unit.plate_number or unit.inventory_number or unit.vin or 'Номер не указан'
    unit.state_colour = {'READY':'success','SCRAP':'warning','WRITTEN_OFF':'secondary','WORKING': 'success', 'IDLE': 'warning', 'REPAIR': 'danger'}.get(unit.unit_state, 'secondary')
    return unit
