"""Named plan-role assignments are independent of HR position and actual arrival."""
from datetime import date
from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError, ObjectDoesNotExist
from django.db import transaction, IntegrityError
from django.db.models import Q, F
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_http_methods
from apps.accounts.models import Company
from apps.resources.models import Employee, Brigade
from apps.projects.models import ConstructionObject
from .models import Board, Person, StaffingGroup, StaffingSlot, ShiftPattern, Camp
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


class AssignmentForm(forms.Form):
    employee=forms.ModelChoiceField(label='Сотрудник',queryset=Employee.objects.none())
    planning_position=forms.ModelChoiceField(label='Должность из СМГ / ГПР',queryset=Brigade.objects.none())
    construction_object=forms.ModelChoiceField(label='Объект назначения',queryset=ConstructionObject.objects.none())
    housing_camp=forms.ModelChoiceField(label='ВЖГ назначения',queryset=Camp.objects.none(),help_text='Выберите ВЖГ, обслуживающий этот объект или его подобъекты. Назначение не оформляет заезд.')
    assigned_from=forms.DateField(label='Назначить с',widget=forms.DateInput(format='%Y-%m-%d',attrs={'type':'date'}))
    assigned_until=forms.DateField(label='По дату включительно',required=False,widget=forms.DateInput(format='%Y-%m-%d',attrs={'type':'date'}),help_text='Оставьте пустым для назначения без даты окончания.')
    pattern=forms.ModelChoiceField(label='График',queryset=ShiftPattern.objects.none(),required=False,empty_label='Пока без графика')
    version=forms.CharField(required=False,widget=forms.HiddenInput)

    def __init__(self,*args,company,row=None,**kwargs):
        super().__init__(*args,**kwargs)
        self.company=company;self.row=row
        self.fields['employee'].queryset=Employee.objects.filter(company=company).filter(Q(is_active=True)|Q(pk=row.person.employee_id if row else None)).select_related('position').order_by('last_name','first_name','pk')
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
def save_assignment(user,data,row_id=None):
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
        if not row and (existing.planning_position_id,existing.construction_object_id,existing.housing_camp_id,existing.assigned_from,existing.assigned_until)==(role.pk,obj.pk,camp.pk,start,end):return existing
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
    if request.method=='POST' and form.is_valid():
        try:saved=save_assignment(request.user,form.cleaned_data,pk)
        except (ValidationError,IntegrityError,ObjectDoesNotExist) as exc:form.add_error(None,'; '.join(exc.messages) if isinstance(exc,ValidationError) else 'Данные изменились одновременно. Обновите страницу.')
        else:
            messages.success(request,'Сотрудник назначен. Фактический заезд оформляется отдельно.')
            return redirect(reverse('personnel:housing')+f'?camp={saved.housing_camp_id}&date={saved.assigned_from}#roster')
    return render(request,'personnel/plan_assignment.html',{'form':form,'row':row,'personnel_section':'management','title':'Изменить назначение' if row else 'Назначить сотрудника'})
