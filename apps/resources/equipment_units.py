from django import forms
from django.contrib import messages
from django.shortcuts import render,redirect,get_object_or_404
from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.http import HttpResponse
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET
from urllib.parse import parse_qsl, urlencode
from core.permissions import READ_ROLES,PLAN_ROLES,require_roles,role_code
from .models import Equipment,EquipmentType,EquipmentCategory
from .equipment_unit_catalogue import UnitFilterForm, filter_units, present_unit, unit_queryset

class UnitForm(forms.ModelForm):
    model = forms.ModelChoiceField(label='Модель из справочника', queryset=EquipmentType.objects.none(), required=False,
        empty_label='Без отдельной модели')
    class Meta:
        model=Equipment
        fields=['type','catalogue_group','plate_number','name','brand_model','manufacture_year','inventory_number','vin','site_name','operator_position','is_active']
        labels={'type':'Техника для планирования','plate_number':'Госномер / регистрационный знак','name':'Название машины','is_active':'Доступна для назначения'}
    def __init__(self,*args,company,**kwargs):
        super().__init__(*args,**kwargs)
        self.instance.company=company
        from .group_order import ordered
        self.fields['catalogue_group'].queryset=ordered(EquipmentCategory.objects.filter(company=company),kind='equipment')
        self.fields['catalogue_group'].label='Группа техники'
        self.fields['catalogue_group'].empty_label='По группе техники для планирования'
        self.fields['catalogue_group'].widget.attrs['data-equipment-group-select']=''
        self.inline_groups=self.fields['catalogue_group'].queryset
        self._original_type = self.instance.type if self.instance.pk else None
        self.fields['type'].queryset=EquipmentType.objects.filter(company=company,merge_source__isnull=True,parent__isnull=True)
        if self._original_type:
            self.initial['type'] = self._original_type.parent_id or self._original_type.pk
            self.initial['model'] = self._original_type.pk if self._original_type.parent_id else None
        parent = self.data.get(self.add_prefix('type')) if self.is_bound else self.initial.get('type')
        models = EquipmentType.objects.filter(company=company, parent__company=company, parent__isnull=False, merge_source__isnull=True).select_related('parent')
        self.model_options = list(models.values('id', 'parent_id', 'name', 'brand_model'))
        self.fields['model'].queryset = models.filter(parent_id=parent) if str(parent or '').isdigit() else models.none()
        self.fields['model'].label_from_instance = lambda row: row.brand_model or row.name
        from .position_catalog import available_positions
        self.fields['operator_position'].queryset=available_positions(company)
        for field in self.fields.values():
            field.widget.attrs.setdefault('class','form-check-input' if isinstance(field.widget, forms.CheckboxInput) else 'form-select' if isinstance(field.widget, forms.Select) else 'form-control')
        self.fields['plate_number'].help_text = 'Инвентарный номер и VIN указываются отдельно ниже.'
        self.fields['type'].help_text = 'Например, «Экскаватор». По этой позиции считается потребность в ГПР и СМГ.'
        self.fields['model'].help_text = 'Например, «CAT 320». Модель должна относиться к выбранной технике для планирования.'
        self.fields['is_active'].help_text = 'Отключённая машина сохраняет историю и не предлагается для новых назначений.'
        self.order_fields(['type','catalogue_group','model','name','brand_model','manufacture_year','plate_number','inventory_number','vin','site_name','operator_position','is_active'])
    def clean_type(self):
        category = self.cleaned_data['type']
        # Keep the existing model link and its history when the category is unchanged.
        if self.add_prefix('model') not in self.data and self._original_type and (self._original_type.parent_id or self._original_type.pk) == category.pk:
            return self._original_type
        return category

    def clean(self):
        data=super().clean()
        if data.get('model'):
            if not data.get('type') or data['model'].parent_id != data['type'].pk:
                self.add_error('model','Выберите модель выбранной техники для планирования.')
            else:
                data['type'] = data['model']
        if self.instance.pk:
            from apps.production.models import EquipmentActivity,EmployeeActivity
            used=EquipmentActivity.objects.filter(equipment=self.instance).exists() or EmployeeActivity.objects.filter(equipment=self.instance).exists() or self.instance.standing_assignments.exists() or self.instance.transport_events.exists() or self.instance.transport_days.exists()
            if used and (data.get('type') != self._original_type or 'plate_number' in self.changed_data):raise forms.ValidationError('Для машины с историей назначений или фактом техника для планирования и номер сохраняются в истории. Создайте отдельную единицу для другой идентичности.')
        if not any(data.get(key) for key in ['plate_number','inventory_number','vin']):raise forms.ValidationError('Укажите госномер, инвентарный или заводской номер.')
        return data
    def validate_unique(self):
        exclude=self._get_validation_exclusions();exclude.discard('company')
        try:
            self.instance.validate_unique(exclude=exclude)
            self.instance.validate_constraints(exclude=exclude)
        except forms.ValidationError as exc:self._update_errors(exc)

def return_query(request):
    raw = request.POST.get('return_query', request.GET.get('return_query', ''))
    return urlencode([(key, value) for key, value in parse_qsl(raw) if key in {*UnitFilterForm.base_fields, 'page'}])


def list_url(query=''):
    return reverse('resources:equipment_unit_list') + ('?' + query if query else '')


def units(request,pk=None,create=False):
    require_roles(request.user,READ_ROLES if request.method=='GET' and not (pk or create) else PLAN_ROLES)
    instance=get_object_or_404(Equipment,pk=pk,company=request.user.company) if pk else None
    if pk or create or request.method == 'POST':
        query = return_query(request)
        initial = {}
        if not instance:
            parent = EquipmentType.objects.filter(company=request.user.company, parent__isnull=True, merge_source__isnull=True,
                pk=request.GET.get('type')).first() if str(request.GET.get('type','')).isdigit() else None
            model = EquipmentType.objects.filter(company=request.user.company, parent__company=request.user.company,
                parent__isnull=False, merge_source__isnull=True, pk=request.GET.get('model')).first() if str(request.GET.get('model','')).isdigit() else None
            if model and (not parent or model.parent_id == parent.pk):
                initial.update(type=model.parent_id, model=model.pk, brand_model=model.brand_model or model.name, name=model.brand_model or model.name)
            elif parent:
                initial.update(type=parent.pk, name=parent.name)
        form=UnitForm(request.POST if request.method=='POST' else None,instance=instance,initial=initial,company=request.user.company)
        if request.method=='POST' and form.is_valid():
            saved = form.save();messages.success(request,'Машина сохранена.')
            url=reverse('resources:equipment_unit_detail',args=[saved.pk])
            return redirect(url + ('?' + urlencode({'return_query':query}) if query else ''))
        return render(request,'resources/equipment_unit_form.html',{'form':form,'editing':instance,
            'return_query':query,'unit_list_url':list_url(query)},status=400 if request.method=='POST' else 200)

    filters = UnitFilterForm(request.GET, company=request.user.company)
    valid = filters.is_valid()
    day = filters.cleaned_data.get('as_of') if valid else timezone.localdate()
    units_queryset = filter_units(unit_queryset(request.user.company, day), filters).order_by('planning_type_name','plate_number','inventory_number','pk')
    if request.method == 'GET' and request.GET.get('export') == 'xlsx':
        if not valid:
            return HttpResponse('Некорректные фильтры. Исправьте значения и повторите экспорт.',status=400)
        from core.catalogue_excel import catalogue_response
        return catalogue_response(request, units_queryset)
    counts = units_queryset.aggregate(total=Count('pk'), available=Count('pk',filter=Q(is_active=True)),
        repair=Count('pk',filter=Q(unit_state='REPAIR')), unknown=Count('pk',filter=Q(unit_state='UNKNOWN')))
    page = Paginator(units_queryset, 25).get_page(request.GET.get('page'))
    page.object_list = [present_unit(unit) for unit in page.object_list]
    params = request.GET.copy();params.pop('page',None);params.pop('export',None)
    query = params.urlencode()
    return render(request,'resources/equipment_units.html',{'units':page.object_list,'filter_form':filters,
        'counts':counts,'as_of':day,'page_obj':page,'paginator':page.paginator,'is_paginated':page.has_other_pages(),
        'filter_query':query,'unit_list_query':query,'filters_active':any(request.GET.get(name) for name in filters.fields),
        'advanced_filters_open':bool(request.GET.get('category') or request.GET.get('site') or
            request.GET.get('as_of') and request.GET.get('as_of') != timezone.localdate().isoformat()),
        'filter_model_options':list(EquipmentType.objects.filter(company=request.user.company,parent__company=request.user.company,
            parent__isnull=False,merge_source__isnull=True).values('id','parent_id','name','brand_model','parent__name')),
        'unit_parent':filters.cleaned_data.get('parent') if valid else None,'unit_model':filters.cleaned_data.get('model') if valid else None,
        'editable':role_code(request.user) in PLAN_ROLES})


@require_GET
def unit_detail(request, pk):
    require_roles(request.user, READ_ROLES)
    unit = present_unit(get_object_or_404(unit_queryset(request.user.company), pk=pk))
    query = return_query(request)
    from apps.production.equipment_operations_models import EquipmentAssignment
    from core.permissions import scope_queryset
    history_visible=scope_queryset(EquipmentAssignment.objects.all(), request.user).filter(equipment=unit).exists()
    from apps.production.transport import camps_for
    camps=camps_for(request.user)
    transport_history_visible=unit.transport_events.filter(Q(from_camp__in=camps)|Q(to_camp__in=camps)).exists()
    from apps.production.transport import present,OPERATORS
    current_stay=present(request.user.company,timezone.localdate()).filter(equipment=unit,camp__in=camps).first()
    details_edit_url=(reverse('production:transport_details',args=[unit.pk])+'?'+urlencode({'camp':current_stay.camp_id,'date':str(timezone.localdate())})) if current_stay and role_code(request.user) in OPERATORS else ''
    return render(request, 'resources/equipment_unit_detail.html', {'details_edit_url':details_edit_url,'unit':unit,'as_of':timezone.localdate(),
        'details':unit.daily_details.filter(company=request.user.company,date__lte=timezone.localdate()).first(),
        'return_query':query,'unit_list_url':list_url(query),'history_visible':history_visible,'transport_history_visible':transport_history_visible,'editable':role_code(request.user) in PLAN_ROLES})
