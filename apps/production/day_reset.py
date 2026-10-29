"""Administrator-only reset of one object's production facts, with stale-form protection."""
from hashlib import sha256
import json
from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core import signing
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import F
from django.shortcuts import redirect,render
from django.http import HttpResponseBadRequest
from django.urls import reverse
from django.views.decorators.http import require_http_methods
from core.fact_history import history_actor
from core.permissions import require_roles
from apps.audit.models import AuditLog
from apps.projects.models import ConstructionObject
from .models import DailyFact,DayActuation
from .dispatch_models import EmployeeActivity,EmployeeWorkLink,WorkFactClaim,EmployeeFactClaim,FactSectionDay,ProductionEntryDay
from .entry_concurrency import sqlite_serialized

KIND='production.day_reset'

def identity(obj,day):return f'{obj.company_id}:{obj.pk}:{day.isoformat()}'
def epoch(obj,day):return AuditLog.objects.filter(entity_type=KIND,entity_id=identity(obj,day)).order_by('-pk').values_list('pk',flat=True).first() or 0


def records(obj,day):
    from .employee_pool import labor_direction
    people=EmployeeActivity.objects.filter(company=obj.company,construction_object=obj,date=day).exclude(labor_direction())
    return {'works':DailyFact.objects.filter(company=obj.company,date=day,project_work__section__construction_object=obj),
        'people':people,'links':EmployeeWorkLink.objects.filter(company=obj.company,activity__in=people),
        'actuations':DayActuation.objects.filter(company=obj.company,construction_object=obj,date=day)}


def fingerprint(obj,day):
    values={name:list(qs.order_by('pk').values_list('pk','updated_at')) for name,qs in records(obj,day).items()}
    return sha256(json.dumps([epoch(obj,day),values],sort_keys=True,default=str).encode()).hexdigest()


class ResetForm(forms.Form):
    version=forms.CharField(widget=forms.HiddenInput)
    reason=forms.CharField(label='Основание сброса',max_length=1000,widget=forms.Textarea(attrs={'rows':2}))
    confirm=forms.BooleanField(label='Подтверждаю удаление производственного факта за выбранную дату')


@login_required
@require_http_methods(['GET','POST'])
@sqlite_serialized
def reset(request):
    require_roles(request.user,{'ADMIN'})
    from .forms import ObjectDateForm
    selector=ObjectDateForm(request.POST if request.method=='POST' else request.GET,user=request.user)
    if not selector.is_valid():return HttpResponseBadRequest('Выберите доступный объект и дату.')
    obj=selector.cleaned_data['construction_object'];day=selector.cleaned_data['date']
    initial={'version':signing.dumps({'user':request.user.pk,'scope':identity(obj,day),'fingerprint':fingerprint(obj,day)},salt=KIND)}
    form=ResetForm(request.POST or None,initial=initial)
    if request.method=='POST' and form.is_valid():
        try:
            version=signing.loads(form.cleaned_data['version'],salt=KIND,max_age=900)
            if version.get('user')!=request.user.pk or version.get('scope')!=identity(obj,day):raise ValidationError('Подтверждение относится к другой форме. Откройте сброс заново.')
            with transaction.atomic(),history_actor(request.user,'Сброс производства: '+form.cleaned_data['reason']):
                ConstructionObject.objects.filter(pk=obj.pk,company=request.user.company).update(name=F('name'))
                if version.get('fingerprint')!=fingerprint(obj,day):raise ValidationError('Факт изменился после открытия подтверждения. Откройте сброс заново и проверьте состав записей.')
                selected=records(obj,day);deleted={name:list(qs.values_list('pk',flat=True)) for name,qs in selected.items()}
                production_employee_ids=list(selected['people'].values_list('employee_id',flat=True))
                affected=set()
                for person in selected['people'].select_related('position__planning_mapping','planning_position__planning_mapping'):
                    mapping=getattr(person.planning_position or person.position,'planning_mapping',None)
                    if mapping:affected.add(mapping.brigade_id)
                selected['people'].delete();selected['works'].delete();selected['actuations'].delete()
                # Recalculate affected headcounts without touching separate personnel facts.
                from collections import defaultdict
                from .models import LaborFact
                counts=defaultdict(set)
                for person in EmployeeActivity.objects.filter(company=obj.company,construction_object=obj,date=day).select_related('position__planning_mapping','planning_position__planning_mapping'):
                    mapping=getattr(person.planning_position or person.position,'planning_mapping',None)
                    if mapping:counts[mapping.brigade_id].add(person.employee_id)
                for fact in LaborFact.objects.filter(company=obj.company,construction_object=obj,date=day,brigade_id__in=affected):
                    count=len(counts[fact.brigade_id])
                    if not count:fact.delete()
                    elif fact.actual_workers!=count:fact.actual_workers=count;fact.save(update_fields=['actual_workers','updated_at'])
                for section in ['works','labor']:
                    state,_=FactSectionDay.objects.get_or_create(company=obj.company,construction_object=obj,date=day,section=section)
                    FactSectionDay.objects.filter(pk=state.pk).update(revision=F('revision')+1,confirmed_at=None,confirmed_by=None,**({'deviation_comment':''} if section=='works' else {}))
                ProductionEntryDay.objects.filter(company=obj.company,construction_object=obj,date=day).update(revision=F('revision')+1,confirmed_at=None,roster_started=True)
                WorkFactClaim.objects.filter(company=obj.company,construction_object=obj,date=day).delete()
                EmployeeFactClaim.objects.filter(company=obj.company,construction_object=obj,date=day,employee_id__in=production_employee_ids).delete()
                AuditLog.objects.create(user=request.user,action='DELETE',entity_type=KIND,entity_id=identity(obj,day),old_value=deleted,new_value={'reset':True},comment=form.cleaned_data['reason'])
                from apps.planning.models import WorkTask
                from apps.planning.task_services import state as task_state
                for task in WorkTask.objects.filter(company=obj.company,construction_object=obj,fact_date=day,kind='FACT',status='DONE').select_related('assigned_to__role'):
                    from .dispatch import fact_sections_confirmed
                    if not task.assigned_to or not fact_sections_confirmed(task.assigned_to,obj.company_id,obj.pk,day):task_state(task,'OPEN',request.user,'Администратор сбросил производственную часть факта.')
            messages.success(request,'Производственная часть за выбранную дату сброшена. Персонал отдельного направления, техника и ГСМ сохранены.')
            return redirect(reverse('production:fact_day_workspace')+f'?construction_object={obj.pk}&date={day}&section=works')
        except (signing.BadSignature,ValidationError) as exc:form.add_error(None,'; '.join(exc.messages) if isinstance(exc,ValidationError) else 'Подтверждение устарело. Откройте сброс заново.')
    counts={name:qs.count() for name,qs in records(obj,day).items()}
    return render(request,'production/day_reset.html',{'object':obj,'day':day,'form':form,'counts':counts})
