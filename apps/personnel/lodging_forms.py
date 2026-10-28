from django import forms
from django.db.models import Q
from apps.resources.models import Employee
from .models import Camp,LodgingBuilding,LodgingBed,LodgingStay
from . import lodging


class StyledForm(forms.Form):
    def style(self):
        for field in self.fields.values():
            field.widget.attrs.setdefault('class','form-check-input' if isinstance(field.widget,forms.CheckboxInput) else 'form-select' if isinstance(field.widget,forms.Select) else 'form-control')


class LodgingFilter(StyledForm):
    camp=forms.ModelChoiceField(label='ВЖГ',queryset=Camp.objects.none())
    date=forms.DateField(label='На дату',widget=forms.DateInput(format='%Y-%m-%d',attrs={'type':'date'}))
    q=forms.CharField(label='Поиск',required=False,widget=forms.TextInput(attrs={'placeholder':'Сотрудник, табельный номер, вагон или место'}))
    state=forms.ChoiceField(label='Места',required=False,choices=[('','Все'),('OCCUPIED','Занятые'),('FREE','Свободные'),('RESERVED','Зарезервированные'),('UNAVAILABLE','Недоступные')])
    def __init__(self,*args,company,**kwargs):
        super().__init__(*args,**kwargs);self.fields['camp'].queryset=Camp.objects.filter(company=company);self.style()


class LodgingMovementForm(StyledForm):
    kind=forms.ChoiceField(label='Действие',choices=[('CHECKIN','Заселить'),('MOVE','Переселить'),('CHECKOUT','Выселить'),('UPDATE','Изменить сведения')])
    employee=forms.ModelChoiceField(label='Сотрудник',queryset=Employee.objects.none())
    bed=forms.ModelChoiceField(label='Новое место',queryset=LodgingBed.objects.none(),required=False)
    date=forms.DateField(label='Фактическая дата',widget=forms.DateInput(format='%Y-%m-%d',attrs={'type':'date'}))
    planned_departure=forms.DateField(label='Плановое освобождение',required=False,widget=forms.DateInput(format='%Y-%m-%d',attrs={'type':'date'}))
    keys_issued=forms.BooleanField(label='Ключи выданы',required=False)
    pass_number=forms.CharField(label='Номер пропуска',max_length=80,required=False)
    comment=forms.CharField(label='Примечание',max_length=4000,required=False,widget=forms.Textarea(attrs={'rows':2}))
    session=forms.UUIDField(widget=forms.HiddenInput)
    signed=forms.CharField(widget=forms.HiddenInput,required=False)
    def __init__(self,*args,company,camp,day,**kwargs):
        super().__init__(*args,**kwargs)
        current=lodging.present(company,day).filter(camp=camp).values('employee_id')
        self.fields['employee'].queryset=Employee.objects.filter(company=company).filter(Q(is_active=True)|Q(pk__in=current)).select_related('position').order_by('last_name','first_name','pk')
        self.fields['employee'].label_from_instance=lambda e:e.display_name+(' · № '+e.employee_number if e.employee_number else '')+(' · '+str(e.position) if e.position_id else '')
        self.fields['bed'].queryset=LodgingBed.objects.filter(company=company,building__camp=camp,building__is_active=True).select_related('building')
        occupied={row.bed_id:row.employee.display_name for row in lodging.present(company,day).filter(camp=camp).select_related('employee')}
        self.fields['bed'].label_from_instance=lambda b:str(b)+' — '+('Занято: '+occupied[b.pk] if b.pk in occupied else b.get_state_display() if b.state!='AVAILABLE' else 'Свободно')
        self.style()
    def clean(self):
        data=super().clean()
        if data.get('kind') in {'CHECKIN','MOVE'} and not data.get('bed'):self.add_error('bed','Выберите новое место.')
        if data.get('planned_departure') and data.get('date') and data['planned_departure']<data['date']:self.add_error('planned_departure','Укажите дату не раньше заселения.')
        return data


class BuildingForm(forms.ModelForm):
    places=forms.IntegerField(label='Создать мест',min_value=0,max_value=200,initial=4,required=False,help_text='Создадим места с номерами 1, 2 и далее. Комнаты можно задать в справочнике мест.')
    class Meta:
        model=LodgingBuilding;fields=['name','inventory_number','kind','is_active']
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        if self.instance.pk:self.fields.pop('places',None)
        for f in self.fields.values():f.widget.attrs['class']='form-check-input' if isinstance(f.widget,forms.CheckboxInput) else 'form-control'


class BedForm(forms.ModelForm):
    class Meta:model=LodgingBed;fields=['room','number','state','note']
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        for f in self.fields.values():f.widget.attrs['class']='form-select' if isinstance(f.widget,forms.Select) else 'form-control'


class CorrectionForm(forms.ModelForm):
    version=forms.CharField(widget=forms.HiddenInput)
    reason=forms.CharField(label='Причина исправления',max_length=500,widget=forms.Textarea(attrs={'rows':2}))
    class Meta:
        model=LodgingStay;fields=['arrived_on','departed_on','planned_departure','keys_issued','pass_number','comment']
        widgets={name:forms.DateInput(format='%Y-%m-%d',attrs={'type':'date'}) for name in ('arrived_on','departed_on','planned_departure')}
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        for f in self.fields.values():f.widget.attrs['class']='form-check-input' if isinstance(f.widget,forms.CheckboxInput) else 'form-control'
