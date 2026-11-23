from django import forms
from django.db.models import Q
from django.forms.models import ModelChoiceIteratorValue
from django.urls import reverse
from apps.resources.models import Employee,Position
from .models import Camp,LodgingOrganization,LodgingGuest,LodgingBuilding,LodgingBed,LodgingStay
from . import lodging
from .employee_widgets import RemoteEmployeeSelect


class LodgingBedSelect(forms.Select):
    """Render only a retained value; free places are loaded when the form opens."""
    def optgroups(self,name,value,attrs=None):
        field=getattr(self.choices,'field',None)
        if field is None:return super().optgroups(name,value,attrs)
        selected={str(item) for item in value if str(item)};options=[self.create_option(name,'',field.empty_label or 'Выберите свободное место',not selected,0)]
        for index,obj in enumerate(field.queryset.filter(pk__in=selected),1):
            options.append(self.create_option(name,ModelChoiceIteratorValue(obj.pk,obj),field.label_from_instance(obj),True,index))
        return [(None,options,0)]
    def create_option(self,name,value,label,selected,index,subindex=None,attrs=None):
        option=super().create_option(name,value,label,selected,index,subindex=subindex,attrs=attrs)
        instance=getattr(value,'instance',None)
        if instance is not None:option['attrs']['data-building']=str(instance.building_id)
        return option


class LodgingFilterBedSelect(forms.SelectMultiple):
    """Keep selected places in HTML; the searchable list is loaded remotely."""
    def optgroups(self,name,value,attrs=None):
        field=getattr(self.choices,'field',None)
        if field is None:return super().optgroups(name,value,attrs)
        selected={str(item) for item in value if str(item)};options=[]
        for index,obj in enumerate(field.queryset.filter(pk__in=selected).select_related('building')):
            options.append(self.create_option(name,ModelChoiceIteratorValue(obj.pk,obj),field.label_from_instance(obj),True,index))
        return [(None,options,0)]


class LodgingBedMultipleChoiceField(forms.ModelMultipleChoiceField):
    def label_from_instance(self,bed):return lodging.bed_filter_label(bed)


class StyledForm(forms.Form):
    def style(self):
        for field in self.fields.values():
            field.widget.attrs.setdefault('class','form-check-input' if isinstance(field.widget,forms.CheckboxInput) else 'form-select' if isinstance(field.widget,forms.Select) else 'form-control')


class LodgingFilter(StyledForm):
    camp=forms.ModelChoiceField(label='Местонахождение',queryset=Camp.objects.none())
    q=forms.CharField(label='Поиск',required=False,widget=forms.TextInput(attrs={'placeholder':'Сотрудник, табельный номер, вагон или место'}))
    state=forms.ChoiceField(label='Состояние мест',required=False,choices=[('','Все'),('OCCUPIED','Занятые'),('FREE','Свободные'),('RESERVED','Зарезервированные'),('UNAVAILABLE','Недоступные')])
    buildings=forms.ModelMultipleChoiceField(label='Вагоны / корпуса',queryset=LodgingBuilding.objects.none(),required=False)
    beds=LodgingBedMultipleChoiceField(label='Места',queryset=LodgingBed.objects.none(),required=False,widget=LodgingFilterBedSelect)
    def __init__(self,*args,company,camp_queryset=None,**kwargs):
        super().__init__(*args,**kwargs)
        camps=Camp.objects.filter(company=company) if camp_queryset is None else camp_queryset.filter(company=company)
        self.fields['camp'].queryset=camps
        camp_id=(self.data.get('camp') if self.is_bound else self.initial.get('camp'))
        if hasattr(camp_id,'pk'):camp_id=camp_id.pk
        if str(camp_id or '').isdigit():
            self.fields['buildings'].queryset=LodgingBuilding.objects.filter(company=company,camp_id=camp_id)
            self.fields['beds'].queryset=LodgingBed.objects.filter(company=company,building__camp_id=camp_id).select_related('building')
        self.fields['buildings'].widget.attrs['data-placeholder']='Все вагоны / корпуса'
        self.fields['beds'].widget.attrs.update({
            'data-placeholder':'Все места',
            'data-remote-search-url':reverse('personnel:lodging_filter_beds'),
            'data-remote-camp-field':'camp',
            'data-remote-building-field':'buildings',
        })
        self.style()


class LodgingReportFilter(StyledForm):
    camp=forms.ModelChoiceField(label='Местонахождение',queryset=Camp.objects.none())
    date=forms.DateField(label='На дату',widget=forms.DateInput(format='%Y-%m-%d',attrs={'type':'date'}))
    mode=forms.ChoiceField(label='Режим',choices=[('PORTAL','По состоянию портала'),('FACTUAL','По указанным фактическим датам')])
    view=forms.ChoiceField(label='Вид',choices=[('TABLE','Таблица мест'),('ORGANIZATION','По организациям'),('POSITION','По должностям')])
    building=forms.ModelMultipleChoiceField(label='Вагоны / корпуса',queryset=LodgingBuilding.objects.none(),required=False)
    state=forms.ChoiceField(label='Состояние',required=False,choices=[('','Все'),('OCCUPIED','Занято'),('FREE','Свободно'),('RESERVED','Зарезервировано'),('UNAVAILABLE','Недоступно')])
    q=forms.CharField(label='Поиск',required=False,widget=forms.TextInput(attrs={'placeholder':'Сотрудник, табельный номер, вагон или место'}))
    def __init__(self,*args,company,camp_queryset=None,**kwargs):
        super().__init__(*args,**kwargs)
        camps=Camp.objects.filter(company=company) if camp_queryset is None else camp_queryset.filter(company=company)
        self.fields['camp'].queryset=camps
        camp_id=(self.data.get('camp') if self.is_bound else self.initial.get('camp'))
        if hasattr(camp_id,'pk'):camp_id=camp_id.pk
        self.fields['building'].queryset=LodgingBuilding.objects.filter(company=company,camp_id=camp_id) if str(camp_id or '').isdigit() else LodgingBuilding.objects.none()
        self.fields['building'].widget.attrs['data-placeholder']='Все вагоны / корпуса'
        self.style()


class LodgingCompositionFilter(StyledForm):
    date=forms.DateField(label='На дату',widget=forms.DateInput(format='%Y-%m-%d',attrs={'type':'date'}))
    mode=forms.ChoiceField(label='Расчёт',choices=[('PORTAL','По истории портала'),('FACTUAL','По указанным датам')])
    camps=forms.ModelMultipleChoiceField(label='ВЖГ / местонахождения',queryset=Camp.objects.none())
    view=forms.ChoiceField(label='Группировка',choices=[('POSITION','По должностям'),('CAMP','По ВЖГ и организациям')])
    organizations=forms.MultipleChoiceField(label='Организации',required=False,choices=[])
    positions=forms.MultipleChoiceField(label='Должности',required=False,choices=[])
    buildings=forms.ModelMultipleChoiceField(label='Вагоны / корпуса',queryset=LodgingBuilding.objects.none(),required=False)
    room=forms.CharField(label='Комната',required=False)
    resident_kind=forms.ChoiceField(label='Тип проживающего',required=False,choices=[('','Все'),('employee','Сотрудники из справочника'),('guest','Временные проживающие')])
    q=forms.CharField(label='Поиск',required=False,widget=forms.TextInput(attrs={'placeholder':'ФИО или табельный номер'}))
    def __init__(self,*args,company,camp_queryset=None,**kwargs):
        super().__init__(*args,**kwargs)
        camps=Camp.objects.filter(company=company) if camp_queryset is None else camp_queryset.filter(company=company)
        self.fields['camps'].queryset=camps
        selected=self.data.getlist('camps') if self.is_bound else [str(getattr(value,'pk',value)) for value in self.initial.get('camps',[])]
        camp_ids=[value for value in selected if str(value).isdigit()]
        buildings=LodgingBuilding.objects.filter(company=company,camp__in=camps)
        if camp_ids:buildings=buildings.filter(camp_id__in=camp_ids)
        self.fields['buildings'].queryset=buildings
        stays=LodgingStay.objects.filter(company=company,camp__in=camps)
        organizations=set(stays.exclude(organization_snapshot='').values_list('organization_snapshot',flat=True))
        organizations.update(str(row) for row in LodgingOrganization.objects.filter(company=company,is_active=True))
        positions=set(stays.exclude(position_snapshot='').values_list('position_snapshot',flat=True))
        positions.update(Position.objects.filter(company=company).values_list('name',flat=True))
        self.fields['organizations'].choices=[(value,value) for value in sorted(organizations,key=str.casefold)]
        self.fields['positions'].choices=[(value,value) for value in sorted(positions,key=str.casefold)]
        self.style()


class LodgingMovementForm(StyledForm):
    kind=forms.ChoiceField(label='Действие',choices=[('CHECKIN','Заселить'),('MOVE','Переселить'),('CHECKOUT','Выселить'),('UPDATE','Изменить сведения')])
    employee=forms.ModelChoiceField(label='Сотрудник',queryset=Employee.objects.none(),required=False,widget=RemoteEmployeeSelect(attrs={
        'data-remote-scope':'lodging','data-remote-kind-field':'kind',
        'data-remote-camp-field':'camp','data-remote-date-field':'date',
        'data-remote-preserve-selection-fields':'kind,date',
    }))
    temporary=forms.BooleanField(label='Сотрудника нет в справочнике',required=False)
    guest=forms.ModelChoiceField(label='Ранее добавленный временный проживающий',queryset=LodgingGuest.objects.none(),required=False,empty_label='Добавить нового')
    guest_full_name=forms.CharField(label='ФИО',max_length=250,required=False)
    guest_organization=forms.ModelChoiceField(label='Организация',queryset=LodgingOrganization.objects.none(),required=False,empty_label='Выберите организацию')
    guest_position=forms.ModelChoiceField(label='Должность из справочника',queryset=Position.objects.none(),required=False,empty_label='Выберите должность')
    guest_position_text=forms.CharField(label='Другая должность',max_length=250,required=False)
    guest_employee_number=forms.CharField(label='Табельный номер',max_length=60,required=False)
    guest_note=forms.CharField(label='Примечание к карточке',max_length=1000,required=False)
    building=forms.ModelChoiceField(label='Вагон / корпус',queryset=LodgingBuilding.objects.none(),required=False,empty_label='Все вагоны / корпуса')
    bed=forms.ModelChoiceField(label='Новое место',queryset=LodgingBed.objects.none(),required=False,widget=LodgingBedSelect)
    date=forms.DateField(label='Фактическая дата',widget=forms.DateInput(format='%Y-%m-%d',attrs={'type':'date'}))
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
        self.fields['building'].queryset=LodgingBuilding.objects.filter(company=company,camp=camp,is_active=True)
        self.fields['guest'].queryset=LodgingGuest.objects.filter(company=company,is_active=True).select_related('organization')
        self.fields['guest'].label_from_instance=lambda guest:f'{guest.full_name} · {guest.organization} · {guest.position_name}'+(f' · № {guest.employee_number}' if guest.employee_number else '')
        self.fields['guest_organization'].queryset=LodgingOrganization.objects.filter(company=company,is_active=True)
        self.fields['guest_organization'].label_from_instance=lambda organization:f'{organization}'+(' · требует проверки' if organization.status=='REVIEW' else '')
        self.fields['guest_position'].queryset=Position.objects.filter(company=company).order_by('name','pk')
        self.fields['building'].label_from_instance=lodging.building_label
        selected_day=day
        if self.is_bound:
            try:selected_day=self.fields['date'].clean(self.data.get('date'))
            except forms.ValidationError:pass
        session=None
        if user and self.is_bound:
            try:session=lodging.session_id(self.data.get('session'))
            except forms.ValidationError:pass
        self.fields['bed'].queryset=lodging.available_beds(company,camp,selected_day,user=user,session=session)
        self.fields['bed'].label_from_instance=lodging.bed_label
        self.style()
    def clean(self):
        data=super().clean()
        if data.get('temporary'):
            data['employee']=None
            if not data.get('guest'):
                if data.get('kind')!='CHECKIN':self.add_error('guest','Выберите временного проживающего.')
                if not data.get('guest_full_name'):self.add_error('guest_full_name','Укажите ФИО.')
                if not data.get('guest_organization'):self.add_error('guest_organization','Выберите организацию.')
                if not data.get('guest_position') and not data.get('guest_position_text'):self.add_error('guest_position','Выберите или укажите должность.')
        elif not data.get('employee'):
            self.add_error('employee','Выберите сотрудника или установите отметку «Сотрудника нет в справочнике».')
        if data.get('kind') in {'CHECKIN','MOVE'} and not data.get('bed'):self.add_error('bed','Выберите новое место.')
        if data.get('building') and data.get('bed') and data['bed'].building_id!=data['building'].pk:self.add_error('bed','Выберите место указанного вагона.')
        return data


class QuickOrganizationForm(StyledForm):
    name=forms.CharField(label='Название организации',max_length=250)
    short_name=forms.CharField(label='Сокращённое название',max_length=120,required=False)
    note=forms.CharField(label='Примечание',max_length=1000,required=False,widget=forms.Textarea(attrs={'rows':2}))
    def __init__(self,*args,company,**kwargs):
        super().__init__(*args,**kwargs);self.company=company;self.style()
        for field in self.fields.values():field.widget.attrs['form']='lodging-quick-organization-form'
    def clean_name(self):
        value=' '.join(self.cleaned_data['name'].split())
        duplicate=LodgingOrganization.objects.filter(company=self.company)
        if any(' '.join(name.split()).casefold()==value.casefold() for name in duplicate.values_list('name',flat=True)):
            raise forms.ValidationError('Такая организация уже есть. Выберите её в списке.')
        return value


class BuildingForm(forms.ModelForm):
    places=forms.IntegerField(label='Создать мест',min_value=0,max_value=200,initial=4,required=False,help_text='Создадим места с номерами 1, 2 и далее. Комнаты можно задать в справочнике мест.')
    class Meta:
        model=LodgingBuilding;fields=['name','inventory_number','description','kind','gender','is_active']
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        if self.instance.pk:
            self.fields.pop('places',None)
            if self.is_bound and 'gender' not in self.data:
                self.data=self.data.copy();self.data['gender']=self.instance.gender
        self.fields['gender'].help_text='Укажите назначение вагона: мужской, женский или смешанный. Пол сотрудников ведётся отдельно.'
        self.fields['description'].widget.attrs['rows']=2
        self.fields['name'].label='Номер / обозначение вагона или корпуса'
        self.fields['description'].label='Название / описание вагона'
        self.fields['description'].help_text='Название показывается в заголовке вагона и при выборе места. Например: Вагон-дом жилой на 8 чел на санях 1218120 БП-002666. Номер укажите отдельно.'
        for f in self.fields.values():f.widget.attrs['class']='form-check-input' if isinstance(f.widget,forms.CheckboxInput) else 'form-select' if isinstance(f.widget,forms.Select) else 'form-control'


class BedForm(forms.ModelForm):
    class Meta:model=LodgingBed;fields=['room','number','state','note']
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        for f in self.fields.values():f.widget.attrs['class']='form-select' if isinstance(f.widget,forms.Select) else 'form-control'


class QuickBedForm(StyledForm):
    building=forms.ModelChoiceField(label='Вагон / корпус',queryset=LodgingBuilding.objects.none(),required=False)
    new_building_name=forms.CharField(label='Или новый вагон / корпус: номер или обозначение',max_length=200,required=False)
    new_building_description=forms.CharField(label='Название / описание нового вагона',max_length=4000,required=False,widget=forms.Textarea(attrs={'rows':2}),help_text='Покажем вместе с номером вагона при выборе места.')
    new_building_inventory_number=forms.CharField(label='Инвентарный номер нового вагона',max_length=80,required=False)
    new_building_gender=forms.ChoiceField(label='Пол проживающих в новом вагоне',choices=LodgingBuilding.GENDERS,required=False,help_text='Для существующего вагона пол меняется в его карточке.')
    room=forms.CharField(label='Комната',max_length=60,required=False)
    number=forms.CharField(label='Номер места',max_length=60)
    note=forms.CharField(label='Примечание к месту',max_length=500,required=False)
    def __init__(self,*args,company,camp,**kwargs):
        kwargs.setdefault('auto_id','quick_bed_%s')
        super().__init__(*args,**kwargs)
        self.fields['building'].queryset=LodgingBuilding.objects.filter(company=company,camp=camp,is_active=True)
        self.fields['building'].label_from_instance=lodging.building_label
        self.style()
        for field in self.fields.values():field.widget.attrs['form']='lodging-quick-bed-form'
    def clean(self):
        data=super().clean()
        if data.get('building') and data.get('new_building_name'):self.add_error('new_building_name','Выберите существующий вагон или введите новый.')
        if not data.get('building') and not data.get('new_building_name'):self.add_error('building','Выберите вагон или укажите название нового.')
        if data.get('new_building_inventory_number') and not data.get('new_building_name'):self.add_error('new_building_name','Укажите название нового вагона.')
        if data.get('new_building_description') and not data.get('new_building_name'):self.add_error('new_building_description','Для существующего вагона название / описание меняется через «Изменить вагон».')
        if data.get('new_building_gender') and not data.get('new_building_name'):self.add_error('new_building_gender','Это поле относится к новому вагону. Пол существующего вагона изменяется в его карточке.')
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
