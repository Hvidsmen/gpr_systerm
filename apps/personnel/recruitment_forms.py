from django import forms
from django.db.models import Q
from django.utils import timezone
from django.utils.html import format_html
from apps.projects.models import ConstructionObject
from apps.resources.models import Employee,Position
from apps.accounts.models import User
from .models import BaseCity,RecruitmentRequest, RecruitmentCandidate,RecruitmentSpecialist,TalentPoolCandidate,Camp
from .recruitment_models import RECRUITMENT_STAGES,POOL_UNAVAILABLE_STAGES,RECRUITMENT_SYSTEM_STAGES,RECRUITMENT_TERMINAL_STAGES
from .recruitment import allowed_camps, validate_documents
from .employee_widgets import RemoteEmployeeSelect


class RevisionForm(forms.Form):
    revision = forms.IntegerField(min_value=0, widget=forms.HiddenInput)


class ReasonForm(RevisionForm):
    reason = forms.CharField(label='Причина отмены', max_length=2000, widget=forms.Textarea(attrs={'rows':2}))


class RecruiterAssignmentForm(RevisionForm):
    recruiter=forms.ModelChoiceField(label='Ответственный рекрутер',queryset=User.objects.none(),empty_label='Выберите рекрутера',widget=forms.Select(attrs={'class':'form-select'}))
    def __init__(self,*args,company,current=None,**kwargs):
        super().__init__(*args,**kwargs)
        rows=User.objects.filter(company=company,is_active=True,archived_at__isnull=True,role__code='RECRUITER')
        self.fields['recruiter'].queryset=rows.order_by('last_name','first_name','username','pk')
        if current and rows.filter(pk=current.pk).exists():self.initial['recruiter']=current.pk


class RecruitmentSpecialistForm(forms.ModelForm):
    class Meta:
        model=RecruitmentSpecialist
        fields=['name','user','is_active']
    def __init__(self,*args,company,**kwargs):
        super().__init__(*args,**kwargs);self.instance.company=company
        users=User.objects.filter(company=company,is_active=True,archived_at__isnull=True,role__code='RECRUITER')
        if self.instance.user_id:
            users=User.objects.filter(company=company).filter(Q(pk=self.instance.user_id)|Q(
                is_active=True,archived_at__isnull=True,role__code='RECRUITER'))
        self.fields['user'].queryset=users.select_related('role').order_by('last_name','first_name','username','pk')
        self.fields['user'].empty_label='Не связан с пользователем'
        self.fields['user'].help_text='Необязательно. Свяжите запись с учётной записью, имеющей роль «Рекрутер».'
        self.fields['is_active'].help_text='Неактивный рекрутер остаётся в истории, но не предлагается в новых карточках кандидатов.'
        self.fields['user'].label_from_instance=lambda user:user.get_full_name() or user.username
    def clean_name(self):
        name=' '.join((self.cleaned_data.get('name') or '').split())
        existing=RecruitmentSpecialist.objects.filter(company=self.instance.company).exclude(pk=self.instance.pk).values_list('name',flat=True)
        if any(value.casefold()==name.casefold() for value in existing):
            raise forms.ValidationError('Такой рекрутер уже есть в справочнике.')
        return name


class RequestForm(forms.ModelForm):
    revision = forms.IntegerField(min_value=0, widget=forms.HiddenInput, initial=0)
    class Meta:
        model = RecruitmentRequest
        fields = ['construction_object','position_ref','quantity','required_on','requirements']
        widgets = {'required_on':forms.DateInput(format='%Y-%m-%d', attrs={'type':'date'}),'requirements':forms.Textarea(attrs={'rows':3})}
    def __init__(self,*args,company,**kwargs):
        super().__init__(*args,**kwargs)
        self.instance.company=company
        self.fields['construction_object'].queryset=ConstructionObject.objects.filter(company=company).order_by('name')
        self.fields['position_ref'].queryset=Position.objects.filter(company=company).order_by('name','pk')
        self.fields['position_ref'].label='Должность';self.fields['position_ref'].empty_label='Выберите должность'
        self.fields['position_ref'].required=True
        if self.instance.pk and not self.instance.position_ref_id and self.instance.position:
            self.fields['position_ref'].help_text=format_html('В старой заявке указано: <strong>{}</strong>. Выберите соответствующую должность из справочника.',self.instance.position)
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
        return f'{candidate.full_name} — {position} · {candidate.get_stage_display()}' + (f' · {site}' if site else '')


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
        pool_queryset=TalentPoolCandidate.objects.filter(company=company,is_active=True).exclude(
                vacancy_candidates__status__in=[value for value,_ in RecruitmentCandidate.STATES if value not in RECRUITMENT_TERMINAL_STAGES|{'RESERVE','ARRIVED'}],
                vacancy_candidates__request__cancelled_at__isnull=True,
                vacancy_candidates__request__completed_at__isnull=True).distinct()
        if self.instance.pool_candidate_id:
            pool_queryset=TalentPoolCandidate.objects.filter(company=company).filter(
                Q(pk=self.instance.pool_candidate_id)|Q(pk__in=pool_queryset.values('pk'))).distinct()
        self.fields['pool_candidate']=TalentPoolChoiceField(label=original.label,required=False,
            queryset=pool_queryset.order_by('full_name','pk'),
            empty_label='Новый человек — заполнить вручную')
        self.pool_candidate_data={str(candidate.pk):{'full_name':candidate.full_name,'phone':candidate.phone or '',
            'notes':candidate.notes or ''} for candidate in self.fields['pool_candidate'].queryset}
        self.initial['request_position']=request_row.position
        self.order_fields(['request_position','pool_candidate','full_name','phone','status','ready_on','notes','documents','revision'])
        self.fields['full_name'].required=False
        self.fields['status'].choices=[(v,l) for v,l in RecruitmentCandidate.STATES if v not in RECRUITMENT_SYSTEM_STAGES]
        if self.instance.pk:self.initial['revision']=self.instance.revision
    def clean(self):
        data=super().clean();pool=data.get('pool_candidate')
        if pool:
            data['full_name']=pool.full_name;data['phone']=pool.phone or data.get('phone','');data['notes']=data.get('notes') or pool.notes
            if not self.instance.pk and data.get('status')=='NEW' and pool.stage not in RECRUITMENT_SYSTEM_STAGES|RECRUITMENT_TERMINAL_STAGES:
                data['status']=pool.stage
        if not (data.get('full_name') or '').strip():self.add_error('full_name','Выберите человека из пула или укажите ФИО.')
        return data


class TalentPoolForm(forms.ModelForm):
    stage_reason=forms.CharField(label='Причина изменения этапа',required=False,widget=forms.Textarea(attrs={'rows':2}),
        help_text='Например: повторно направлен на рассмотрение после прежнего отказа.')
    birth_date=forms.DateField(label='Дата рождения',required=False,widget=forms.DateInput(format='%Y-%m-%d',attrs={'type':'date'}))
    passport=forms.CharField(label='Серия и номер паспорта',required=False)
    skzi_until=forms.DateField(label='СКЗИ действует до',required=False,widget=forms.DateInput(format='%Y-%m-%d',attrs={'type':'date'}))
    pass_documents=forms.CharField(label='Документы направлены на пропуск',required=False)
    new_base_city=forms.CharField(label='Добавить базовый город',required=False,max_length=150,
        help_text='Заполните, только если нужного города нет в справочнике.')
    dudinka_arrival=forms.CharField(label='Прибытие в ОП Дудинка',required=False,disabled=True,
        help_text='Заполняется автоматически из заявки на заезд: по билету, затем заменяется фактической датой.')
    responsible_hr=forms.ModelChoiceField(label='Ответственный HR',required=False,
        queryset=RecruitmentSpecialist.objects.none(),empty_label='Не назначен')
    agency=forms.CharField(label='Название агентства',required=False)
    source_fields=('birth_date','passport','skzi_until','pass_documents','agency')
    class Meta:
        model=TalentPoolCandidate
        fields=['full_name','stage','position','construction_object','camp','phone','base_city','ready_for_arrival','ready_for_arrival_on',
            'manager_approved','manager_approved_on','security_approved','security_approved_on','medical_exam_passed','medical_exam_on',
            'responsible_hr','notes']
        widgets={'notes':forms.Textarea(attrs={'rows':3}),
            'ready_for_arrival_on':forms.DateInput(format='%Y-%m-%d',attrs={'type':'date'}),
            'manager_approved_on':forms.DateInput(format='%Y-%m-%d',attrs={'type':'date'}),
            'security_approved_on':forms.DateInput(format='%Y-%m-%d',attrs={'type':'date'}),
            'medical_exam_on':forms.DateInput(format='%Y-%m-%d',attrs={'type':'date'})}
    def __init__(self,*args,company,user,**kwargs):
        super().__init__(*args,**kwargs);self.instance.company=company
        if not self.instance.pk:self.instance.created_by=user
        editable_stages=[choice for choice in self.fields['stage'].choices if choice[0] not in RECRUITMENT_SYSTEM_STAGES]
        if self.instance.stage in RECRUITMENT_SYSTEM_STAGES:
            current=(self.instance.stage,dict(RECRUITMENT_STAGES)[self.instance.stage])
            self.fields['stage'].choices=[current,*editable_stages];self.fields['stage'].disabled=True
            self.fields['stage'].help_text='Системный этап изменяется автоматически по данным портала и ЗУП.'
        else:self.fields['stage'].choices=editable_stages
        self.fields['position'].queryset=Position.objects.filter(company=company).order_by('name','pk')
        self.fields['position'].empty_label='Выберите должность';self.fields['position'].required=True
        objects=ConstructionObject.objects.filter(company=company).select_related('parent').order_by('name','pk')
        self.fields['construction_object'].queryset=objects;self.fields['construction_object'].empty_label='Выберите объект / проект';self.fields['construction_object'].required=True
        self.fields['construction_object'].label='Строительный участок / объект'
        by_id={row.pk:row for row in objects}
        def object_label(row):
            names=[row.name];seen={row.pk};parent=row.parent_id
            while parent and parent not in seen and parent in by_id:
                seen.add(parent);names.append(by_id[parent].name);parent=by_id[parent].parent_id
            return ' → '.join(reversed(names))
        self.fields['construction_object'].label_from_instance=object_label
        camps=Camp.objects.filter(company=company,is_active=True)
        if self.instance.camp_id:camps=Camp.objects.filter(company=company).filter(Q(is_active=True)|Q(pk=self.instance.camp_id))
        self.fields['camp'].queryset=camps.order_by('name','pk');self.fields['camp'].empty_label='Не выбран'
        self.fields['camp'].required=False;self.fields['camp'].help_text='Необязательно. Выберите местонахождение, если оно уже известно.'
        self.fields['base_city'].queryset=BaseCity.objects.filter(company=company).order_by('name','pk')
        self.fields['base_city'].empty_label='Выберите базовый город';self.fields['base_city'].required=False
        specialists=RecruitmentSpecialist.objects.filter(company=company,is_active=True)
        if self.instance.responsible_hr_id:
            specialists=RecruitmentSpecialist.objects.filter(company=company).filter(
                Q(is_active=True)|Q(pk=self.instance.responsible_hr_id))
        self.fields['responsible_hr'].queryset=specialists.order_by('name','pk')
        source=self.instance.source_data or {}
        for field in self.source_fields:
            if field not in self.initial:self.initial[field]=source.get(field,'')
        raw_position=(source.get('position') or '').strip()
        if raw_position and not self.instance.position_id:
            self.fields['position'].help_text=format_html('В Excel указано: <strong>{}</strong>. Значение не найдено однозначно — выберите должность из справочника.',raw_position)
        raw_object=(source.get('construction_site') or '').strip()
        if raw_object and not self.instance.construction_object_id:
            self.fields['construction_object'].help_text=format_html('В Excel указано: <strong>{}</strong>. Выберите соответствующий объект портала.',raw_object)
        raw_city=(source.get('base_city') or '').strip()
        if raw_city and not self.instance.base_city_id:
            self.initial['new_base_city']=raw_city
            self.fields['new_base_city'].help_text=format_html('В Excel указано: <strong>{}</strong>. Проверьте название — при сохранении город добавится в справочник.',raw_city)
        arrival=self.instance.dudinka_arrival if self.instance.pk else None
        if arrival:self.initial['dudinka_arrival']=f"{arrival['date'].strftime('%d.%m.%Y')} · {'факт' if arrival['actual'] else 'по купленному билету'}"
        legacy_hints=(('ready_for_arrival','readiness','Готовность в Excel'),('manager_approved','manager_approval','Согласование руководителя в Excel'),
            ('security_approved','security_approval','Согласование СБ в Excel'),('medical_exam_passed','medical_exam','Медосмотр в Excel'))
        for field,source_key,label in legacy_hints:
            raw=(source.get(source_key) or '').strip()
            if raw and not self.initial.get(field) and not getattr(self.instance,field,False):
                self.fields[field].help_text=format_html('{}: <strong>{}</strong>. Подтвердите флажком и датой.',label,raw)
        self.order_fields(['full_name','stage','stage_reason','position','construction_object','camp','phone','birth_date','passport',
            'base_city','new_base_city','ready_for_arrival','ready_for_arrival_on','dudinka_arrival','manager_approved','manager_approved_on',
            'security_approved','security_approved_on','skzi_until','medical_exam_passed','medical_exam_on','pass_documents',
            'responsible_hr','agency','notes'])
    def save(self,commit=True):
        instance=super().save(commit=False);source=dict(instance.source_data or {})
        new_city=self.cleaned_data.get('new_base_city','')
        if new_city:
            instance.base_city=BaseCity.objects.filter(company=instance.company,name__iexact=new_city).first()
            if instance.base_city is None:instance.base_city=BaseCity.objects.create(company=instance.company,name=new_city)
        for field in self.source_fields:
            value=self.cleaned_data.get(field)
            source[field]=value.isoformat() if hasattr(value,'isoformat') else (value or '').strip()
        source['position']=instance.position.name if instance.position_id else source.get('position','')
        source['construction_site']=instance.construction_object.name if instance.construction_object_id else source.get('construction_site','')
        source['base_city']=instance.base_city.name if instance.base_city_id else source.get('base_city','')
        source['readiness']=instance.ready_for_arrival_on.isoformat() if instance.ready_for_arrival else source.get('readiness','')
        source['manager_approval']=instance.manager_approved_on.isoformat() if instance.manager_approved else source.get('manager_approval','')
        source['security_approval']=instance.security_approved_on.isoformat() if instance.security_approved else source.get('security_approval','')
        source['medical_exam']=instance.medical_exam_on.isoformat() if instance.medical_exam_passed else source.get('medical_exam','')
        source['responsible_hr']=instance.responsible_hr.name if instance.responsible_hr_id else ''
        source['full_name']=instance.full_name;source['phone']=instance.phone;source['notes']=instance.notes
        instance.source_data=source;instance.is_active=instance.stage not in POOL_UNAVAILABLE_STAGES
        if commit:instance.save()
        return instance
    def clean(self):
        data=super().clean()
        new_city=' '.join((data.get('new_base_city') or '').split());selected_city=data.get('base_city')
        if new_city and selected_city:self.add_error('new_base_city','Выберите город из списка или добавьте новый, но не оба варианта.')
        data['new_base_city']=new_city
        pairs=(('ready_for_arrival','ready_for_arrival_on'),('manager_approved','manager_approved_on'),
            ('security_approved','security_approved_on'),('medical_exam_passed','medical_exam_on'))
        for flag,date_field in pairs:
            if data.get(flag) and not data.get(date_field):self.add_error(date_field,'Укажите дату подтверждения.')
            if data.get(date_field) and not data.get(flag):self.add_error(flag,'Установите флажок для указанной даты.')
        obj,camp=data.get('construction_object'),data.get('camp')
        if obj and camp and not allowed_camps(self.instance.company,obj).filter(pk=camp.pk).exists():
            self.add_error('camp','Выберите местонахождение, связанное с указанным объектом.')
        from .recruitment import normalized_candidate_identity
        identity=normalized_candidate_identity(data.get('full_name'),data.get('phone'))
        if identity[0]:
            for existing in TalentPoolCandidate.objects.filter(company=self.instance.company).exclude(pk=self.instance.pk):
                if normalized_candidate_identity(existing.full_name,existing.phone)==identity:
                    raise forms.ValidationError(f'Такой кандидат уже есть в общем пуле: {existing.full_name}.')
        return data


class TalentPoolImportForm(forms.Form):
    file=forms.FileField(label='Файл Excel',help_text='Поддерживается XLSX. Будут собраны единые карточки из пяти листов воронки.')


class ArrivalForm(RevisionForm):
    candidate_name=forms.CharField(label='ФИО кандидата',disabled=True)
    project_name=forms.CharField(label='Проект',disabled=True)
    expected_on=forms.DateField(label='Плановая дата заезда',widget=forms.DateInput(format='%Y-%m-%d',attrs={'type':'date'}))
    camp=forms.ModelChoiceField(label='Местонахождение',queryset=None,required=False)
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
    camp=forms.ModelChoiceField(label='Местонахождение',queryset=Camp.objects.none(),required=False)
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
