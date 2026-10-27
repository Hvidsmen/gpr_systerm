from django import forms
from django.contrib import messages
from django.shortcuts import render,redirect,get_object_or_404
from core.permissions import READ_ROLES,PLAN_ROLES,require_roles,role_code
from .models import Equipment,EquipmentType

class UnitForm(forms.ModelForm):
    class Meta:
        model=Equipment
        fields=['type','plate_number','name','brand_model','manufacture_year','inventory_number','vin','site_name','operator_position','is_active']
        labels={'type':'Техника для планирования','plate_number':'Госномер / инвентарный номер','name':'Название','is_active':'Активна'}
    def __init__(self,*args,company,**kwargs):
        super().__init__(*args,**kwargs)
        self.instance.company=company
        self._original_type = self.instance.type if self.instance.pk else None
        self.fields['type'].queryset=EquipmentType.objects.filter(company=company,merge_source__isnull=True,parent__isnull=True)
        if self._original_type:
            self.initial['type'] = self._original_type.parent_id or self._original_type.pk
        from .position_catalog import available_positions
        self.fields['operator_position'].queryset=available_positions(company)
        for field in self.fields.values():field.widget.attrs.setdefault('class','form-control')
    def clean_type(self):
        category = self.cleaned_data['type']
        # Keep the existing model link and its history when the category is unchanged.
        if self._original_type and (self._original_type.parent_id or self._original_type.pk) == category.pk:
            return self._original_type
        return category

    def clean(self):
        data=super().clean()
        if self.instance.pk:
            from apps.production.models import EquipmentActivity,EmployeeActivity
            used=EquipmentActivity.objects.filter(equipment=self.instance).exists() or EmployeeActivity.objects.filter(equipment=self.instance).exists() or self.instance.standing_assignments.exists()
            if used and (data.get('type') != self._original_type or 'plate_number' in self.changed_data):raise forms.ValidationError('Для машины с историей назначений или фактом техника для планирования и номер сохраняются в истории. Создайте отдельную единицу для другой идентичности.')
        if not any(data.get(key) for key in ['plate_number','inventory_number','vin']):raise forms.ValidationError('Укажите госномер, инвентарный или заводской номер.')
        return data
    def validate_unique(self):
        exclude=self._get_validation_exclusions();exclude.discard('company')
        try:
            self.instance.validate_unique(exclude=exclude)
            self.instance.validate_constraints(exclude=exclude)
        except forms.ValidationError as exc:self._update_errors(exc)

def units(request,pk=None):
    require_roles(request.user,READ_ROLES if request.method=='GET' else PLAN_ROLES)
    instance=get_object_or_404(Equipment,pk=pk,company=request.user.company) if pk else None
    units_queryset = Equipment.objects.filter(company=request.user.company).select_related('type__parent', 'operator_position')
    if request.method == 'GET' and request.GET.get('export') == 'xlsx':
        from core.catalogue_excel import catalogue_response
        return catalogue_response(request, units_queryset)
    form=UnitForm(request.POST if request.method=='POST' else None,instance=instance,company=request.user.company)
    if request.method=='POST' and form.is_valid():
        form.save();messages.success(request,'Единица техники сохранена.')
        return redirect('resources:equipment_unit_list')
    return render(request,'resources/equipment_units.html',{'form':form,'units':units_queryset,'editable':role_code(request.user) in PLAN_ROLES,'editing':instance},status=400 if request.method=='POST' else 200)
