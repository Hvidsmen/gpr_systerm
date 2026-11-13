from django import forms
from django.utils import timezone
from apps.projects.models import ConstructionObject
from apps.resources.models import Employee
from .models import RecruitmentRequest, RecruitmentCandidate,TalentPoolCandidate,Camp
from .recruitment import allowed_camps, validate_documents
from .employee_widgets import RemoteEmployeeSelect


class RevisionForm(forms.Form):
    revision = forms.IntegerField(min_value=0, widget=forms.HiddenInput)


class ReasonForm(RevisionForm):
    reason = forms.CharField(label='Причина отмены', max_length=2000, widget=forms.Textarea(attrs={'rows':2}))


class RequestForm(forms.ModelForm):
    revision = forms.IntegerField(min_value=0, widget=forms.HiddenInput, initial=0)
    class Meta:
        model = RecruitmentRequest
        fields = ['construction_object','position','quantity','required_on','requirements']
        widgets = {'required_on':forms.DateInput(format='%Y-%m-%d', attrs={'type':'date'}),'requirements':forms.Textarea(attrs={'rows':3})}
    def __init__(self,*args,company,**kwargs):
        super().__init__(*args,**kwargs)
        self.instance.company=company
        self.fields['construction_object'].queryset=ConstructionObject.objects.filter(company=company).order_by('name')
        self.fields['quantity'].min_value=1
        self.fields['quantity'].widget.attrs['min']=1
        if self.instance.pk:self.initial['revision']=self.instance.revision


class MultipleFiles(forms.ClearableFileInput):allow_multiple_selected=True
class DocumentsField(forms.FileField):
    def clean(self,data,initial=None):
        files=[super(DocumentsField,self).clean(item,initial) for item in (data if isinstance(data,(list,tuple)) else [data]) if item]
        validate_documents(files)
        return files


class TalentPoolChoiceField(forms.ModelChoiceField):
    def label_from_instance(self,candidate):
        position=(candidate.source_data or {}).get('position') or 'должность не указана'
        site=(candidate.source_data or {}).get('construction_site') or ''
        return f'{candidate.full_name} — {position}' + (f' · {site}' if site else '')


class CandidateForm(forms.ModelForm):
    request_position=forms.CharField(label='Должность по заявке',disabled=True,required=False)
    revision = forms.IntegerField(min_value=0, widget=forms.HiddenInput, initial=0)
    documents = DocumentsField(label='Резюме и документы', required=False, widget=MultipleFiles,
        help_text='До 10 файлов, каждый до 10 МБ: PDF, DOC, DOCX, TXT, JPG, PNG. Скачивание доступно только участникам процесса.')
    class Meta:
        model = RecruitmentCandidate
        fields = ['pool_candidate','full_name','phone','status','ready_on','notes']
        widgets = {'ready_on':forms.DateInput(format='%Y-%m-%d',attrs={'type':'date'}),'notes':forms.Textarea(attrs={'rows':3})}
    def __init__(self,*args,company,request_row,**kwargs):
        super().__init__(*args,**kwargs)
        self.instance.company=company;self.instance.request=request_row
        original=self.fields['pool_candidate']
        self.fields['pool_candidate']=TalentPoolChoiceField(label=original.label,required=False,
            queryset=TalentPoolCandidate.objects.filter(company=company,is_active=True).exclude(
                vacancy_candidates__status__in=['NEW','SCREENING','READY','TRANSFERRED'],
                vacancy_candidates__request__cancelled_at__isnull=True,
                vacancy_candidates__request__completed_at__isnull=True).distinct().order_by('full_name','pk'),
            empty_label='Новый человек — заполнить вручную')
        self.pool_candidate_data={str(candidate.pk):{'full_name':candidate.full_name,'phone':candidate.phone or '',
            'notes':candidate.notes or ''} for candidate in self.fields['pool_candidate'].queryset}
        self.initial['request_position']=request_row.position
        self.order_fields(['request_position','pool_candidate','full_name','phone','status','ready_on','notes','documents','revision'])
        self.fields['full_name'].required=False
        self.fields['status'].choices=[(v,l) for v,l in RecruitmentCandidate.STATES if v!='TRANSFERRED']
        if self.instance.pk:self.initial['revision']=self.instance.revision
    def clean(self):
        data=super().clean();pool=data.get('pool_candidate')
        if pool:
            data['full_name']=pool.full_name;data['phone']=pool.phone or data.get('phone','');data['notes']=data.get('notes') or pool.notes
        if not (data.get('full_name') or '').strip():self.add_error('full_name','Выберите человека из пула или укажите ФИО.')
        return data


class TalentPoolForm(forms.ModelForm):
    position=forms.CharField(label='Должность',required=False)
    construction_site=forms.CharField(label='Строительный участок',required=False)
    birth_date=forms.DateField(label='Дата рождения',required=False,widget=forms.DateInput(format='%Y-%m-%d',attrs={'type':'date'}))
    passport=forms.CharField(label='Серия и номер паспорта',required=False)
    manager_approval=forms.CharField(label='Согласование непосредственного руководителя',required=False)
    security_approval=forms.CharField(label='Согласование СБ',required=False)
    skzi_until=forms.DateField(label='СКЗИ действует до',required=False,widget=forms.DateInput(format='%Y-%m-%d',attrs={'type':'date'}))
    medical_exam=forms.CharField(label='Медицинский осмотр',required=False)
    pass_documents=forms.CharField(label='Документы направлены на пропуск',required=False)
    base_city=forms.CharField(label='Базовый город',required=False)
    readiness=forms.CharField(label='Готовность к заезду',required=False)
    dudinka_arrival=forms.DateField(label='Дата прибытия в Дудинку',required=False,widget=forms.DateInput(format='%Y-%m-%d',attrs={'type':'date'}))
    responsible_hr=forms.CharField(label='Ответственный HR',required=False)
    agency=forms.CharField(label='Название агентства',required=False)
    source_fields=('position','construction_site','birth_date','passport','manager_approval','security_approval','skzi_until',
        'medical_exam','pass_documents','base_city','readiness','dudinka_arrival','responsible_hr','agency')
    class Meta:
        model=TalentPoolCandidate;fields=['full_name','phone','notes','is_active'];widgets={'notes':forms.Textarea(attrs={'rows':3})}
    def __init__(self,*args,company,user,**kwargs):
        super().__init__(*args,**kwargs);self.instance.company=company
        if not self.instance.pk:self.instance.created_by=user
        source=self.instance.source_data or {}
        for field in self.source_fields:
            if field not in self.initial:self.initial[field]=source.get(field,'')
        self.order_fields(['full_name','position','construction_site','phone','birth_date','passport','base_city','readiness',
            'dudinka_arrival','manager_approval','security_approval','skzi_until','medical_exam','pass_documents',
            'responsible_hr','agency','notes','is_active'])
    def save(self,commit=True):
        instance=super().save(commit=False);source=dict(instance.source_data or {})
        for field in self.source_fields:
            value=self.cleaned_data.get(field)
            source[field]=value.isoformat() if hasattr(value,'isoformat') else (value or '').strip()
        source['full_name']=instance.full_name;source['phone']=instance.phone;source['notes']=instance.notes
        instance.source_data=source
        if commit:instance.save()
        return instance
    def clean(self):
        data=super().clean()
        from .recruitment import normalized_candidate_identity
        identity=normalized_candidate_identity(data.get('full_name'),data.get('phone'))
        if identity[0]:
            for existing in TalentPoolCandidate.objects.filter(company=self.instance.company).exclude(pk=self.instance.pk):
                if normalized_candidate_identity(existing.full_name,existing.phone)==identity:
                    raise forms.ValidationError(f'Такой кандидат уже есть в общем пуле: {existing.full_name}.')
        return data


class TalentPoolImportForm(forms.Form):
    file=forms.FileField(label='Файл Excel',help_text='Поддерживается XLSX. Будет прочитан лист «Магазин 2026».')


class ArrivalForm(RevisionForm):
    candidate_name=forms.CharField(label='ФИО кандидата',disabled=True)
    project_name=forms.CharField(label='Проект',disabled=True)
    expected_on=forms.DateField(label='Плановая дата заезда',widget=forms.DateInput(format='%Y-%m-%d',attrs={'type':'date'}))
    camp=forms.ModelChoiceField(label='ВЖГ',queryset=None,required=False)
    employee=forms.ModelChoiceField(label='Оформленный сотрудник',queryset=None,required=False,widget=RemoteEmployeeSelect,
        help_text='Выберите сотрудника после оформления и появления в реестре. Совпадение ФИО само по себе не связывает кандидата с сотрудником.')
    actual_on=forms.DateField(required=False,widget=forms.HiddenInput())
    notes=forms.CharField(label='Комментарий к заезду',required=False,widget=forms.Textarea(attrs={'rows':3}))
    def __init__(self,*args,company,preparation,**kwargs):
        super().__init__(*args,**kwargs)
        camps=allowed_camps(company,preparation.candidate.request.construction_object).order_by('name')
        self.fields['camp'].queryset=camps
        self.fields['employee'].queryset=Employee.objects.filter(company=company,is_active=True).order_by('last_name','first_name','pk')
        self.fields['employee'].widget.attrs['data-personnel-searchable']='1'
        automatic_camp=camps.first() if not preparation.camp_id and camps.count()==1 else None
        request_object=preparation.candidate.request.construction_object
        # Кадры называют проектом объект назначения (КТП, КТП-1), а не
        # верхнеуровневую сущность Project (например, «Ямал»).
        self.initial.update(candidate_name=preparation.candidate.full_name,project_name=request_object.name,
            revision=preparation.revision,expected_on=preparation.expected_on,
            camp=preparation.camp_id or (automatic_camp.pk if automatic_camp else None),employee=preparation.employee_id,
            notes=preparation.notes,actual_on=timezone.localdate())
        self.order_fields(['candidate_name','project_name','expected_on','camp','employee','notes','actual_on','revision'])


class BulkArrivalCommonForm(forms.Form):
    construction_object=forms.ModelChoiceField(label='Проект',queryset=ConstructionObject.objects.none(),required=False)
    camp=forms.ModelChoiceField(label='ВЖГ',queryset=Camp.objects.none(),required=False)
    def __init__(self,*args,company,required=False,**kwargs):
        super().__init__(*args,**kwargs)
        self.fields['construction_object'].queryset=ConstructionObject.objects.filter(company=company).order_by('name','pk')
        self.fields['camp'].queryset=Camp.objects.filter(company=company,is_active=True).order_by('name','pk')
        self.fields['construction_object'].required=required;self.fields['camp'].required=required
        for field in self.fields.values():field.widget.attrs['class']='form-select'


class BulkArrivalRowForm(forms.Form):
    planning_position_name=forms.CharField(label='Должность по заявке',max_length=250,required=False)
    position_name=forms.CharField(label='Должность сотрудника',max_length=250,required=False)
    base_city=forms.CharField(label='Базовый город',max_length=150,required=False)
    phone=forms.CharField(label='Телефон',max_length=80,required=False)
    movement_date=forms.DateField(label='Прибытие в ОП Дудинка',required=False,widget=forms.DateInput(format='%Y-%m-%d',attrs={'type':'date'}))
    planned_hub_departure_date=forms.DateField(label='Выезд из ОП Дудинка',required=False,widget=forms.DateInput(format='%Y-%m-%d',attrs={'type':'date'}))
    note=forms.CharField(label='Комментарий',required=False,max_length=2000)
    def __init__(self,*args,required=False,**kwargs):
        super().__init__(*args,**kwargs)
        if required:
            for name in ('planning_position_name','position_name','base_city','phone','movement_date','planned_hub_departure_date'):
                self.fields[name].required=True
        for field in self.fields.values():field.widget.attrs['class']='form-control form-control-sm'
    def clean(self):
        data=super().clean()
        if data.get('movement_date') and data.get('planned_hub_departure_date') and data['planned_hub_departure_date']<data['movement_date']:
            self.add_error('planned_hub_departure_date','Не может быть раньше прибытия в ОП Дудинка.')
        return data
