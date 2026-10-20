from django.core import signing
from django.core.exceptions import ValidationError
from django.contrib import messages
from django.shortcuts import get_object_or_404,render,redirect
from django.urls import reverse
from django.views import View
from django.utils import timezone
from . import carryover_review as service
from .carryover_views import fingerprint
from .global_services import GlobalPlanService
from core.permissions import PLAN_ROLES,require_roles

SALT='planning.closed-carryover.v1'


class CarryoverTaskList(View):
    def get(self,request):
        require_roles(request.user,PLAN_ROLES)
        return redirect(reverse('planning:task_list')+'?kind=CARRYOVER')


class CarryoverTaskDetail(View):
    def display(self,request,pk,post=False):
        require_roles(request.user,PLAN_ROLES)
        task=get_object_or_404(service.tasks_for(request.user,due=False),pk=pk)
        error=None;rows=[];token=''
        try:
            rows=service.preview(task)
            if post:
                payload=signing.loads(request.POST.get('token',''),salt=SALT,max_age=3600)
                if payload['user']!=request.user.pk or payload['task']!=task.pk or payload['hash']!=fingerprint(rows):raise ValidationError('Факт или план изменились. Проверьте обновленный расчет заново.')
                if request.POST.get('action')=='submit':
                    if not task.correction_version_id:raise ValidationError('Сначала сохраните корректировку остатков.')
                    GlobalPlanService.transition(task.correction_version,request.user,'submit','Допсогласование после уточнения прогнозных остатков по закрытому факту.')
                    messages.success(request,'Корректировка отправлена на допсогласование.')
                else:
                    version=service.reconcile(request.user,task,{str(r['work_id']) for r in rows})
                    messages.success(request,'Остатки уточнены. Проверьте месячный план и отправьте корректировку на допсогласование.')
                    return redirect(reverse('planning:workspace_edit',args=[version.pk])+'?month='+task.month.isoformat())
                return redirect('planning:carryover_task_detail',pk=task.pk)
        except signing.BadSignature:error='Предварительный просмотр устарел. Проверьте расчет и повторите.'
        except ValidationError as exc:error='; '.join(exc.messages)
        token=signing.dumps({'user':request.user.pk,'task':task.pk,'hash':fingerprint(rows)},salt=SALT)
        return render(request,'planning/carryover_task.html',dict(task=task,rows=rows,token=token,error=error,closed=service.is_closed(task),today=timezone.localdate()))
    def get(self,request,pk):return self.display(request,pk)
    def post(self,request,pk):return self.display(request,pk,True)
