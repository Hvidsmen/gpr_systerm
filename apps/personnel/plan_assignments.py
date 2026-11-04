"""Plan-role assignments with an optional one-time actual opening state."""
from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError, ObjectDoesNotExist
from django.db import transaction, IntegrityError
from django.db.models import Q, F, Exists, OuterRef
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_http_methods
from apps.accounts.models import Company
from apps.resources.models import Employee, Brigade
from apps.projects.models import ConstructionObject
from .models import Board, Person, StaffingGroup, StaffingSlot, ShiftPattern, Camp, CampStay, HousingEvent
from core.fact_history import history_actor
from . import housing
from .views import access


def object_scope(obj):
    ids={obj.pk}
    pairs=list(ConstructionObject.objects.filter(company_id=obj.company_id,project_id=obj.project_id).values_list('pk','parent_id'))
    while True:
        children={pk for pk,parent in pairs if parent in ids}
        if children<=ids:return ids
        ids.update(children)


def active_on(day):
    return (Q(assigned_from=None)|Q(assigned_from__lte=day)) & (Q(assigned_until=None)|Q(assigned_until__gte=day))


class InitialEmployeeSelect(forms.Select):
    def create_option(self,name,value,label,selected,index,subindex=None,attrs=None):
        option=super().create_option(name,value,label,selected,index,subindex,attrs)
        if hasattr(value,'instance') and value.instance.has_personnel_history:
            option['attrs']['data-has-personnel-history']='1'
        return option


class InitialStateForm(forms.Form):
    state=forms.ChoiceField(label='Начальный статус',required=False,choices=[('','Не указывать — только назначить'),('MV','На вахте'),('MO','МО — межвахтовый отдых')],widget=forms.Select(attrs={'data-initial-state':'','data-compact-select':''}))
    arrival_date=forms.DateField(label='Дата заезда',required=False,widget=forms.DateInput(format='%Y-%m-%d',attrs={'type':'date','data-initial-arrival':''}),help_text='При МО можно указать известную дату предыдущего заезда. Если она неизвестна, оставьте поле пустым.')
    departure_date=forms.DateField(label='Дата выезда на МО',required=False,widget=forms.DateInput(format='%Y-%m-%d',attrs={'type':'date','data-initial-departure':''}))

    def __init__(self,*args,as_of=None,**kwargs):
        super().__init__(*args,**kwargs);self.as_of=as_of
        for field in self.fields.values():field.widget.attrs.setdefault('class','form-select' if isinstance(field.widget,forms.Select) else 'form-control')
        for name in ('arrival_date','departure_date'):self.fields[name].widget.attrs['max']=str(timezone.localdate())

    def clean(self):
        data=super().clean();state=data.get('state');arrival,departure=data.get('arrival_date'),data.get('departure_date')
        if not state:
            if arrival or departure:raise forms.ValidationError('Выберите начальный статус для указанных дат.')
            return data
        if state=='MV':
            if not arrival:self.add_error('arrival_date','Для статуса «На вахте» укажите дату заезда.')
            if departure:self.add_error('departure_date','Для статуса «На вахте» дата выезда не указывается.')
        if state=='MO':
            if not departure:self.add_error('departure_date','Для МО укажите дату выезда.')
            if arrival and departure and arrival>=departure:self.add_error('arrival_date','Предыдущий заезд должен быть раньше выезда на МО.')
        for name in ('arrival_date','departure_date'):
            when=data.get(name)
            if when and when>timezone.localdate():self.add_error(name,'Фактическая дата не может быть в будущем.')
            elif when and self.as_of and when>self.as_of:self.add_error(name,'Начальное состояние должно быть известно на дату начала назначения. Уточните дату назначения или перемещения.')
        return data


def has_movements(person):
    return person.stays.exists() or person.housing_events.exists()


def initialize_state(user,row,data):
    """One explicit opening balance, not an unrestricted historical movement editor."""
    if not data or not data.get('state'):return False
    state,arrival,departure=data['state'],data.get('arrival_date'),data.get('departure_date')
    if row.initial_movement_id:
        event=row.initial_movement
        saved_arrival=event.date if event.kind=='ARRIVAL' else event.stay.arrived_on if event.stay_id else None
        saved_departure=event.date if event.kind=='DEPARTURE' else None
        saved_state='MV' if event.kind=='ARRIVAL' else 'MO'
        saved_camp=event.to_camp_id if event.kind=='ARRIVAL' else event.from_camp_id
        if (saved_state,saved_arrival,saved_departure,saved_camp)==(state,arrival,departure,row.housing_camp_id):return False
        raise ValidationError('Начальное состояние уже введено. Для изменений используйте историю и заезд / выезд.')
    if has_movements(row.person):raise ValidationError('У сотрудника уже есть история перемещений. Начальное состояние не сохранено; используйте заезд / выезд.')
    # Opening balances can predate the daily edit window. Existing movements are never rewritten here.
    # Validate again inside the transaction for callers outside the HTTP form.
    check=InitialStateForm({'state':state,'arrival_date':arrival or '', 'departure_date':departure or ''},as_of=row.assigned_from)
    if not check.is_valid():raise ValidationError(check.errors.as_text())
    comment='Начальное состояние при первом назначении на должность плана.'
    with history_actor(user,comment):
        stay=None
        if arrival:
            stay=CampStay.objects.create(company=user.company,person=row.person,camp=row.housing_camp,arrived_on=arrival,departed_on=departure if state=='MO' else None,comment=comment,recorded_by=user)
            event=HousingEvent.objects.create(company=user.company,person=row.person,date=arrival,kind='ARRIVAL',to_camp=row.housing_camp,stay=stay,comment=comment,recorded_by=user)
        if state=='MO':
            event=HousingEvent.objects.create(company=user.company,person=row.person,date=departure,kind='DEPARTURE',departure_destination='REST',from_camp=row.housing_camp,stay=stay,comment=comment,recorded_by=user)
        row.initial_movement=event;row.save(update_fields=['initial_movement','updated_at'])
    return True


class AssignmentForm(forms.Form):
    employee=forms.ModelChoiceField(label='Сотрудник',queryset=Employee.objects.none(),widget=InitialEmployeeSelect)
    planning_position=forms.ModelChoiceField(label='Должность из СМГ / ГПР',queryset=Brigade.objects.none())
    construction_object=forms.ModelChoiceField(label='Объект назначения',queryset=ConstructionObject.objects.none())
    housing_camp=forms.ModelChoiceField(label='ВЖГ назначения',queryset=Camp.objects.none(),help_text='Выберите ВЖГ, обслуживающий этот объект или его подобъекты. Начальные заезд / выезд ниже относятся к этому ВЖГ.')
    assigned_from=forms.DateField(label='Назначить с',widget=forms.DateInput(format='%Y-%m-%d',attrs={'type':'date'}))
    assigned_until=forms.DateField(label='По дату включительно',required=False,widget=forms.DateInput(format='%Y-%m-%d',attrs={'type':'date'}),help_text='Оставьте пустым для назначения без даты окончания.')
    pattern=forms.ModelChoiceField(label='График',queryset=ShiftPattern.objects.none(),required=False,empty_label='Пока без графика')
    version=forms.CharField(required=False,widget=forms.HiddenInput)

    def __init__(self,*args,company,row=None,**kwargs):
        super().__init__(*args,**kwargs)
        self.company=company;self.row=row
        history=Person.objects.filter(company=company,employee_id=OuterRef('pk')).filter(Q(stays__isnull=False)|Q(housing_events__isnull=False))
        employees=Employee.objects.filter(company=company)
        employees=employees.filter(pk=row.person.employee_id) if row else employees.filter(is_active=True)
        self.fields['employee'].queryset=employees.annotate(has_personnel_history=Exists(history)).select_related('position').order_by('last_name','first_name','pk')
        self.fields['employee'].label_from_instance=lambda e:e.display_name+(' · '+str(e.position) if e.position_id else '')+(' · № '+e.employee_number if e.employee_number else '')
        self.fields['employee'].help_text='Можно выбрать любого действующего сотрудника компании. Его штатная должность не изменится.'
        self.fields['planning_position'].queryset=Brigade.objects.filter(company=company).order_by('name','pk')
        self.fields['construction_object'].queryset=ConstructionObject.objects.filter(company=company).select_related('project').order_by('project__name','name','pk')
        self.fields['construction_object'].label_from_instance=lambda obj:f'{obj.project.name} · {obj.name}'
        self.fields['housing_camp'].queryset=Camp.objects.filter(company=company,is_active=True).order_by('name','pk')
        self.fields['pattern'].queryset=ShiftPattern.objects.filter(company=company).select_related('board').order_by('name','pk')
        self.fields['pattern'].label_from_instance=lambda p:f'{p} · {p.board.name}'
        if row:
            for field in ('planning_position','construction_object','housing_camp','assigned_from','assigned_until','pattern'):
                self.initial[field]=getattr(row,field+'_id',None) if field in ('planning_position','construction_object','housing_camp','pattern') else getattr(row,field)
            self.initial.update(employee=row.person.employee_id,version=row.updated_at.isoformat())
            self.fields['employee'].disabled=True;self.fields['version'].required=True
        for field in self.fields.values():field.widget.attrs.setdefault('class','form-select' if isinstance(field.widget,forms.Select) else 'form-control')

    def clean(self):
        data=super().clean();start,end=data.get('assigned_from'),data.get('assigned_until')
        if end and start and end<start:self.add_error('assigned_until','Дата окончания раньше начала назначения.')
        obj,camp=data.get('construction_object'),data.get('housing_camp')
        if obj and camp and not camp.object_links.filter(company=self.company,construction_object_id__in=object_scope(obj)).exists():
            self.add_error('housing_camp','Этот ВЖГ не обслуживает выбранный объект или его подобъекты.')
        return data


@transaction.atomic
def save_assignment(user,data,row_id=None,initial_state=None):
    # Serialize competing assignments, including SQLite where row locks are ignored.
    Company.objects.filter(pk=user.company_id).update(name=F('name'))
    employee=Employee.objects.select_for_update().get(company=user.company,pk=data['employee'].pk)
    if not employee.is_active and not row_id:raise ValidationError('Выберите действующего сотрудника.')
    role=Brigade.objects.get(company=user.company,pk=data['planning_position'].pk)
    obj=ConstructionObject.objects.get(company=user.company,pk=data['construction_object'].pk)
    camp=Camp.objects.get(company=user.company,is_active=True,pk=data['housing_camp'].pk)
    if not camp.object_links.filter(company=user.company,construction_object_id__in=object_scope(obj)).exists():raise ValidationError('ВЖГ не обслуживает объект назначения.')
    person=housing.person_for(employee)
    row=StaffingSlot.objects.select_for_update().get(pk=row_id,company=user.company,person=person,planning_position__isnull=False) if row_id else None
    if row and row.updated_at.isoformat()!=data['version']:raise ValidationError('Назначение уже изменено другим пользователем. Обновите страницу.')
    start,end=data['assigned_from'],data.get('assigned_until')
    overlap=person.assignments.filter(company=user.company,planning_position__isnull=False).exclude(pk=row_id).filter(Q(assigned_until=None)|Q(assigned_until__gte=start))
    if end:overlap=overlap.filter(Q(assigned_from=None)|Q(assigned_from__lte=end))
    existing=overlap.first()
    if existing:
        if not row and (existing.planning_position_id,existing.construction_object_id,existing.housing_camp_id,existing.assigned_from,existing.assigned_until)==(role.pk,obj.pk,camp.pk,start,end):
            initialize_state(user,existing,initial_state)
            return existing
        raise ValidationError('На этот период у сотрудника уже есть назначение. Измените его или сначала укажите дату окончания.')
    board=Board.objects.filter(company=user.company,name='Назначения по плану',import_digest='').order_by('pk').first()
    if board is None:board=Board.objects.create(company=user.company,name='Назначения по плану')
    group=board.groups.filter(position=role.name).first() or StaffingGroup.objects.create(company=user.company,board=board,position=role.name)
    pattern=data.get('pattern')
    if pattern:
        pattern=ShiftPattern.objects.get(company=user.company,pk=pattern.pk)
        if pattern.board_id!=board.pk:
            pattern=ShiftPattern.objects.create(company=user.company,board=board,name=pattern.name,kind=pattern.kind,calendar=pattern.calendar,on_days=pattern.on_days,off_days=pattern.off_days,cycle_codes=pattern.cycle_codes)
    if row is None:row=StaffingSlot(company=user.company,person=person)
    row.group=group;row.planning_position=role;row.construction_object=obj;row.housing_camp=camp
    row.assigned_from=start;row.assigned_until=end;row.pattern=pattern
    row.save()
    initialize_state(user,row,initial_state)
    person.schedule_slot=row;person.save(update_fields=['schedule_slot','updated_at'])
    return row


@login_required
@require_http_methods(['GET','POST'])
def edit(request,pk=None):
    access(request)
    row=get_object_or_404(StaffingSlot.objects.select_related('person'),company=request.user.company,pk=pk,planning_position__isnull=False) if pk else None
    initial={key:request.GET[key] for key in ('planning_position','construction_object','housing_camp','assigned_from') if key in request.GET}
    initial.setdefault('assigned_from',timezone.localdate())
    form=AssignmentForm(request.POST if request.method=='POST' else None,company=request.user.company,row=row,initial=initial)
    initial_form=InitialStateForm(request.POST if request.method=='POST' else None,prefix='initial') if row is None or not has_movements(row.person) else None
    if request.method=='POST':
        valid=form.is_valid()
        if initial_form:
            initial_form.as_of=form.cleaned_data.get('assigned_from')
            valid=initial_form.is_valid() and valid
        elif request.POST.get('initial-state'):
            form.add_error(None,'Начальное состояние уже введено. Используйте историю и заезд / выезд.');valid=False
    if request.method=='POST' and valid:
        try:saved=save_assignment(request.user,form.cleaned_data,pk,initial_form.cleaned_data if initial_form else None)
        except (ValidationError,IntegrityError,ObjectDoesNotExist) as exc:form.add_error(None,'; '.join(exc.messages) if isinstance(exc,ValidationError) else 'Данные изменились одновременно. Обновите страницу.')
        else:
            messages.success(request,'Назначение и начальное состояние сохранены.' if initial_form and initial_form.cleaned_data.get('state') else 'Сотрудник назначен. Фактический заезд оформляется отдельно.')
            return redirect(reverse('personnel:housing')+f'?camp={saved.housing_camp_id}&date={saved.assigned_from}#roster')
    return render(request,'personnel/plan_assignment.html',{'form':form,'initial_form':initial_form,'row':row,'personnel_section':'management','title':'Изменить назначение' if row else 'Назначить сотрудника'})
