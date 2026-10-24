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
from django.urls import reverse
from django.utils import timezone
from django.views import View
from core.mixins import CompanyRequiredMixin
from core.permissions import role_code, FACT_ROLES, require_roles, scope_queryset
from apps.resources.models import Employee, Equipment,Brigade,EquipmentType
from apps.works.models import ProjectWork,ProjectWorkItem
from apps.planning.models import DailySchedule
from .models import DailyFact, EmployeeActivity, EquipmentActivity, FactSectionDay, FuelFact,LaborFact,EquipmentFact,DayActuation
from .forms import ObjectDateForm,DailyFactForm,DayActuationForm

DIRECTIONS={'DISPATCHER':'works','PERSONNEL_DISPATCHER':'labor','MECHANIZATION_DISPATCHER':'mechanization'}
LABELS=dict(FactSectionDay.SECTIONS)

def sections_for(user):
    code=role_code(user)
    return [DIRECTIONS[code]] if code in DIRECTIONS else list(LABELS)

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
    return works,resources

class ScopeFormMixin:
    def __init__(self,*args,user,obj,day,work_plan=None,actuated=False,**kwargs):
        super().__init__(*args,**({'user':user,**kwargs} if isinstance(self,DailyFactForm) else kwargs))
        self.obj=obj;self.day=day;self.work_plan=work_plan or {};self.actuated=actuated
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
                self.fields[name].queryset=self.fields[name].queryset.filter(Q(is_active=True)|Q(pk=getattr(self.instance,name+'_id',None)))
        for field in self.fields.values():field.widget.attrs.setdefault('class','form-control')

class WorkForm(ScopeFormMixin,DailyFactForm):
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        self.fields['project_work'].queryset=self.fields['project_work'].queryset.filter(section__construction_object=self.obj)
        self.fields['work_item'].queryset=self.fields['work_item'].queryset.filter(project_work__section__construction_object=self.obj)
        if not self.instance.pk:self.initial['actual_quantity']=None
    def clean(self):
        data=super().clean();work=data.get('project_work');item=data.get('work_item')
        if work:
            key=(work.pk,item.pk if item else None);plan=self.work_plan.get(key,Decimal(0))
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
        self.fields['work'].queryset=self.fields['work'].queryset.filter(pk__in=work_ids)
        self.fields['work_item'].queryset=self.fields['work_item'].queryset.filter(pk__in=item_ids)
    def clean(self):
        data=super().clean()
        if isinstance(self.instance,EmployeeActivity):
            employee=data.get('employee')
            if employee:
                if not employee.position_id:self.add_error('employee','Сначала назначьте сотруднику должность в справочнике.')
                elif not self.instance.pk or self.instance.employee_id!=employee.pk:self.instance.position=employee.position
        if data.get('general'):
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
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        self.fields['employee'].queryset=self.fields['employee'].queryset.select_related('position')
        self.fields['employee'].label_from_instance=lambda e: f"{' '.join(filter(None,[e.last_name,e.first_name,e.middle_name]))} · {e.position or 'Должность не указана'}"
    class Meta:
        model=EmployeeActivity
        fields=['employee','shift','general','work','work_item','equipment','activity']

class MachineForm(ActivityForm):
    def clean(self):
        data=super().clean();equipment=data.get('equipment')
        if equipment and data.get('state')=='WORKING':
            _,resources=plans(self.obj,self.day)
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


def project_resources(obj,day,section):
    """Project detail into the existing report totals; never add a second total."""
    company=obj.company
    if section=='labor':
        from apps.resources.position_catalog import brigade_for
        counts=defaultdict(set)
        for row in EmployeeActivity.objects.filter(company=company,construction_object=obj,date=day).select_related('position'):
            if row.position_id:counts[brigade_for(row.position)].add(row.employee_id)
        required=plans(obj,day)[1]['labor']
        for brigade_id in Brigade.objects.filter(company=company,pk__in=required).values_list('pk',flat=True):counts.setdefault(brigade_id,set())
        LaborFact.objects.filter(company=company,construction_object=obj,date=day).delete()
        for brigade_id,employees in counts.items():LaborFact.objects.create(company=company,construction_object=obj,date=day,brigade_id=brigade_id,actual_workers=len(employees))
    elif section=='mechanization':
        rows=EquipmentActivity.objects.filter(company=company,construction_object=obj,date=day).select_related('equipment')
        counts={}
        for row in rows:
            key=(row.equipment.type_id,row.equipment.plate_number)
            counts[key]=max(counts.get(key,0),int(row.state=='WORKING'))
        required=plans(obj,day)[1]['equipment']
        present_types={key[0] for key in counts}
        for type_id in EquipmentType.objects.filter(company=company,pk__in=required).values_list('pk',flat=True):
            if type_id not in present_types:counts[(type_id,'')]=0
        EquipmentFact.objects.filter(company=company,construction_object=obj,date=day).delete()
        for (equipment_type_id,number),count in counts.items():EquipmentFact.objects.create(company=company,construction_object=obj,date=day,equipment_type_id=equipment_type_id,equipment_number=number,actual_count=count)


class DispatcherDayView(CompanyRequiredMixin,View):
    def get(self,request):
        if role_code(request.user)=='FOREMAN':
            from .views.day_workspace import FactDayWorkspaceView
            return FactDayWorkspaceView.as_view()(request)
        return self.display(request)
    def post(self,request):
        require_roles(request.user,FACT_ROLES)
        if role_code(request.user)=='FOREMAN':
            from .views.day_workspace import FactDayWorkspaceView
            return FactDayWorkspaceView.as_view()(request)
        return self.display(request,bound=True)
    def display(self,request,bound=False):
        selector=ObjectDateForm(request.POST if bound else request.GET or None,user=request.user,initial={'date':timezone.localdate()})
        allowed=sections_for(request.user)
        section=(request.POST if bound else request.GET).get('section',allowed[0])
        if section not in allowed:raise PermissionDenied('Другое направление факта.')
        context={'selector':selector,'section':section,'tabs':[(s,LABELS[s]) for s in allowed],'editable':role_code(request.user) in FACT_ROLES}
        if not selector.is_bound or not selector.is_valid():return render(request,'production/dispatcher_day.html',context,status=400 if bound else 200)
        obj=selector.cleaned_data['construction_object'];day=selector.cleaned_data['date']
        status,_=FactSectionDay.objects.get_or_create(company=obj.company,construction_object=obj,date=day,section=section)
        work_plan,resource_plan=plans(obj,day)
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

        definitions=[('works',DailyFact,WorkForm)] if section=='works' else [('labor',EmployeeActivity,PersonForm)] if section=='labor' else [('equipment',EquipmentActivity,MachineForm),('fuel',FuelFact,FuelForm)]
        groups=[]
        for prefix,model,form in definitions:
            qs=model.objects.filter(company=obj.company,date=day)
            qs=qs.filter(project_work__section__construction_object=obj) if prefix=='works' else qs.filter(construction_object=obj)
            if prefix=='fuel':qs=qs.filter(equipment_ref='')
            initial=[]
            if prefix=='works':
                existing=set(qs.values_list('project_work_id','work_item_id'))
                available=set(ProjectWork.objects.filter(company=obj.company,section__construction_object=obj,merged_source__isnull=True).values_list('pk',flat=True))
                initial=[{'project_work':w,'work_item':i,'actual_quantity':None} for w,i in work_plan if (w,i) not in existing and w in available]
            if prefix=='fuel':
                existing_types=set(qs.values_list('fuel_type',flat=True))
                initial=[{'fuel_type':kind,'opening_balance':None,'received_liters':None,'actual_liters':None,'actual_balance':None} for kind in resource_plan['fuel'] if kind not in existing_types]
            factory=modelformset_factory(model,form=form,formset=ScopedSet,extra=len(initial)+1,can_delete=True,max_num=2000,absolute_max=2000)
            formset=factory(data,prefix=prefix,queryset=qs.order_by('pk'),initial=initial,form_kwargs={'user':request.user,'obj':obj,'day':day,'work_plan':work_plan,'actuated':bool(actuated)})
            if prefix=='works':
                from django.db.models import Sum
                cumulative={}
                for row in DailyFact.objects.filter(company=obj.company,project_work__section__construction_object=obj,date__lt=day).values('project_work_id','work_item_id').annotate(q=Sum('actual_quantity')):cumulative[(row['project_work_id'],row['work_item_id'])]=row['q']
                for row in formset:
                    try:key=(int(row['project_work'].value()),int(row['work_item'].value()) if row['work_item'].value() else None)
                    except (TypeError,ValueError):continue
                    work=ProjectWork.objects.filter(pk=key[0],company=obj.company).select_related('section__construction_object','complex_object').first()
                    if not work:continue
                    item=ProjectWorkItem.objects.filter(pk=key[1],company=obj.company).first() if key[1] else None
                    row.hint=f'{work.complex_object or "Самостоятельные работы"} → {work.name}'+(f' → {item.name}' if item else '')+f' · {item.unit if item else work.unit} · План СМГ: {work_plan.get(key,0)} · Ранее: {cumulative.get(key,0)}'+(' · Вне СМГ' if key not in work_plan else '')
            if prefix=='labor':
                for row in formset:
                    if row.instance.pk:row.hint=f'Должность на дату факта: {row.instance.position or "не указана"}'
            groups.append({'prefix':prefix,'formset':formset})
        records={r.section:r for r in FactSectionDay.objects.filter(company=obj.company,construction_object=obj,date=day)}
        context['revision']=request.POST.get('revision','') if bound else status.revision
        context.update(groups=groups,status=status,object=obj,day=day,summary=[{'label':label,'confirmed':bool(records.get(key) and records[key].confirmed_at),'started':key in records} for key,label in LABELS.items()],deviation_comment=request.POST.get('deviation_comment','') if bound else status.deviation_comment)
        context['resource_plan']=resource_plan
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


        detail_model=EmployeeActivity if section=='labor' else EquipmentActivity
        legacy_model=LaborFact if section=='labor' else EquipmentFact
        legacy_exists=section!='works' and status.revision==0 and not detail_model.objects.filter(company=obj.company,construction_object=obj,date=day).exists() and legacy_model.objects.filter(company=obj.company,construction_object=obj,date=day).exists()
        context['legacy_exists']=legacy_exists
        context['crew']=EmployeeActivity.objects.filter(company=obj.company,construction_object=obj,date=day,equipment__isnull=False).select_related('employee','equipment','position') if section=='mechanization' else []
        context['machine_states']=[(label,EquipmentActivity.objects.filter(company=obj.company,construction_object=obj,date=day,state=state).values('equipment_id').distinct().count()) for state,label in EquipmentActivity.STATES]
        if bound:
            valid=[g['formset'].is_valid() for g in groups]
            if all(valid) and (section!='works' or actuation.is_valid()):
                try:
                    with transaction.atomic():
                        # Claim with the first write: works on SQLite as well as row-locking databases.
                        from django.db.models import F
                        try:revision=int(request.POST.get('revision',''))
                        except (ValueError,TypeError):raise ValidationError('Неверная версия формы. Обновите страницу.')
                        if not FactSectionDay.objects.filter(pk=status.pk,revision=revision).update(revision=F('revision')+1):raise ValidationError('Факт изменён в другой вкладке. Обновите страницу.')
                        type(obj).objects.select_for_update().get(pk=obj.pk)
                        current=FactSectionDay.objects.select_for_update().get(pk=status.pk)
                        latest_works,latest_resources=plans(obj,day)
                        if latest_works!=work_plan or latest_resources!=resource_plan:raise ValidationError('План СМГ изменился. Обновите страницу перед сохранением факта.')
                        if section=='works':
                            if actuated:
                                record,_=DayActuation.objects.update_or_create(company=obj.company,construction_object=obj,date=day,defaults={'actuation_type':actuation.cleaned_data['actuation_type'],'reason':actuation.cleaned_data['reason'],'reported_by':request.user})
                                record.full_clean()
                                DailyFact.objects.filter(company=obj.company,project_work__section__construction_object=obj,date=day).update(actual_quantity=0,actual_value=0)
                            else:DayActuation.objects.filter(company=obj.company,construction_object=obj,date=day).delete()
                        if legacy_exists and not request.POST.get('replace_legacy'):raise ValidationError('На эту дату уже есть сводный факт. Подтвердите его замену детализацией по ФИО или машинам.')
                        for group in groups:
                            fs=group['formset'];rows=fs.save(commit=False)
                            for row in fs.deleted_objects:row.delete()
                            for row in rows:
                                if isinstance(row,DailyFact):row.reported_by=request.user
                                row.full_clean();row.save()
                        previous_totals=[]
                        if section in {'labor','mechanization'}:
                            previous_totals=[{k:str(v) if isinstance(v,Decimal) or hasattr(v,'isoformat') else v for k,v in row.items()} for row in legacy_model.objects.filter(company=obj.company,construction_object=obj,date=day).values()]
                            project_resources(obj,day,section)
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

                            if section=='mechanization':
                                present_fuels=set(FuelFact.objects.filter(company=obj.company,construction_object=obj,date=day,equipment_ref='').values_list('fuel_type',flat=True))
                                if set(resource_plan['fuel'])-present_fuels:raise ValidationError('Заполните ГСМ объекта по всем видам топлива СМГ, включая явные нули.')
                            if section=='works':
                                present=set(DailyFact.objects.filter(company=obj.company,date=day,project_work__section__construction_object=obj).values_list('project_work_id','work_item_id'))
                                if set(work_plan)-present:raise ValidationError('Заполните все работы СМГ, включая нулевой факт и причины отклонения.')
                            current.confirmed_at=timezone.now();current.confirmed_by=request.user
                        else:current.confirmed_at=None;current.confirmed_by=None
                        current.save()
                        from apps.audit.models import AuditLog
                        AuditLog.objects.create(user=request.user,action='UPDATE',entity_type='production.factsectionday',entity_id=str(current.pk),new_value={'section':section,'confirmed':bool(current.confirmed_at),'revision':current.revision,'previous_resource_totals':previous_totals},comment='Ввод факта за день по направлению')
                        from apps.planning.models import WorkTask
                        from apps.planning.task_services import state
                        for task in WorkTask.objects.filter(company=obj.company,construction_object=obj,fact_date=day,kind='FACT',status__in=['OPEN','DONE']).select_related('assigned_to__role'):
                            if task.assigned_to and DIRECTIONS.get(role_code(task.assigned_to))==section:state(task,'DONE' if current.confirmed_at else 'OPEN',request.user,'Факт направления подтверждён.' if current.confirmed_at else 'Факт изменён, требуется подтверждение.')
                    messages.success(request,'Факт подтверждён.' if request.POST.get('action')=='confirm' else 'Черновик факта сохранён.')
                    return redirect(reverse('production:fact_day_workspace')+'?'+urlencode({'construction_object':obj.pk,'date':day.isoformat(),'section':section}))
                except (ValidationError,IntegrityError) as exc:context['error']='; '.join(exc.messages) if isinstance(exc,ValidationError) else 'Повтор записи: сотрудник или техника уже учтены в этой смене. Обновите страницу и проверьте объект.'
            return render(request,'production/dispatcher_day.html',context,status=400)
        return render(request,'production/dispatcher_day.html',context)
