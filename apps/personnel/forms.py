from django import forms
from .models import Board, Person, StaffingGroup, StaffingSlot


class StyledModelForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for f in self.fields.values():
            f.widget.attrs['class'] = 'form-select' if isinstance(f.widget, forms.Select) else 'form-control'
            if isinstance(f, forms.DateField): f.widget = forms.DateInput(format='%Y-%m-%d', attrs={'type': 'date', 'class': 'form-control'})
            if isinstance(f.widget, forms.Textarea): f.widget.attrs['rows'] = 3


class BoardForm(StyledModelForm):
    class Meta:
        model = Board
        fields = ['name', 'project', 'site']


class PersonForm(StyledModelForm):
    class Meta:
        model = Person
        fields = ['employee','full_name', 'employee_number', 'phone', 'notes']
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        from apps.resources.models import Employee
        self.fields['employee'].queryset=Employee.objects.filter(company=self.instance.company).select_related('position')
        self.fields['full_name'].required=False
        self.fields['employee'].help_text='Эта связь позволяет выбирать человека в производственном факте после заезда в подходящий ВЖГ.'
    def clean(self):
        data=super().clean();employee=data.get('employee')
        if self.instance.pk and self.instance.employee_id and self.instance.employee_id!=(employee.pk if employee else None) and self.instance.stays.exists():self.add_error('employee','У карточки уже есть история размещения. Связь сотрудника с историей нельзя заменить.')
        if employee:
            data['full_name']=data.get('full_name') or employee.display_name
            data['employee_number']=data.get('employee_number') or employee.employee_number
        if not data.get('full_name'):self.add_error('full_name','Укажите ФИО или выберите сотрудника.')
        return data


class GroupForm(StyledModelForm):
    class Meta:
        model = StaffingGroup
        fields = ['position', 'attendance_plan', 'staffing_plan']


class SlotForm(StyledModelForm):
    def clean(self):
        data=super().clean()
        if self.data.get('status') and not data.get('person'):raise forms.ValidationError('У вакансии не может быть фактического статуса.')
        return data
    class Meta:
        model = StaffingSlot
        fields = ['person', 'housing_camp', 'pattern', 'comment']
    def __init__(self, *args, board, **kwargs):
        super().__init__(*args, **kwargs)
        from .models import Camp
        self.fields['housing_camp'].queryset=Camp.objects.filter(company_id=board.company_id)
        self.fields['housing_camp'].label='ВЖГ в строке графика'
        self.fields['housing_camp'].help_text='Фактические заезды, выезды и переводы оформляются в «Размещении в ВЖГ».'
        self.fields['person'].queryset = Person.objects.filter(company_id=board.company_id)
        self.fields['pattern'].queryset = board.patterns.all()


class CalendarForm(forms.Form):
    start = forms.DateField(label='С даты', widget=forms.DateInput(attrs={'type':'date', 'class':'form-control'}))
    end = forms.DateField(label='По дату', widget=forms.DateInput(attrs={'type':'date', 'class':'form-control'}))
    code = forms.CharField(label='Код дня: часы, МВ, МО, В или ДП. Пусто — вернуть график', max_length=10, required=False, widget=forms.TextInput(attrs={'class':'form-control'}))
    def clean(self):
        data = super().clean(); start, end = data.get('start'), data.get('end')
        if start and end and (end < start or (end-start).days > 730): raise forms.ValidationError('Период должен быть от 1 дня до 2 лет.')
        code = data.get('code', '').strip().upper()
        if code and code not in {'МО','МВ','В','ДП'}:
            try:
                number = float(code.replace(',', '.'))
                if not 0 <= number <= 24: raise ValueError
                code = str(number).rstrip('0').rstrip('.') if '.' in str(number) else str(number)
            except ValueError: raise forms.ValidationError('Укажите часы от 0 до 24 или код МВ, МО, В, ДП.')
        data['code'] = code
        return data


class ImportForm(forms.Form):
    name = forms.CharField(label='Название таблицы', max_length=200, initial='Нефтепровод', widget=forms.TextInput(attrs={'class':'form-control'}))
    file = forms.FileField(label='Файл Excel, лист «Нефтепровод»', widget=forms.ClearableFileInput(attrs={'class':'form-control','accept':'.xlsx'}))
    confirm = forms.BooleanField(label='Подтверждаю загрузку показанных данных', required=False)


class PatternForm(forms.Form):
    name = forms.CharField(label='Название графика', max_length=150)
    kind = forms.CharField(label='Вид графика (например, М или Ж)', max_length=20, required=False)
    anchor = forms.DateField(label='Первый день вахтового цикла', widget=forms.DateInput(format='%Y-%m-%d',attrs={'type':'date'}))
    start = forms.DateField(label='Календарь с', widget=forms.DateInput(format='%Y-%m-%d',attrs={'type':'date'}))
    end = forms.DateField(label='Календарь по', widget=forms.DateInput(format='%Y-%m-%d',attrs={'type':'date'}))
    on_days = forms.IntegerField(label='Дней на вахте', min_value=1, max_value=365, initial=60)
    off_days = forms.IntegerField(label='Дней межвахтового отдыха', min_value=1, max_value=365, initial=30)
    hours = forms.DecimalField(label='Часов в каждый день вахты', min_value=0, max_value=24, decimal_places=2, max_digits=4, initial=10)
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        for f in self.fields.values():f.widget.attrs['class']='form-control'
    def clean(self):
        data=super().clean()
        if data.get('start') and data.get('end') and (data['end']<data['start'] or (data['end']-data['start']).days>730):raise forms.ValidationError('Календарь должен быть от 1 дня до 2 лет.')
        return data


class CampForm(StyledModelForm):
    version=forms.CharField(widget=forms.HiddenInput,required=False)
    objects_served=forms.ModelMultipleChoiceField(label='Объекты проекта, доступные сотрудникам этого ВЖГ',queryset=None,required=False,widget=forms.SelectMultiple(attrs={'size':8}))
    class Meta:
        from .models import Camp
        model=Camp
        fields=['name','address','is_active']
    def __init__(self,*args,company,**kwargs):
        from apps.projects.models import ConstructionObject
        super().__init__(*args,**kwargs)
        self.instance.company=company
        self.fields['objects_served'].queryset=ConstructionObject.objects.filter(company=company).order_by('name','pk')
        if self.instance.pk:
            self.initial['objects_served']=self.instance.objects_served.values_list('pk',flat=True)
            self.initial['version']=self.instance.updated_at.isoformat();self.fields['version'].required=True
        self.fields['objects_served'].help_text='Один ВЖГ может обслуживать несколько объектов, один объект — несколько ВЖГ.'
        self.fields['is_active'].widget=forms.CheckboxInput(attrs={'class':'form-check-input'})


class MovementForm(forms.Form):
    kind=forms.ChoiceField(label='Действие',choices=[('ARRIVAL','Заезд'),('DEPARTURE','Выезд'),('TRANSFER','Перевод в другой ВЖГ')])
    date=forms.DateField(label='Фактическая дата',widget=forms.DateInput(format='%Y-%m-%d',attrs={'type':'date'}))
    people=forms.ModelMultipleChoiceField(label='Сотрудники кадрового реестра',queryset=Person.objects.none(),required=False,widget=forms.SelectMultiple(attrs={'size':8,'data-personnel-searchable':''}))
    employees=forms.ModelMultipleChoiceField(label='Добавить из справочника сотрудников',queryset=None,required=False,widget=forms.SelectMultiple(attrs={'size':8,'data-personnel-searchable':''}))
    target=forms.ModelChoiceField(label='ВЖГ назначения при переводе',queryset=None,required=False)
    comment=forms.CharField(label='Комментарий',required=False,widget=forms.Textarea(attrs={'rows':2}),max_length=4000)
    snapshot=forms.CharField(widget=forms.HiddenInput)
    def __init__(self,*args,company,camp,day,**kwargs):
        from .models import Camp
        from .housing import present_stays
        from apps.resources.models import Employee
        super().__init__(*args,**kwargs)
        self.fields['people'].queryset=Person.objects.filter(company=company).select_related('employee__position')
        kind=self.data.get('kind',self.initial.get('kind','ARRIVAL'))
        if kind in {'DEPARTURE','TRANSFER'}:
            self.fields['people'].queryset=self.fields['people'].queryset.filter(stays__in=present_stays(company,day).filter(camp=camp)).distinct()
        self.fields['people'].label_from_instance=lambda p:p.full_name+(' · '+str(p.employee.position) if p.employee_id and p.employee.position_id else '')+(' · № '+p.employee_number if p.employee_number else '')
        self.fields['employees'].queryset=Employee.objects.filter(company=company,is_active=True).select_related('position')
        self.fields['employees'].label_from_instance=lambda e:e.display_name+(' · '+str(e.position) if e.position_id else '')+(' · № '+e.employee_number if e.employee_number else '')
        self.fields['target'].queryset=Camp.objects.filter(company=company,is_active=True).exclude(pk=camp.pk)
        for field in self.fields.values():field.widget.attrs.setdefault('class','form-select' if isinstance(field.widget,forms.Select) else 'form-control')
    def clean(self):
        data=super().clean()
        if not data.get('people') and not data.get('employees'):raise forms.ValidationError('Выберите сотрудников из одного или обоих списков.')
        if data.get('kind')!='ARRIVAL' and data.get('employees'):self.add_error('employees','Для выезда или перевода выберите людей из состава ВЖГ.')
        if data.get('kind')=='TRANSFER' and not data.get('target'):self.add_error('target','Выберите ВЖГ назначения.')
        return data


class PersonScheduleForm(forms.Form):
    version=forms.CharField(widget=forms.HiddenInput)
    slot=forms.ModelChoiceField(label='Использовать строку существующей таблицы',queryset=StaffingSlot.objects.none(),required=False)
    pattern=forms.ModelChoiceField(label='График',queryset=None,required=False)
    start=forms.DateField(label='Изменить дни с',required=False,widget=forms.DateInput(format='%Y-%m-%d',attrs={'type':'date'}))
    end=forms.DateField(label='По',required=False,widget=forms.DateInput(format='%Y-%m-%d',attrs={'type':'date'}))
    code=forms.CharField(label='Часы или МВ / МО / В / ДП. Пусто — вернуть график',required=False,max_length=10)
    def __init__(self,*args,person,**kwargs):
        from .models import ShiftPattern
        from .housing import schedule_slot
        super().__init__(*args,**kwargs)
        self.fields['slot'].queryset=person.assignments.filter(company=person.company).select_related('group__board')
        self.fields['slot'].label_from_instance=lambda s:f'{s.group.board.name} · {s.group.position}'
        self.fields['pattern'].queryset=ShiftPattern.objects.filter(company=person.company).select_related('board')
        self.fields['pattern'].label_from_instance=lambda p:f'{p.board.name} · {p}'
        from .housing import schedule_snapshot
        self.initial['version']=schedule_snapshot(person)
        slot=schedule_slot(person)
        if slot:self.initial.update(slot=slot.pk,pattern=slot.pattern_id)
        for f in self.fields.values():f.widget.attrs['class']='form-select' if isinstance(f.widget,forms.Select) else 'form-control'
    def clean(self):
        data=super().clean();start,end=data.get('start'),data.get('end')
        if bool(start)!=bool(end):raise forms.ValidationError('Укажите обе границы периода.')
        if start and end:
            check=CalendarForm({'start':start,'end':end,'code':data.get('code','')})
            if not check.is_valid():raise forms.ValidationError(check.errors.as_text())
            data['code']=check.cleaned_data['code']
        return data
