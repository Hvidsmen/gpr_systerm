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
    planned_departure=forms.DateField(label='Плановая дата выселения',required=False,help_text='Необязательно. Ожидаемая дата; место освобождается только после оформления выселения.',widget=forms.DateInput(format='%Y-%m-%d',attrs={'type':'date'}))
    keys_issued=forms.BooleanField(label='Ключи выданы',required=False)
    key_number=forms.CharField(label='Номер ключа',max_length=80,required=False)
    pass_number=forms.CharField(label='Номер пропуска',max_length=80,required=False)
    comment=forms.CharField(label='Примечание',max_length=4000,required=False,widget=forms.Textarea(attrs={'rows':2}))
    session=forms.UUIDField(widget=forms.HiddenInput)
    signed=forms.CharField(widget=forms.HiddenInput,required=False)
    def __init__(self,*args,company,camp,day,user=None,**kwargs):
        super().__init__(*args,**kwargs)
        current=lodging.present(company,day).filter(camp=camp).values('employee_id')
        from apps.resources.group_order import ordered
        self.fields['employee'].queryset=ordered(Employee.objects.filter(company=company).select_related('position__planning_mapping__brigade__group'),group_path='position__planning_mapping__brigade__group',within=('position__name','last_name','first_name','pk'))
        self.fields['employee'].label_from_instance=lambda e:e.display_name+(' · архивный' if not e.is_active else '')+(' · № '+e.employee_number if e.employee_number else '')+(' · '+str(e.position) if e.position_id else '')
        selected_day=day
        if self.is_bound:
            try:selected_day=self.fields['date'].clean(self.data.get('date'))
            except forms.ValidationError:pass
        session=None
        if user and self.is_bound:
            try:session=lodging.session_id(self.data.get('session'))
            except forms.ValidationError:pass
        self.fields['bed'].queryset=lodging.available_beds(company,camp,selected_day,user=user,session=session)
        self.fields['bed'].label_from_instance=lambda b:str(b)+' — Свободно'
        self.style()
    def clean(self):
        data=super().clean()
        if data.get('kind') in {'CHECKIN','MOVE'} and not data.get('bed'):self.add_error('bed','Выберите новое место.')
        if data.get('planned_departure') and data.get('date') and data['planned_departure']<data['date']:self.add_error('planned_departure','Укажите дату не раньше заселения.')
        return data


class BuildingForm(forms.ModelForm):
    places=forms.IntegerField(label='Создать мест',min_value=0,max_value=200,initial=4,required=False,help_text='Создадим места с номерами 1, 2 и далее. Комнаты можно задать в справочнике мест.')
    class Meta:
        model=LodgingBuilding;fields=['name','inventory_number','description','kind','is_active']
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        if self.instance.pk:self.fields.pop('places',None)
        self.fields['description'].widget.attrs['rows']=2
        self.fields['description'].label='Описание в заголовке вагона'
        self.fields['description'].help_text='Например: Вагон-дом жилой на 8 чел на санях 1218120 БП-002666. Номер вагона укажите отдельно.'
        for f in self.fields.values():f.widget.attrs['class']='form-check-input' if isinstance(f.widget,forms.CheckboxInput) else 'form-control'


class BedForm(forms.ModelForm):
    class Meta:model=LodgingBed;fields=['room','number','state','note']
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        for f in self.fields.values():f.widget.attrs['class']='form-select' if isinstance(f.widget,forms.Select) else 'form-control'


class QuickBedForm(StyledForm):
    building=forms.ModelChoiceField(label='Вагон / корпус',queryset=LodgingBuilding.objects.none(),required=False)
    new_building_name=forms.CharField(label='Или новый вагон / корпус',max_length=200,required=False)
    new_building_inventory_number=forms.CharField(label='Инвентарный номер нового вагона',max_length=80,required=False)
    room=forms.CharField(label='Комната',max_length=60,required=False)
    number=forms.CharField(label='Номер места',max_length=60)
    note=forms.CharField(label='Примечание к месту',max_length=500,required=False)
    def __init__(self,*args,company,camp,**kwargs):
        kwargs.setdefault('auto_id','quick_bed_%s')
        super().__init__(*args,**kwargs)
        self.fields['building'].queryset=LodgingBuilding.objects.filter(company=company,camp=camp,is_active=True)
        self.style()
        for field in self.fields.values():field.widget.attrs['form']='lodging-quick-bed-form'
    def clean(self):
        data=super().clean()
        if data.get('building') and data.get('new_building_name'):self.add_error('new_building_name','Выберите существующий вагон или введите новый.')
        if not data.get('building') and not data.get('new_building_name'):self.add_error('building','Выберите вагон или укажите название нового.')
        if data.get('new_building_inventory_number') and not data.get('new_building_name'):self.add_error('new_building_name','Укажите название нового вагона.')
        return data


class CorrectionForm(forms.ModelForm):
    version=forms.CharField(widget=forms.HiddenInput)
    reason=forms.CharField(label='Причина исправления',max_length=500,widget=forms.Textarea(attrs={'rows':2}))
    class Meta:
        model=LodgingStay;fields=['arrived_on','departed_on','planned_departure','keys_issued','key_number','pass_number','comment']
        widgets={name:forms.DateInput(format='%Y-%m-%d',attrs={'type':'date'}) for name in ('arrived_on','departed_on','planned_departure')}
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        for f in self.fields.values():f.widget.attrs['class']='form-check-input' if isinstance(f.widget,forms.CheckboxInput) else 'form-control'
