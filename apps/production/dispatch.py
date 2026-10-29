"""Three independent fact directions sharing one object/day, without hours."""
from collections import defaultdict
from decimal import Decimal
from urllib.parse import urlencode
from django import forms
from django.contrib import messages
from django.core.exceptions import ValidationError, PermissionDenied
from django.db import transaction, IntegrityError
from django.db.models import Q
from django.forms import BaseModelFormSet, modelformset_factory
from django.shortcuts import render, redirect
from django.http import JsonResponse
from django.urls import reverse
from django.utils import timezone
from django.views import View
from core.mixins import CompanyRequiredMixin
from core.permissions import role_code, FACT_ROLES, require_roles, scope_queryset, DISPATCHER_ROLES
from apps.resources.models import Employee, Equipment,Brigade,EquipmentType
from apps.works.models import ProjectWork,ProjectWorkItem
from apps.planning.models import DailySchedule
from .models import DailyFact, EmployeeActivity, EquipmentActivity, FactSectionDay, FuelFact,LaborFact,EquipmentFact,DayActuation
from .forms import ObjectDateForm,DailyFactForm,DayActuationForm

DIRECTIONS={'DISPATCHER':'works','PERSONNEL_DISPATCHER':'labor','MECHANIZATION_DISPATCHER':'mechanization','FUEL_WAREHOUSE':'fuel'}
LABELS=dict(FactSectionDay.SECTIONS)

def sections_for(user):
    code=role_code(user)
    return [DIRECTIONS[code]] if code in DIRECTIONS else list(LABELS)

def required_fact_sections(user):
    return set(sections_for(user)) if user and role_code(user) in DISPATCHER_ROLES else None

def fact_sections_confirmed(user,company_id,object_id,day):
    if user and role_code(user)=='DISPATCHER':
        from .dispatch_models import ProductionEntryDay
        entry=ProductionEntryDay.objects.filter(company_id=company_id,construction_object_id=object_id,date=day,dispatcher=user).first()
        if entry:return bool(entry.confirmed_at)
    required=required_fact_sections(user)
    confirmed=set(FactSectionDay.objects.filter(company_id=company_id,construction_object_id=object_id,date=day,section__in=required or [],confirmed_at__isnull=False).values_list('section',flat=True))
    return bool(required) and required<=confirmed

def smg(obj,day):
    return DailySchedule.objects.filter(company=obj.company,construction_object=obj,month=day.replace(day=1),status='APPLIED').order_by('-revision').first()

def plans(obj,day):
    schedule=smg(obj,day);works={};resources={'labor':defaultdict(Decimal),'equipment':defaultdict(Decimal),'fuel':defaultdict(Decimal)}
    if schedule:
        for entry in schedule.entries.all():
            for row in entry.rows:
                if row.get('date')!=day.isoformat():continue
                if entry.kind=='work':
                    work_id=entry.metadata.get('id')
                    if work_id is None and entry.key.startswith('work:'):
                        try:work_id=int(entry.key.split(':')[1])
                        except ValueError:continue
                    key=(int(work_id),int(row['item_id']) if row.get('item_id') else None)
                    works[key]=works.get(key,Decimal(0))+Decimal(str(row.get('quantity') or 0))
                elif entry.kind=='fuel':
                    resources['fuel'][row.get('fuel_type','DIESEL')]+=Decimal(str(row.get('planned_liters') or 0))
                elif entry.kind in {'labor','equipment'}:
                    identity=row.get('brigade_id') if entry.kind=='labor' else row.get('equipment_type_id')
                    value=row.get('planned_workers',row.get('planned_count',0)) if entry.kind=='labor' else row.get('planned_count',0)
                    resources[entry.kind][identity]+=Decimal(str(value or 0))
    packages=set(ProjectWork.objects.filter(company=obj.company,kind='COMPOSITE',pk__in=[key[0] for key in works if key[1] is None]).values_list('pk',flat=True))
    works={key:value for key,value in works.items() if key[1] is not None or key[0] not in packages}
    return works,resources

class ScopeFormMixin:
    def __init__(self,*args,user,obj,day,work_plan=None,actuated=False,equipment_baselines=None,day_resource_plan=None,activity_work_keys=None,activity_choices=None,employee_pool=None,**kwargs):
        super().__init__(*args,**({'user':user,**kwargs} if isinstance(self,DailyFactForm) else kwargs))
        self.obj=obj;self.day=day;self.work_plan=work_plan or {};self.actuated=actuated
        self.equipment_baselines=equipment_baselines or {}
        self.day_resource_plan=day_resource_plan
        self.activity_work_keys=set(activity_work_keys or [])
        self.activity_choices=activity_choices or []
        self.employee_pool=employee_pool
        self.instance.company=user.company;self.instance.date=day
        if hasattr(self.instance,'construction_object_id'):self.instance.construction_object=obj
        for name in ['date','construction_object']:self.fields.pop(name,None)
        for field in self.fields.values():
            if isinstance(field,forms.ModelChoiceField):field.queryset=scope_queryset(field.queryset,user)
            if isinstance(field.widget,forms.Textarea):field.widget.attrs['rows']=2
        if 'work' in self.fields:
            self.fields['work'].queryset=ProjectWork.objects.filter(company=user.company,section__construction_object=obj,merged_source__isnull=True)
            self.fields['work_item'].queryset=ProjectWorkItem.objects.filter(company=user.company,project_work__section__construction_object=obj)
        for name in ['employee','equipment']:
            if name in self.fields:
                retained=[key[0] for key in self.equipment_baselines] if name=='equipment' else []
                self.fields[name].queryset=self.fields[name].queryset.filter(Q(is_active=True)|Q(pk=getattr(self.instance,name+'_id',None))|Q(pk__in=retained))
        for field in self.fields.values():field.widget.attrs.setdefault('class','form-control')

class WorkForm(ScopeFormMixin,DailyFactForm):
    work_claim_token=forms.CharField(required=False,widget=forms.HiddenInput)
    work_editing=forms.CharField(required=False,initial='1',widget=forms.HiddenInput)
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        self.fields['project_work'].queryset=self.fields['project_work'].queryset.filter(section__construction_object=self.obj)
        self.fields['work_item'].queryset=self.fields['work_item'].queryset.filter(project_work__section__construction_object=self.obj)
        def work_label(work):
            return ' → '.join(part for part in [work.complex_object.name if work.complex_object_id else work.section.name,work.name] if part)
        self.fields['project_work'].queryset=self.fields['project_work'].queryset.select_related('complex_object','section')
        self.fields['project_work'].label_from_instance=work_label
        self.fields['work_item'].queryset=self.fields['work_item'].queryset.select_related('project_work__complex_object','project_work__section')
        self.fields['work_item'].label_from_instance=lambda item:work_label(item.project_work)+' → '+item.name
        if not self.instance.pk:self.initial['actual_quantity']=None
    def clean(self):
        data=super().clean();work=data.get('project_work');item=data.get('work_item')
        if work:
            key=(work.pk,item.pk if item else None);plan=self.work_plan.get(key,Decimal(0))
            if self.instance.pk:
                original=(int(self.initial.get('project_work') or work.pk),int(self.initial['work_item']) if self.initial.get('work_item') else None)
                if original in self.work_plan and original!=key:self.add_error('project_work','Работу из плана СМГ нельзя заменить другой работой.')
            self.instance.outside_smg=key not in self.work_plan;self.instance.smg_plan_quantity=plan
            if (self.instance.outside_smg or data.get('actual_quantity')!=plan) and not data.get('deviation_reason') and not self.actuated:
                self.add_error('deviation_reason','Выберите причину отклонения или выполнения вне СМГ.')
            reason=data.get('deviation_reason')
            if reason and reason.name.strip().lower() in {'другое','прочее'} and not data.get('comment','').strip():self.add_error('comment','Для причины «Другое» нужен комментарий.')
        return data

class ActivityForm(ScopeFormMixin,forms.ModelForm):
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        actual=DailyFact.objects.filter(company=self.obj.company,date=self.day,project_work__section__construction_object=self.obj)
        work_ids={key[0] for key in self.work_plan}|set(actual.values_list('project_work_id',flat=True))
        item_ids={key[1] for key in self.work_plan if key[1]}|set(actual.exclude(work_item=None).values_list('work_item_id',flat=True))
        work_ids|={key[0] for key in self.activity_work_keys}
        item_ids|={key[1] for key in self.activity_work_keys if key[1]}
        if self.instance.work_id:work_ids.add(self.instance.work_id)
        if self.instance.work_item_id:item_ids.add(self.instance.work_item_id)
        if isinstance(self.instance,EquipmentActivity):
            work_ids |= {row['assignment'].work_id for row in self.equipment_baselines.values() if row['assignment'].work_id}
            item_ids |= {row['assignment'].work_item_id for row in self.equipment_baselines.values() if row['assignment'].work_item_id}
            if self.instance.work_id:work_ids.add(self.instance.work_id)
            if self.instance.work_item_id:item_ids.add(self.instance.work_item_id)
        self.fields['work'].queryset=self.fields['work'].queryset.filter(pk__in=work_ids)
        self.fields['work_item'].queryset=self.fields['work_item'].queryset.filter(pk__in=item_ids)
    def clean(self):
        data=super().clean()
        if isinstance(self.instance,EmployeeActivity):
            employee=data.get('employee')
            if employee:
                if not employee.position_id and not getattr(self,'production_entry',False):self.add_error('employee','Сначала назначьте сотруднику должность в справочнике.')
                elif not self.instance.pk or self.instance.employee_id!=employee.pk:self.instance.position=employee.position
                self.instance.entry_direction='works' if getattr(self,'production_entry',False) else 'labor'
            from .production_entry import is_general_employee
            if is_general_employee(employee) and not data.get('work') and not data.get('work_item'):
                data['general']=True
        general_allowed=isinstance(self.instance,EmployeeActivity) and (getattr(self,'production_entry',False) or is_general_employee(data.get('employee')))
        if data.get('general') and not general_allowed:
            schedule=smg(self.obj,self.day)
            if schedule:
                matched=[]
                for assignment in schedule.resource_assignments.filter(start_date__lte=self.day,end_date__gte=self.day):
                    if isinstance(self.instance,EmployeeActivity):
                        employee=data.get('employee');position=employee.position_id if employee else None
                        match=position and (assignment.position_id==position or any(m.get('position_id')==position for m in assignment.crew_snapshot))
                    else:
                        equipment=data.get('equipment');match=equipment and assignment.equipment_type_id==equipment.type_id
                    if match:matched.append(assignment)
                if matched and all(a.work_id for a in matched):self.add_error('general','В СМГ этот ресурс назначен на работы. Выберите работу либо укажите причину её отсутствия без отметки общего назначения.')
        return data

class PersonForm(ActivityForm):
    person_claim_token=forms.CharField(required=False,widget=forms.HiddenInput)
    person_editing=forms.CharField(required=False,initial='1',widget=forms.HiddenInput)
    prepared_from_previous=False
    def has_changed(self):
        return self.prepared_from_previous or super().has_changed()
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        from apps.resources.group_order import ordered
        self.fields['employee'].queryset=ordered(self.fields['employee'].queryset.select_related('position__planning_mapping__brigade__group'),group_path='position__planning_mapping__brigade__group',within=('position__name','last_name','first_name','pk'))
        self.fields['employee'].label_from_instance=lambda e: f"{e.display_name} · {e.position or 'Должность не указана'}"
        self.fields['general'].label='Работал без привязки к работе'
    def clean(self):
        if not getattr(self,'production_entry',False):self.instance.assignment_reviewed=True
        return super().clean()
    class Meta:
        model=EmployeeActivity
        fields=['employee','shift','general','work','work_item','equipment','activity']
        error_messages={'__all__':{'unique_together':'Этот сотрудник уже учтён в эту дату и смену. Измените существующую запись.'}}

class MachineForm(ActivityForm):
    prepared_from_assignment=False
    def has_changed(self):
        # Extra forms prefilled from an assignment must be saved even when
        # the user confirms the baseline without changing a single field.
        return self.prepared_from_assignment or super().has_changed()
    def clean(self):
        if self.cleaned_data.get('state')!='WORKING':
            self.cleaned_data.update(work=None,work_item=None,general=False)
        data=super().clean();equipment=data.get('equipment')
        baseline=self.equipment_baselines.get((equipment.pk,data.get('shift'))) if equipment else None
        if baseline and data.get('state')!=baseline['values']['state'] and data.get('state')!='WORKING' and data.get('activity','').strip()==baseline['values']['activity'].strip():
            self.add_error('activity','Укажите причину нового состояния за выбранный день.')
        if equipment and data.get('state')=='WORKING':
            resources=self.day_resource_plan if self.day_resource_plan is not None else plans(self.obj,self.day)[1]
            if equipment.type_id not in resources['equipment'] and not data.get('activity','').strip():self.add_error('activity','Для техники вне СМГ укажите причину привлечения.')
        return data
    class Meta:
        model=EquipmentActivity
        fields=['equipment','shift','state','general','work','work_item','activity']

class FuelForm(ScopeFormMixin,forms.ModelForm):
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        self.fields['actual_balance'].label='Остаток на конец, л'
        self.fields['actual_balance'].required=True
    class Meta:
        model=FuelFact
        fields=['fuel_type','opening_balance','received_liters','actual_liters','actual_balance','comment']
    def clean(self):
        data=super().clean();values=[data.get(f) for f in ['opening_balance','received_liters','actual_liters','actual_balance']]
        if all(v is not None for v in values) and values[0]+values[1]-values[2]!=values[3]:self.add_error('actual_balance','Остаток на конец должен равняться: начало + поступление − расход.')
        self.instance.equipment_ref=''
        return data

class ScopedSet(BaseModelFormSet):
    def add_fields(self,form,index):
        super().add_fields(form,index)
        form.fields['id'].queryset=self.get_queryset()

    def clean(self):
        super().clean()
        if self.model is not EmployeeActivity:return
        seen=set()
        for form in self.forms:
            data=getattr(form,'cleaned_data',{})
            if data.get('DELETE'):continue
            employee=data.get('employee')
            if employee:
                key=(employee.pk,data.get('shift'))
            elif not data and form.instance.pk:
                # Unedited saved rows are still part of this day's roster.
                key=(form.instance.employee_id,form.instance.shift)
            else:continue
            if key in seen:
                message='Этот сотрудник уже указан в этой смене. Измените существующую строку или уберите повтор.'
                form.add_error('employee',message)
                raise ValidationError(message,code='duplicate_employee_shift')
            seen.add(key)


def project_resources(obj,day,section):
    """Project detail into the existing report totals; never add a second total."""
    company=obj.company
    if section=='labor':
        from apps.resources.position_catalog import brigade_for
        counts=defaultdict(set)
        for row in EmployeeActivity.objects.filter(company=company,construction_object=obj,date=day).select_related('position'):
            if row.planning_position_id or row.position_id:counts[brigade_for(row.planning_position or row.position)].add(row.employee_id)
        required=plans(obj,day)[1]['labor']
        for brigade_id in Brigade.objects.filter(company=company,pk__in=required).values_list('pk',flat=True):counts.setdefault(brigade_id,set())
        LaborFact.objects.filter(company=company,construction_object=obj,date=day).delete()
        for brigade_id,employees in counts.items():LaborFact.objects.create(company=company,construction_object=obj,date=day,brigade_id=brigade_id,actual_workers=len(employees))
    elif section=='mechanization':
        rows=EquipmentActivity.objects.filter(company=company,construction_object=obj,date=day).select_related('equipment__type')
        counts={}
        for row in rows:
            key=(row.equipment.type.parent_id or row.equipment.type_id,(row.equipment.plate_number or row.equipment.inventory_number or row.equipment.vin)[:50])
            working=counts.setdefault(key,set())
            if row.state=='WORKING':working.add(row.equipment_id)
        required=plans(obj,day)[1]['equipment']
        present_types={key[0] for key in counts}
        for type_id in EquipmentType.objects.filter(company=company,pk__in=required).values_list('pk',flat=True):
            if type_id not in present_types:counts[(type_id,'')]=set()
        EquipmentFact.objects.filter(company=company,construction_object=obj,date=day).delete()
        for (equipment_type_id,number),machines in counts.items():EquipmentFact.objects.create(company=company,construction_object=obj,date=day,equipment_type_id=equipment_type_id,equipment_number=number,actual_count=len(machines))


def dispatcher_response(request,context,status=200):
    if request.method=='POST' and request.POST.get('autosave')=='1' and request.POST.get('section')=='works':
        errors=[]
        for group in context.get('groups',[]):
            for form in group['formset']:
                if form.errors:errors.append({'prefix':form.prefix,'fields':{name:list(messages) for name,messages in form.errors.items()}})
            errors.extend({'prefix':group['prefix'],'message':str(error)} for error in group['formset'].non_form_errors())
        actuation=context.get('actuation')
        if actuation and actuation.errors:errors.append({'prefix':'actuation','fields':{key:list(values) for key,values in actuation.errors.items()}})
        return JsonResponse({'error':context.get('error') or 'Изменения не сохранены. Проверьте заполнение выделенных записей.','errors':errors},status=max(status,400))
    return render(request,'production/dispatcher_day.html',context,status=status)


class DispatcherDayView(CompanyRequiredMixin,View):
    def get(self,request):
        if role_code(request.user)=='MECHANIZATION_DISPATCHER' and not request.GET.get('construction_object'):
            return redirect('production:transport_board')
        if role_code(request.user)=='FOREMAN':
            from .views.day_workspace import FactDayWorkspaceView
            return FactDayWorkspaceView.as_view()(request)
        return self.display(request)
    def post(self,request):
        require_roles(request.user,FACT_ROLES)
        if role_code(request.user)=='FOREMAN':
            from .views.day_workspace import FactDayWorkspaceView
            return FactDayWorkspaceView.as_view()(request)
        try:return self.display(request,bound=True)
        except ValidationError as exc:raise PermissionDenied("; ".join(exc.messages))
    from .entry_concurrency import sqlite_serialized
    @sqlite_serialized
    def display(self,request,bound=False):
        selector=ObjectDateForm(request.POST if bound else request.GET or None,user=request.user,initial={'date':timezone.localdate()})
        allowed=sections_for(request.user)
        section=(request.POST if bound else request.GET).get('section',allowed[0])
        if section not in allowed:raise PermissionDenied('Другое направление факта.')
        if bound and section=='mechanization' and any(key.startswith('fuel-') for key in request.POST):
            raise ValidationError('Форма устарела: ГСМ вынесены в отдельное направление. Обновите страницу; техника и ГСМ теперь сохраняются отдельно.')
        context={'selector':selector,'section':section,'tabs':[(s,LABELS[s]) for s in allowed],'editable':role_code(request.user) in FACT_ROLES}
        if not selector.is_bound or not selector.is_valid():return dispatcher_response(request,context,status=400 if bound else 200)
        obj=selector.cleaned_data['construction_object'];day=selector.cleaned_data['date']
        from core.fact_edit_window import window_context,require_fact_day
        context.update(window_context(request.user,day))
        context['editable']=context['editable'] and context['fact_day_editable']
        if bound:require_fact_day(request.user,day)
        from . import equipment_operations
        equipment_baselines,equipment_version=equipment_operations.prepare(obj,day) if section=='mechanization' else ({},'')
        if section=='mechanization':
            context['equipment_assignment_version']=request.POST.get('equipment_assignment_version','') if bound else equipment_version
            context['prepared_equipment_count']=len(equipment_baselines)
            context['equipment_assignments_url']=reverse('production:equipment_assignment_list')+'?'+urlencode({'construction_object':obj.pk,'date':day.isoformat()})
        from .day_reset import epoch as reset_epoch
        context['production_reset_epoch']=reset_epoch(obj,day)
        status,_=FactSectionDay.objects.get_or_create(company=obj.company,construction_object=obj,date=day,section=section)
        work_plan,resource_plan=plans(obj,day)
        from .production_entry import ProductionPersonForm, candidate_keys, previous_people, work_catalogue, GENERAL_PATH
        catalogue=work_catalogue(obj)
        own_entry=section=='works' and role_code(request.user)=='DISPATCHER'
        activity_keys=candidate_keys(obj,day,work_plan,catalogue,request.POST if bound else None,user=request.user if own_entry else None)
        people_status=FactSectionDay.objects.filter(company=obj.company,construction_object=obj,date=day,section='labor').first()
        context['labor_revision']=request.POST.get('labor_revision','') if bound else (people_status.revision if people_status else 0)
        context['production_work_choices']=catalogue
        context['own_entry']=own_entry
        if section=='works':
            from .dispatch_models import ProductionEntryDay
            context['my_production_entry']=ProductionEntryDay.objects.filter(company=obj.company,construction_object=obj,date=day,dispatcher=request.user).first()
            context['other_production_entries']=ProductionEntryDay.objects.filter(company=obj.company,construction_object=obj,date=day).exclude(dispatcher=request.user).select_related('dispatcher')
        existing_actuation=DayActuation.objects.filter(company=obj.company,construction_object=obj,date=day).first()
        initial_actuation={'actuated':bool(existing_actuation),'actuation_type':existing_actuation.actuation_type if existing_actuation else 'FULL_DAY','reason':existing_actuation.reason if existing_actuation else ''}
        actuation=DayActuationForm(request.POST if bound and section=='works' else None,initial=initial_actuation)
        actuated=bound and section=='works' and actuation.is_valid() and actuation.cleaned_data.get('actuated')
        data=request.POST.copy() if bound else None
        if actuated:
            for key in list(data):
                if key.startswith('works-') and key.endswith('-project_work') and data[key]:
                    prefix=key[:-len('project_work')];data[prefix+'actual_quantity']='0';data[prefix+'DELETE']=''
        context['actuation']=actuation if section=='works' else None

        from .employee_pool import prepare as prepare_employee_pool,labor_direction
        employee_pool=prepare_employee_pool(obj,day) if section=='works' else None
        if employee_pool:context['production_employee_locations']=employee_pool['locations']
        definitions=[('works',DailyFact,WorkForm),('labor',EmployeeActivity,ProductionPersonForm)] if section=='works' else [('labor',EmployeeActivity,PersonForm)] if section=='labor' else [('fuel',FuelFact,FuelForm)] if section=='fuel' else [('equipment',EquipmentActivity,MachineForm)]
        groups=[]
        for prefix,model,form in definitions:
            qs=model.objects.filter(company=obj.company,date=day)
            qs=qs.filter(project_work__section__construction_object=obj) if prefix=='works' else qs.filter(construction_object=obj)
            if prefix=='fuel':qs=qs.filter(equipment_ref='')
            if prefix=='equipment':qs=qs.select_related('equipment__type__parent__category','equipment__type__category','equipment__catalogue_group')
            if prefix=='labor':
                qs=qs.select_related('employee__position__planning_mapping__brigade__group','position').prefetch_related('work_links')
                if section=='works':qs=qs.exclude(labor_direction())
            initial=[]
            if prefix=='works':
                existing=set(DailyFact.objects.filter(company=obj.company,project_work__section__construction_object=obj,date=day).values_list('project_work_id','work_item_id'))
                available=set(ProjectWork.objects.filter(company=obj.company,section__construction_object=obj,merged_source__isnull=True).values_list('pk',flat=True))
                initial=[{'project_work':row['work'],'work_item':row['item'],'actual_quantity':None} for row in catalogue if (row['work'],row['item']) in activity_keys and (row['work'],row['item']) not in existing and row['work'] in available]
            carried_day=None
            if prefix=='labor' and section!='works' and context['editable'] and not qs.exists() and not (section=='works' and context['production_reset_epoch']) and (status.revision==0 or own_entry and (not context.get('my_production_entry') or not context['my_production_entry'].roster_started)):
                carried_day,initial=previous_people(obj,day,production=section=='works',user=request.user if own_entry else None)
            if prefix=='fuel':
                existing_types=set(qs.values_list('fuel_type',flat=True))
                initial=[{'fuel_type':kind,'opening_balance':None,'received_liters':None,'actual_liters':None,'actual_balance':None} for kind in resource_plan['fuel'] if kind not in existing_types]
            if prefix=='equipment':
                existing=set(qs.values_list('equipment_id','shift'))
                initial=[row['values'] for key,row in equipment_baselines.items() if key not in existing]
            extra=len(initial)+1
            if prefix=='equipment' and (equipment_baselines or existing):extra=len(initial)
            factory=modelformset_factory(model,form=form,formset=ScopedSet,extra=extra,can_delete=True,max_num=2000,absolute_max=2000)
            formset=factory(data,prefix=prefix,queryset=qs.order_by('pk'),initial=initial,form_kwargs={'user':request.user,'obj':obj,'day':day,'work_plan':work_plan,'actuated':bool(actuated),'equipment_baselines':equipment_baselines,'day_resource_plan':resource_plan,'activity_work_keys':activity_keys,'activity_choices':catalogue,'employee_pool':employee_pool})
            if prefix=='equipment':
                for row in formset:
                    try:key=(int(row.initial.get('equipment') or row['equipment'].value()),int(row.initial.get('shift') or row['shift'].value() or 1))
                    except (TypeError,ValueError):key=None
                    baseline=equipment_baselines.get(key)
                    if baseline:
                        row.fields['equipment'].queryset=row.fields['equipment'].queryset.filter(pk=key[0])
                        row.fields['equipment'].disabled=True
                        row.fields['shift'].disabled=True
                    row.prepared_from_assignment=bool(baseline and not row.instance.pk)
                    row.machine_label=baseline['assignment'].equipment if baseline else row.instance.equipment if row.instance.equipment_id else 'Разовая запись техники'
                    row.state_label=dict(EquipmentActivity.STATES).get(row['state'].value(),'Состояние не выбрано')
                    row.shift_label=row['shift'].value() or 1
                    row.baseline=baseline
                    row.daily_deviation=bool(baseline and row.instance.pk and any(getattr(row.instance,k+'_id' if k in {'equipment','work','work_item'} else k)!=v for k,v in baseline['values'].items()))
            if prefix=='works':
                from .work_entry_ui import prepare_rows
                work_groups,work_extra=prepare_rows(formset,obj,day,work_plan,catalogue)
            if prefix=='labor':
                for index,row in enumerate(formset):
                    if carried_day and not row.instance.pk and index < len(initial):
                        row.prepared_from_previous=True
                        row.hint=f'Подставлено из факта за {carried_day:%d.%m.%Y}. Проверьте сотрудника, смену и работы.'
                    if row.instance.pk:row.hint=f'Должность на дату факта: {row.instance.position or "не указана"}'
            ordered_rows=list(formset)
            if prefix=='labor' and section!='works':
                from apps.resources.group_order import employee_order
                from apps.resources.models import Employee
                employees={e.pk:e for e in Employee.objects.filter(company=obj.company).select_related('position__planning_mapping__brigade__group')}
                def person_order(row):
                    try:employee=employees.get(int(row['employee'].value() or 0))
                    except (TypeError,ValueError):employee=None
                    return (employee is None,employee_order(employee) if employee else (),str(row['shift'].value() or 1))
                ordered_rows.sort(key=person_order)
            if prefix=='equipment':
                from apps.resources.group_order import machine_order
                ordered_rows.sort(key=lambda row:(not bool(row.baseline or row.instance.pk),machine_order(row.baseline['assignment'].equipment if row.baseline else row.instance.equipment) if row.baseline or row.instance.pk else ()))
            groups.append({'prefix':prefix,'formset':formset,'ordered_rows':ordered_rows,'carried_day':carried_day,
                **({'work_groups':work_groups,'work_extra':work_extra} if prefix=='works' else {})})
        records={r.section:r for r in FactSectionDay.objects.filter(company=obj.company,construction_object=obj,date=day)}
        context['revision']=request.POST.get('revision','') if bound else status.revision
        context.update(groups=groups,status=status,object=obj,day=day,summary=[{'label':label,'confirmed':bool(records.get(key) and records[key].confirmed_at),'started':key in records} for key,label in LABELS.items()],deviation_comment=request.POST.get('deviation_comment','') if bound else status.deviation_comment)
        context['resource_plan']=resource_plan
        if section=='works':
            from .production_positions import context as position_context
            context.update(position_context(obj,day))
        if section in {'labor','mechanization'}:
            from django.db.models import Sum
            if section=='labor':
                totals=dict(LaborFact.objects.filter(company=obj.company,construction_object=obj,date=day).values('brigade_id').annotate(n=Sum('actual_workers')).values_list('brigade_id','n'))
                expected=resource_plan['labor'];catalog=Brigade
            else:
                totals=dict(EquipmentFact.objects.filter(company=obj.company,construction_object=obj,date=day).values('equipment_type_id').annotate(n=Sum('actual_count')).values_list('equipment_type_id','n'))
                expected=resource_plan['equipment'];catalog=EquipmentType
            names=dict(catalog.objects.filter(company=obj.company,pk__in=set(expected)|set(totals)).values_list('pk','name'))
            context['comparison']=[{'name':names.get(key,'Ресурс'),'plan':expected.get(key,0),'actual':totals.get(key,0)} for key in set(expected)|set(totals)]


        detail_model=EmployeeActivity if section in {'works','labor'} else EquipmentActivity
        legacy_model=LaborFact if section in {'works','labor'} else EquipmentFact
        legacy_revision=(people_status.revision if people_status else 0) if section=='works' else status.revision
        legacy_exists=section!='fuel' and legacy_revision==0 and not detail_model.objects.filter(company=obj.company,construction_object=obj,date=day).exists() and legacy_model.objects.filter(company=obj.company,construction_object=obj,date=day).exists()
        context['legacy_exists']=legacy_exists
        context['crew']=EmployeeActivity.objects.filter(company=obj.company,construction_object=obj,date=day,equipment__isnull=False).select_related('employee','equipment','position') if section=='mechanization' else []
        context['machine_states']=[(label,EquipmentActivity.objects.filter(company=obj.company,construction_object=obj,date=day,state=state).values('equipment_id').distinct().count()) for state,label in EquipmentActivity.STATES]
        if section=='works':
            from . import entry_concurrency
            from .production_entry import report_data
            report=report_data(obj,day)
            context['production_graph_saved']=report['works']
            context['production_report_people']=report['people']
            context['production_snapshot']=request.POST.get('production_snapshot','') if bound else entry_concurrency.snapshot(request.user,obj,day,groups)
            if bound:
                try:
                    if request.POST.get('action')=='confirm':
                        for group in groups:
                            if group['prefix']!='labor':continue
                            for form in group['formset']:
                                if form.data.get(form.add_prefix('person_editing'))=='0' and form['employee'].value() and not form['id'].value() and not form.data.get(form.add_prefix('DELETE')):
                                    raise ValidationError('Возьмите сотрудников из списка на вкладке «Люди» или уберите отсутствующих перед подтверждением. Черновик работ можно сохранить отдельно.')
                    production_baseline=entry_concurrency.prepare(request.user,obj,day,groups,context['production_snapshot'])
                except ValidationError as exc:
                    context['error']='; '.join(exc.messages)
                    return dispatcher_response(request,context,status=400)
        if bound:
            valid=[g['formset'].is_valid() for g in groups]
            if all(valid) and (section!='works' or actuation.is_valid()):
                people_changed=any(form.has_changed() for group in groups if group['prefix']=='labor' for form in group['formset'] if form.cleaned_data)
                try:
                    with transaction.atomic():
                        # Every operational write locks object before its day
                        # revision and machines; also serialize SQLite writers.
                        from django.db.models import F
                        type(obj).objects.filter(pk=obj.pk,company=obj.company).update(name=F('name'))
                        try:revision=int(request.POST.get('revision',''))
                        except (ValueError,TypeError):raise ValidationError('Неверная версия формы. Обновите страницу.')
                        if not FactSectionDay.objects.filter(pk=status.pk,**({} if section=='works' else {'revision':revision})).update(revision=F('revision')+1):raise ValidationError('Факт изменён в другой вкладке. Обновите страницу.')
                        type(obj).objects.select_for_update().get(pk=obj.pk)
                        machine_ids={key[0] for key in equipment_baselines}
                        for group in groups:
                            if group['prefix'] in {'labor','equipment'}:
                                machine_ids.update(form.cleaned_data['equipment'].pk for form in group['formset'] if form.cleaned_data.get('equipment'))
                        list(Equipment.objects.select_for_update().filter(company=obj.company,pk__in=machine_ids).order_by('pk'))
                        if section=='mechanization':
                            _,latest_equipment_version=equipment_operations.prepare(obj,day)
                            submitted=request.POST.get('equipment_assignment_version','')
                            if (equipment_baselines or submitted) and submitted!=latest_equipment_version:
                                raise ValidationError('Назначения или состояния техники изменились. Обновите день перед подтверждением.')
                        current=FactSectionDay.objects.select_for_update().get(pk=status.pk)
                        people_current=None
                        if section=='works':
                            try:people_revision=int(request.POST.get('labor_revision',''))
                            except (ValueError,TypeError):raise ValidationError('Неверная версия списка сотрудников. Обновите день.')
                            people_current,_=FactSectionDay.objects.get_or_create(company=obj.company,construction_object=obj,date=day,section='labor')
                            people_current=FactSectionDay.objects.select_for_update().get(pk=people_current.pk)
                            entry_concurrency.check_locked(groups,obj,day,production_baseline)
                            from .work_claims import check_form_claims
                            check_form_claims(request.user,obj,day,groups)
                        if section!='works':
                            from .work_claims import check_form_claims
                            check_form_claims(request.user,obj,day,groups)
                        require_fact_day(request.user,day)
                        latest_works,latest_resources=plans(obj,day)
                        if latest_works!=work_plan or latest_resources!=resource_plan:raise ValidationError('План СМГ изменился. Обновите страницу перед сохранением факта.')
                        if section=='works':
                            other_authors=DailyFact.objects.filter(company=obj.company,project_work__section__construction_object=obj,date=day).exclude(Q(reported_by=request.user)|Q(reported_by=None)).exists()
                            actuation_changed=bool(actuated)!=bool(existing_actuation) or bool(actuated and existing_actuation and any(actuation.cleaned_data.get(key)!=getattr(existing_actuation,key) for key in ('actuation_type','reason')))
                            if own_entry and other_authors and actuation_changed:
                                raise ValidationError('Актировка меняет весь объект. Когда факт вводят несколько диспетчеров, её должен оформить ответственный за общий день.')
                            if actuated:
                                if actuation_changed or not existing_actuation:
                                    record,_=DayActuation.objects.update_or_create(company=obj.company,construction_object=obj,date=day,defaults={'actuation_type':actuation.cleaned_data['actuation_type'],'reason':actuation.cleaned_data['reason'],'reported_by':request.user})
                                    record.full_clean()
                                zeroes=DailyFact.objects.filter(company=obj.company,project_work__section__construction_object=obj,date=day)
                                if own_entry:zeroes=zeroes.filter(Q(reported_by=request.user)|Q(reported_by=None))
                                zeroes.update(actual_quantity=0,actual_value=0)
                            elif actuation_changed:DayActuation.objects.filter(company=obj.company,construction_object=obj,date=day).delete()
                        if legacy_exists and not request.POST.get('replace_legacy') and (section!='works' or people_changed):raise ValidationError('На эту дату уже есть сводный факт. Подтвердите его замену детализацией по ФИО или машинам.')
                        if section=='works':
                            changed_authors={form.instance.reported_by_id for group in groups if group['prefix'] in {'works','labor'} for form in group['formset'] if form.instance.pk and form.has_changed() and form.instance.reported_by_id}
                            ProductionEntryDay.objects.filter(company=obj.company,construction_object=obj,date=day,dispatcher_id__in=changed_authors).exclude(dispatcher=request.user).update(confirmed_at=None)
                        for group in groups:
                            fs=group['formset'];rows=fs.save(commit=False)
                            for row in [*fs.deleted_objects,*rows]:
                                if isinstance(row,EquipmentActivity) and row.pk and hasattr(row,'transport_record'):
                                    raise ValidationError('Эта техника учтена в журнале ВЖГ. Измените её через «Техника на ВЖГ».')
                            for row in fs.deleted_objects:
                                if isinstance(row,DailyFact):
                                    original=DailyFact.objects.get(pk=row.pk,company=obj.company)
                                    if (original.project_work_id,original.work_item_id) in work_plan:raise ValidationError('Работу из плана СМГ удалить нельзя. Введите фактический объём, включая ноль, и причину отклонения.')
                                    from .collaboration import detach_work
                                    detach_work(original)
                                row.delete()
                            for row in rows:
                                if isinstance(row,(DailyFact,EmployeeActivity)) and not row.pk and not row.reported_by_id:row.reported_by=request.user
                                if isinstance(row,EquipmentActivity) and not row.pk:
                                    baseline=equipment_baselines.get((row.equipment_id,row.shift))
                                    if baseline:
                                        row.standing_assignment=baseline['assignment'];row.source_state=baseline['state_record']
                                if section=='works' and request.POST.get('collaborative')=='1' and isinstance(row,EmployeeActivity):
                                    from .collaboration import preserve_relations
                                    preserve_relations(row)
                                row.full_clean();row.save()
                            from .production_entry import sync_links
                            for form in fs:
                                if form.cleaned_data and not form.cleaned_data.get('DELETE'):sync_links(form)
                        if section=='mechanization':
                            present=set(EquipmentActivity.objects.filter(company=obj.company,construction_object=obj,date=day).values_list('equipment_id','shift'))
                            if set(equipment_baselines)-present:
                                raise ValidationError('Не заполнена техника из действующих назначений. Укажите состояние за день или завершите назначение; удаление строки не заменяет отклонение.')
                        previous_totals=[]
                        if section in {'labor','mechanization'}:
                            previous_totals=[{k:str(v) if isinstance(v,Decimal) or hasattr(v,'isoformat') else v for k,v in row.items()} for row in legacy_model.objects.filter(company=obj.company,construction_object=obj,date=day).values()]
                            project_resources(obj,day,section)
                        if section=='works' and people_changed:
                            previous_totals=[{k:str(v) if isinstance(v,Decimal) or hasattr(v,'isoformat') else v for k,v in row.items()} for row in LaborFact.objects.filter(company=obj.company,construction_object=obj,date=day).values()]
                            project_resources(obj,day,'labor')
                            people_current.revision+=1
                            people_current.confirmed_at=None;people_current.confirmed_by=None
                            people_current.save()
                        current.deviation_comment=request.POST.get('deviation_comment','').strip()[:4000]
                        if request.POST.get('action')=='confirm':
                            if section in {'labor','mechanization'}:
                                from django.db.models import Sum
                                if section=='labor':
                                    actual=dict(LaborFact.objects.filter(company=obj.company,construction_object=obj,date=day).values('brigade_id').annotate(n=Sum('actual_workers')).values_list('brigade_id','n'))
                                    expected=resource_plan['labor']
                                else:
                                    actual=dict(EquipmentFact.objects.filter(company=obj.company,construction_object=obj,date=day).values('equipment_type_id').annotate(n=Sum('actual_count')).values_list('equipment_type_id','n'))
                                    expected=resource_plan['equipment']
                                if expected and any(Decimal(actual.get(key,0))!=expected.get(key,Decimal(0)) for key in set(actual)|set(expected)) and not current.deviation_comment:raise ValidationError('Укажите причину отклонения численности или работающей техники от СМГ.')

                            if section=='fuel':
                                present_fuels=set(FuelFact.objects.filter(company=obj.company,construction_object=obj,date=day,equipment_ref='').values_list('fuel_type',flat=True))
                                if set(resource_plan['fuel'])-present_fuels:raise ValidationError('Заполните ГСМ объекта по всем видам топлива СМГ, включая явные нули.')
                            if section=='works':
                                present=set(DailyFact.objects.filter(company=obj.company,date=day,project_work__section__construction_object=obj).values_list('project_work_id','work_item_id'))
                                if not own_entry and set(work_plan)-present:raise ValidationError('Заполните все работы СМГ, включая нулевой факт и причины отклонения.')
                                from .dispatch_models import EmployeeWorkLink
                                people=EmployeeActivity.objects.filter(company=obj.company,construction_object=obj,date=day)
                                if own_entry:people=people.filter(reported_by=request.user)
                                if people.filter(assignment_reviewed=False).exists():raise ValidationError('На шаге «Связи» назначьте работы каждому сотруднику или отметьте работу без привязки.')
                                used=set(people.exclude(work=None).values_list('work_id','work_item_id'))
                                used.update(EmployeeWorkLink.objects.filter(company=obj.company,activity__in=people).values_list('work_id','work_item_id'))
                                if used-present:raise ValidationError('Заполните факт по работам, выбранным у сотрудников, или отметьте работу сотрудника без привязки.')
                            current.confirmed_at=timezone.now();current.confirmed_by=request.user
                        else:current.confirmed_at=None;current.confirmed_by=None
                        if section=='works':
                            entry,_=ProductionEntryDay.objects.get_or_create(company=obj.company,construction_object=obj,date=day,dispatcher=request.user)
                            entry.roster_started=entry.roster_started or people_changed or request.POST.get('action')=='confirm'
                            entry.revision+=1
                            entry.confirmed_at=timezone.now() if request.POST.get('action')=='confirm' else None
                            entry.save()
                            contributions=ProductionEntryDay.objects.filter(company=obj.company,construction_object=obj,date=day)
                            if request.POST.get('action')=='confirm' and not own_entry:contributions.update(confirmed_at=timezone.now())
                            if contributions.filter(confirmed_at=None).exists():current.confirmed_at=None;current.confirmed_by=None
                        current.save()
                        if section=='works' and request.POST.get('collaborative')=='1':
                            from .collaboration import automatic_readiness
                            automatic_readiness(obj,day,request.user)
                        if section=='works' and request.POST.get('claim_session') and request.POST.get('autosave')!='1':
                            from .work_claims import release_session
                            release_session(request.user,obj,day,request.POST.get('claim_session'))
                        require_fact_day(request.user,day)
                        from apps.audit.models import AuditLog
                        AuditLog.objects.create(user=request.user,action='UPDATE',entity_type='production.factsectionday',entity_id=str(current.pk),new_value={'section':section,'confirmed':bool(current.confirmed_at),'revision':current.revision,'previous_resource_totals':previous_totals},comment='Ввод факта за день по направлению')
                        from apps.planning.models import WorkTask
                        from apps.planning.task_services import state
                        for task in WorkTask.objects.filter(company=obj.company,construction_object=obj,fact_date=day,kind='FACT',status__in=['OPEN','DONE']).select_related('assigned_to__role'):
                            if task.assigned_to and ({section}|({'labor'} if section=='works' and people_changed else set())) & (required_fact_sections(task.assigned_to) or set()):
                                complete=fact_sections_confirmed(task.assigned_to,obj.company_id,obj.pk,day)
                                state(task,'DONE' if complete else 'OPEN',request.user,'Все необходимые направления подтверждены.' if complete else 'Требуется подтверждение направлений факта.')
                        if section=='works' and request.POST.get('autosave')=='1' and request.POST.get('action')!='confirm':
                            result=entry_concurrency.saved_state(request.user,obj,day,groups,production_baseline)
                            autosave_response=JsonResponse({**result,'revision':current.revision,'labor_revision':people_current.revision,'report':report_data(obj,day)})
                    if section=='works' and request.POST.get('autosave')=='1' and request.POST.get('action')!='confirm':return autosave_response
                    messages.success(request,'Факт подтверждён.' if request.POST.get('action')=='confirm' else 'Черновик факта сохранён.')
                    destination={'construction_object':obj.pk,'date':day.isoformat(),'section':section}
                    if section=='works' and request.POST.get('return_step')=='2':destination['entry_step']='2'
                    return redirect(reverse('production:fact_day_workspace')+'?'+urlencode(destination))
                except (ValidationError,IntegrityError) as exc:context['error']='; '.join(exc.messages) if isinstance(exc,ValidationError) else 'Повтор записи: сотрудник или техника уже учтены в этой смене. Обновите страницу и проверьте объект.'
            return dispatcher_response(request,context,status=400)
        return dispatcher_response(request,context)
