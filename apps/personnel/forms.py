from datetime import timedelta
from django import forms
from django.db.models import Q
from .models import BaseCity, Board, MovementReviewRequest, Person, StaffingGroup, StaffingSlot, ZupWorkSchedule


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
        from django.db.models import Q
        current=self.instance.employee_id if self.instance.pk else None
        self.fields['employee'].queryset=Employee.objects.filter(company=self.instance.company).filter(Q(is_active=True)|Q(pk=current)).select_related('position').order_by('last_name','first_name','middle_name','employee_number','pk')
        self.fields['employee'].label_from_instance=lambda e:e.display_name+(' · № '+e.employee_number if e.employee_number else '')+(' · '+str(e.position) if e.position_id else '')+(' · Архивный, сохранённая связь' if not e.is_active else '')
        self.fields['full_name'].required=False
        self.fields['employee'].help_text='Для новой связи доступны только активные сотрудники. После заезда в подходящий ВЖГ сотрудник станет доступен в производственном факте.'
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
    personal_scope=forms.CharField(required=False,widget=forms.HiddenInput)
    kind=forms.ChoiceField(label='Действие',choices=[('ARRIVAL','Заезд'),('DEPARTURE','Выезд'),('TRANSFER','Перевод в другой ВЖГ'),('DISMISSAL','Выезд по увольнению')])
    date=forms.DateField(label='Фактическая дата',widget=forms.DateInput(format='%Y-%m-%d',attrs={'type':'date'}))
    camp=forms.ModelChoiceField(label='ВЖГ заезда / исходный ВЖГ',queryset=None)
    employees=forms.ModelMultipleChoiceField(label='Сотрудники',queryset=None,widget=forms.SelectMultiple(attrs={'size':8,'data-personnel-searchable':''}))
    destination=forms.ChoiceField(label='Куда / причина выезда',choices=[('REST','На межвахтовый отдых'),('CAMP','В другой ВЖГ'),('DISMISSAL','По увольнению')],initial='REST',required=False)
    target=forms.ModelChoiceField(label='ВЖГ назначения',queryset=None,required=False)
    comment=forms.CharField(label='Комментарий',required=False,widget=forms.Textarea(attrs={'rows':2}),max_length=4000)
    direct_fact=forms.BooleanField(label='Временный режим: провести факт сразу, без заявки',required=False,
        help_text='Используйте только на этапе настройки рабочих мест. Дата может быть сегодняшней или прошедшей; будущий факт запрещён.')
    snapshot=forms.CharField(widget=forms.HiddenInput)
    def __init__(self,*args,company,camp,day,fixed_employee=None,allow_direct=False,**kwargs):
        from .models import Camp
        from .housing import present_stays
        from apps.resources.models import Employee
        super().__init__(*args,**kwargs)
        self.fields['camp'].queryset=Camp.objects.filter(company=company).order_by('name','pk')
        self.initial.setdefault('camp',camp.pk)
        kind=self.data.get('kind',self.initial.get('kind','ARRIVAL'))
        employees=Employee.objects.filter(company=company).exclude(personnel_card__dispatcher_removed_at__isnull=False).select_related('position')
        present=present_stays(company,day).filter(camp=camp).values('person__employee_id')
        # Keep present archived employees selectable for departure/transfer.
        # The browser switches the visible choices without clearing the form.
        from apps.resources.group_order import ordered
        self.fields['employees'].queryset=ordered(employees.filter(Q(is_active=True)|Q(pk__in=present)).select_related('position__planning_mapping__brigade__group'),group_path='position__planning_mapping__brigade__group',within=('position__name','last_name','first_name','pk'))
        self.fields['employees'].widget.attrs['data-present-employees']=','.join(str(pk) for pk in present.values_list('person__employee_id',flat=True) if pk)
        import json
        by_camp={}
        for camp_id,employee_id in present_stays(company,day).exclude(person__employee=None).values_list('camp_id','person__employee_id'):
            by_camp.setdefault(str(camp_id),[]).append(str(employee_id))
        self.fields['employees'].widget.attrs['data-present-by-camp']=json.dumps(by_camp)
        self.fields['employees'].widget.attrs['data-active-employees']=','.join(str(pk) for pk in employees.filter(is_active=True).values_list('pk',flat=True))
        self.fields['employees'].label_from_instance=lambda e:e.display_name+(' · '+str(e.position) if e.position_id else '')+(' · № '+e.employee_number if e.employee_number else '')+(' · архивный, находится в ВЖГ' if not e.is_active else '')
        self.personal=bool(fixed_employee)
        if fixed_employee:
            self.initial['employees']=[fixed_employee]
            self.fields['employees'].queryset=self.fields['employees'].queryset.filter(pk=fixed_employee)
            self.fields['employees'].disabled=True
            self.fields['employees'].label='Сотрудник'
        self.camp=camp;self.company=company
        self.fields['target'].queryset=Camp.objects.filter(company=company,is_active=True)
        if not allow_direct:self.fields.pop('direct_fact')
        for field in self.fields.values():field.widget.attrs.setdefault('class','form-select' if isinstance(field.widget,forms.Select) else 'form-control')
        if 'direct_fact' in self.fields:self.fields['direct_fact'].widget.attrs['class']='form-check-input'
    def clean(self):
        data=super().clean()
        camp=data.get('camp') or self.camp
        employees=data.get('employees')
        if employees:
            if data.get('kind')=='ARRIVAL' and employees.filter(is_active=False).exists():
                self.add_error('employees','Для заезда выберите активных сотрудников.')
            elif data.get('kind') in {'DEPARTURE','TRANSFER','DISMISSAL'}:
                from .housing import present_stays
                ids=present_stays(self.company,data.get('date') or self.initial.get('date')).filter(camp=camp).values('person__employee_id')
                if employees.exclude(pk__in=ids).exists():self.add_error('employees','Для выезда или перевода выберите сотрудников, находящихся в этом ВЖГ на выбранную дату.')
        if data.get('kind')=='DEPARTURE' and data.get('destination')=='CAMP':data['kind']='TRANSFER'
        if data.get('kind')=='DISMISSAL':data['kind']='DEPARTURE';data['destination']='DISMISSAL'
        if data.get('kind')=='TRANSFER' and not data.get('target'):self.add_error('target','Выберите ВЖГ назначения.')
        return data


class MovementRecommendationForm(forms.Form):
    employees=forms.ModelMultipleChoiceField(label='Сотрудники',queryset=None,widget=forms.SelectMultiple(attrs={'size':6,'data-personnel-searchable':''}))
    camp=forms.ModelChoiceField(label='ВЖГ',queryset=None)
    planned_on=forms.DateField(label='Рекомендуемая дата',widget=forms.DateInput(format='%Y-%m-%d',attrs={'type':'date'}))
    def __init__(self,*args,company,day,kind,**kwargs):
        from apps.resources.models import Employee
        from .models import Camp
        super().__init__(*args,**kwargs);self.company=company;self.day=day;self.kind=kind
        self.fields['employees'].queryset=Employee.objects.filter(company=company,is_active=True).exclude(personnel_card__dispatcher_removed_at__isnull=False).select_related('position').order_by('last_name','first_name','middle_name','pk')
        self.fields['employees'].label_from_instance=lambda employee:employee.display_name+(' · '+str(employee.position) if employee.position_id else '')
        self.fields['camp'].queryset=Camp.objects.filter(company=company,is_active=True).order_by('name')
        self.initial.setdefault('planned_on',day)
        for field in self.fields.values():field.widget.attrs.setdefault('class','form-select' if isinstance(field.widget,forms.Select) else 'form-control')
    def clean_planned_on(self):
        value=self.cleaned_data['planned_on']
        if not self.day<=value<=self.day+timedelta(days=14):raise forms.ValidationError('Дата должна быть в пределах ближайших 14 дней.')
        return value


class MovementReviewForm(StyledModelForm):
    base_city_choice=forms.ModelChoiceField(label='Базовый город',queryset=BaseCity.objects.none(),required=False,empty_label='Выберите город')
    new_base_city=forms.CharField(label='Добавить новый базовый город',max_length=150,required=False,
        help_text='Если нужного города ещё нет в списке, введите его здесь — он сразу добавится в справочник.')
    class Meta:
        model=MovementReviewRequest
        fields=['employment_kind','planning_position_name','position_name','construction_object','site_object','movement_date','phone','request_number','travel_time','travel_status','note']
        widgets={'movement_date':forms.DateInput(format='%Y-%m-%d',attrs={'type':'date'}),'travel_time':forms.TimeInput(format='%H:%M',attrs={'type':'time'})}
    def __init__(self,*args,confirm=False,**kwargs):
        super().__init__(*args,**kwargs);self.confirm=confirm
        self.fields['movement_date'].label='Дата заезда' if self.instance.kind=='ARRIVAL' else 'Дата выезда'
        from apps.projects.models import ConstructionObject
        objects=ConstructionObject.objects.filter(company=self.instance.company).select_related('parent').order_by('name')
        self.fields['construction_object'].queryset=objects.filter(parent=None)
        self.fields['construction_object'].empty_label='Выберите объект'
        self.fields['site_object'].queryset=objects
        self.fields['site_object'].empty_label='Выберите участок'
        self.fields['site_object'].label_from_instance=lambda value:f'{value.parent.name} · {value.name}' if value.parent_id else value.name
        self.fields['base_city_choice'].queryset=BaseCity.objects.filter(company=self.instance.company).order_by('name')
        current=BaseCity.objects.filter(company=self.instance.company,name__iexact=self.instance.base_city.strip()).first() if self.instance.base_city else None
        if current:self.initial['base_city_choice']=current.pk
        elif self.instance.base_city:self.initial['new_base_city']=self.instance.base_city
        self.fields['travel_status'].widget=forms.Select(choices=self.status_choices())
        if self.instance.kind!='ARRIVAL':
            self.fields['employment_kind'].required=False
            self.fields['employment_kind'].widget=forms.HiddenInput()
        if confirm:
            required=['planning_position_name','position_name','construction_object','site_object','movement_date','phone','request_number','travel_time','travel_status']
            if self.instance.kind=='ARRIVAL':required.append('employment_kind')
            for name in required:self.fields[name].required=True
    def status_choices(self):
        values=[('','Выберите статус'),('Ожидается','Ожидается'),('Перенесён','Перенесён'),('Отменён','Отменён')]
        values.insert(2,('Прибыл','Прибыл') if self.instance.kind=='ARRIVAL' else ('Убыл','Убыл'))
        values.insert(3,('Не прибыл','Не прибыл') if self.instance.kind=='ARRIVAL' else ('Не выехал','Не выехал'))
        return values
    def clean(self):
        data=super().clean();new=' '.join((data.get('new_base_city') or '').split());selected=data.get('base_city_choice')
        if new and selected:self.add_error('new_base_city','Выберите город из списка или добавьте новый, но не оба варианта.')
        if self.confirm and not (new or selected):self.add_error('base_city_choice','Выберите базовый город или добавьте новый.')
        obj,site=data.get('construction_object'),data.get('site_object')
        if obj and site and site.pk!=obj.pk and site.parent_id!=obj.pk:self.add_error('site_object','Выберите участок выбранного объекта.')
        data['new_base_city']=new
        return data


class PersonScheduleForm(forms.Form):
    version=forms.CharField(widget=forms.HiddenInput)
    slot=forms.ModelChoiceField(label='Использовать строку существующей таблицы',queryset=StaffingSlot.objects.none(),required=False)
    pattern=forms.ModelChoiceField(label='График',queryset=None,required=False)
    on_days=forms.IntegerField(label='Новый цикл · дней на вахте',min_value=1,max_value=365,required=False,help_text='Оставьте пустым, чтобы использовать цикл выбранного графика.')
    off_days=forms.IntegerField(label='Новый цикл · дней отдыха',min_value=1,max_value=365,required=False)
    hours=forms.DecimalField(label='Часов в рабочий день нового цикла',min_value=0,max_value=24,decimal_places=2,max_digits=4,required=False,help_text='Если не указаны, сохраняется обычная продолжительность рабочего дня выбранного графика.')
    start=forms.DateField(label='Изменить дни с',required=False,widget=forms.DateInput(format='%Y-%m-%d',attrs={'type':'date'}))
    end=forms.DateField(label='По',required=False,widget=forms.DateInput(format='%Y-%m-%d',attrs={'type':'date'}))
    code=forms.CharField(label='Часы или МВ / МО / В / ДП. Пусто — вернуть график',required=False,max_length=10)
    def __init__(self,*args,person,**kwargs):
        from .models import ShiftPattern
        from .housing import schedule_slot
        super().__init__(*args,**kwargs)
        self.fields['slot'].queryset=person.assignments.filter(company=person.company,cancelled_at=None).select_related('group__board')
        self.fields['slot'].label_from_instance=lambda s:f'{s.group.board.name} · {s.group.position}'
        self.fields['pattern'].queryset=ShiftPattern.objects.filter(company=person.company).select_related('board')
        self.fields['pattern'].empty_label='Без личного графика — вахтовый цикл из ЗУП, если задан'
        self.fields['pattern'].label_from_instance=lambda p:f'{p.board.name} · {p}'
        from .housing import schedule_snapshot
        self.initial['version']=schedule_snapshot(person)
        slot=schedule_slot(person)
        if slot:self.initial.update(slot=slot.pk,pattern=slot.pattern_id)
        from .rotation_calendar import description
        from django.utils import timezone
        self.fields['pattern'].help_text=description(slot,timezone.localdate(),person=person)+' Личный график имеет приоритет. Без него используется вахтовый цикл из ЗУП, например 60х30. Фактический заезд начинает вахту, выезд на МО — отдых; перевод между ВЖГ сохраняет цикл.'
        for f in self.fields.values():f.widget.attrs['class']='form-select' if isinstance(f.widget,forms.Select) else 'form-control'
    def clean(self):
        data=super().clean();start,end=data.get('start'),data.get('end')
        if bool(data.get('on_days'))!=bool(data.get('off_days')):raise forms.ValidationError('Укажите обе длительности нового цикла: вахту и отдых.')
        if bool(start)!=bool(end):raise forms.ValidationError('Укажите обе границы периода.')
        if start and end:
            check=CalendarForm({'start':start,'end':end,'code':data.get('code','')})
            if not check.is_valid():raise forms.ValidationError(check.errors.as_text())
            data['code']=check.cleaned_data['code']
        return data


class ZupScheduleChangeForm(forms.Form):
    schedule=forms.ModelChoiceField(label='Новый график из ЗУП',queryset=ZupWorkSchedule.objects.none(),empty_label='Выберите график')
    def __init__(self,*args,person,**kwargs):
        super().__init__(*args,**kwargs);self.person=person
        self.fields['schedule'].queryset=ZupWorkSchedule.objects.filter(company=person.company,is_active=True).order_by('name','pk')
        self.fields['schedule'].widget.attrs['class']='form-select'
        current=((person.employee.zup_card or {}).get('schedule') or '').strip() if person.employee_id else ''
        self.fields['schedule'].help_text='Сейчас в ЗУП: '+(current or 'не указан')+'. После сохранения график изменится в портале только при следующей синхронизации из ЗУП.'
