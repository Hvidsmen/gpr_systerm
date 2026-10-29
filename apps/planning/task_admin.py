"""Audited administrative closure of inbox tasks; not workflow approval."""
import hashlib,json
from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core import signing
from django.core.exceptions import PermissionDenied,ValidationError
from django.db import transaction
from django.shortcuts import render,redirect
from django.utils import timezone
from django.views.decorators.http import require_POST
from core.permissions import require_roles,role_code
from apps.audit.models import AuditLog
from .models import WorkTask
from . import task_services as service

SALT='planning.admin-task-close.v1'
MAX_TASKS=5000

class CloseForm(forms.Form):
    reason=forms.CharField(label='Причина принудительного закрытия',min_length=3,max_length=2000,widget=forms.Textarea(attrs={'class':'form-control','rows':3}))


def selected(user,ids,lock=False):
    require_roles(user,{'ADMIN'})
    query=WorkTask.objects.filter(company=user.company,pk__in=ids).order_by('pk')
    if lock:query=query.select_for_update()
    tasks=list(query)
    if {task.pk for task in tasks}!=set(ids):raise PermissionDenied('Часть заданий недоступна или удалена.')
    return tasks


def fingerprint(tasks):
    return hashlib.sha256(json.dumps([(task.pk,task.status,task.title,str(task.updated_at),task.assigned_to_id,task.target_role) for task in tasks]).encode()).hexdigest()


@transaction.atomic
def close_tasks(user,ids,reason,signature):
    require_roles(user,{'ADMIN'})
    reason=reason.strip()
    if not 3<=len(reason)<=2000:raise ValidationError('Укажите причину закрытия от 3 до 2000 символов.')
    tasks=selected(user,ids,lock=True)
    if fingerprint(tasks)!=signature:raise ValidationError('Задания изменились. Проверьте обновлённый список и подтвердите закрытие повторно.')
    if any(task.kind=='CATALOGUE' and task.status in service.ACTIVE for task in tasks):
        raise ValidationError('Заявку справочника нужно подтвердить, заменить или отклонить в её карточке. Закрытие задания не заменяет проверку.')
    count=0
    for task in tasks:
        if task.status not in service.ACTIVE:continue
        service.state(task,'CLOSED_ADMIN',user,reason);count+=1
    AuditLog.objects.create(user=user,action='UPDATE',entity_type='planning.WorkTask',entity_id='bulk',old_value={'ids':ids},new_value={'status':'CLOSED_ADMIN','count':count,'reason':reason},comment='Принудительное закрытие заданий администратором')
    return count


def filtered(user,params):
    query=service.visible(user).filter(status__in=service.ACTIVE)
    query=service.filter_status(query,params.get('status','active'))
    query=service.filter_scope(query,user,params.get('scope','all'))
    if params.get('kind') in dict(WorkTask._meta.get_field('kind').choices):query=query.filter(kind=params['kind'])
    return query


@login_required
@require_POST
def force_close(request):
    require_roles(request.user,{'ADMIN'})
    error=None
    if request.POST.get('confirm'):
        try:
            payload=signing.loads(request.POST.get('token',''),salt=SALT,max_age=900)
            if (payload['user'],payload['company'])!=(request.user.pk,request.user.company_id):raise signing.BadSignature
            ids=payload['ids']
        except (signing.BadSignature,KeyError,TypeError):
            messages.error(request,'Подтверждение устарело. Выберите задания повторно.');return redirect('planning:task_list')
        form=CloseForm(request.POST)
        if form.is_valid():
            try:
                count=close_tasks(request.user,ids,form.cleaned_data['reason'],payload['signature'])
                messages.success(request,f'Принудительно закрыто заданий: {count}.');return redirect('planning:task_list')
            except ValidationError as exc:error='; '.join(exc.messages)
    else:
        if request.POST.get('mode')=='filtered':ids=list(filtered(request.user,request.POST).order_by('pk').values_list('pk',flat=True)[:MAX_TASKS+1])
        else:
            try:ids=sorted(set(map(int,request.POST.getlist('selected'))))
            except ValueError:raise PermissionDenied('Некорректный выбор заданий.')
        if len(ids)>MAX_TASKS:
            messages.error(request,'Сузьте фильтр: за один раз можно закрыть до 5000 заданий.');return redirect('planning:task_list')
        form=CloseForm()
    tasks=selected(request.user,ids)
    active=[task for task in tasks if task.status in service.ACTIVE]
    token=signing.dumps({'user':request.user.pk,'company':request.user.company_id,'ids':ids,'signature':fingerprint(tasks)},salt=SALT,compress=True)
    return render(request,'planning/task_force_close.html',{'tasks':tasks,'active_count':len(active),'form':form,'token':token,'error':error})
