from django import forms
from django.db.models import Q
from apps.resources.models import Employee
from .transport_details_models import EquipmentDayDetails,EquipmentServiceStatus,DETAIL_FIELDS

STATUS_FIELDS={'request_stage':'REQUEST','parts_stage':'PARTS','maintenance_stage':'MAINTENANCE'}
EMPLOYEE_FIELDS={'shift_one_employee','shift_two_employee'}


class DetailsForm(forms.ModelForm):
    signed=forms.CharField(widget=forms.HiddenInput,required=False)
    class Meta:
        model=EquipmentDayDetails
        fields=[name for name,_ in DETAIL_FIELDS]
        widgets={name:forms.Textarea(attrs={'rows':2}) for name in ('defects','request_details','comment')}
    def __init__(self,*args,company,lazy=False,status_catalogue=None,**kwargs):
        super().__init__(*args,**kwargs)
        for name in EMPLOYEE_FIELDS:
            retained=getattr(self.instance,name+'_id',None)
            queryset=Employee.objects.filter(company=company).filter(Q(is_active=True)|Q(pk=retained)).select_related('position__planning_mapping__brigade__group')
            if not lazy:
                from apps.resources.group_order import ordered
                queryset=ordered(queryset,group_path='position__planning_mapping__brigade__group',within=('position__name','last_name','first_name','pk'))
            self.fields[name].queryset=queryset
            self.fields[name].label_from_instance=lambda e:e.display_name+(' · № '+e.employee_number if e.employee_number else '')+(' · '+str(e.position) if e.position_id else '')
            if lazy:
                field=self.fields[name]
                employee=getattr(self.instance,name,None) if retained and self.initial.get(name) is not None else None
                field.choices=[('','Не выбран'),*([(employee.pk,field.label_from_instance(employee))] if employee else [])]
                field.widget.attrs['data-transport-employees']='1'
        for name,kind in STATUS_FIELDS.items():
            self.fields[name].queryset=EquipmentServiceStatus.objects.filter(company=company,kind=kind).filter(Q(is_active=True)|Q(pk=getattr(self.instance,name+'_id',None)))
            if status_catalogue is not None:
                retained=getattr(self.instance,name+'_id',None)
                self.fields[name].choices=[('','---------'),*[(item.pk,item.name) for item in status_catalogue if item.kind==kind and (item.is_active or item.pk==retained)]]
            self.fields[name].widget.attrs['data-status-kind']=kind
        for name in ('repair_started_on','parts_expected_on','request_date'):
            self.fields[name].widget=forms.DateInput(format='%Y-%m-%d',attrs={'type':'date'})
        self.fields['request_details'].label='Номер заявки / ссылки'
        for name,field in self.fields.items():
            field.widget.attrs.setdefault('class','form-select form-select-sm' if isinstance(field.widget,forms.Select) else 'form-control form-control-sm')
            field.widget.attrs['data-detail-field']=name
