from datetime import date
from uuid import uuid4
from urllib.parse import urlencode
from django import forms
from django.contrib import messages
from django.core.exceptions import ValidationError,PermissionDenied
from django.shortcuts import render,redirect,get_object_or_404
from django.urls import reverse
from django.views import View
from django.db import transaction
from django.utils import timezone
from core.permissions import role_code,ROLES,require_roles,scope_queryset
from apps.accounts.models import User
from apps.projects.models import ConstructionObject
from .models import WorkTask,EstimateReviewTask
from . import task_services as service


class CreateForm(forms.Form):
    title=forms.CharField(label='Задание',max_length=255)
    description=forms.CharField(label='Что нужно сделать',widget=forms.Textarea,max_length=8000)
    assigned_to=forms.ModelChoiceField(label='Исполнитель',queryset=User.objects.none())
    due_date=forms.DateField(label='Срок',required=False,widget=forms.DateInput(attrs={'type':'date'}))
    construction_object=forms.ModelChoiceField(label='Объект / субобъект',queryset=ConstructionObject.objects.none(),required=False)
    def __init__(self,*args,user,remark=False,**kwargs):
        super().__init__(*args,**kwargs)
        self.fields['assigned_to'].queryset=User.objects.filter(company=user.company,is_active=True,role__code__in=({'ADMIN','PLANNER'} if remark else ROLES-{'OBSERVER'}))
        self.fields['construction_object'].queryset=scope_queryset(ConstructionObject.objects.all(),user)
        if remark:self.fields.pop('construction_object')


def link(task):
    if task.kind=='FACT':return reverse('production:fact_day_workspace')+'?'+urlencode({'construction_object':task.construction_object_id,'date':task.fact_date.isoformat()})
    if task.kind=='SUBMISSION' and task.schedule_id:return reverse('planning:daily_schedule_detail',args=[task.schedule_id])
    if task.estimate_id:return reverse('planning:estimate_review_detail',args=[task.estimate_id])
    if task.carryover_id:return reverse('planning:carryover_task_detail',args=[task.carryover_id])
    if task.schedule_id:return reverse('planning:daily_schedule_detail',args=[task.schedule_id])
    if task.version_id:return reverse('planning:global_detail',args=[task.version_id])
    return None


class TaskList(View):
    def get(self,request):
        require_roles(request.user,ROLES)
        service.generate_daily(request.user)
        if role_code(request.user)=='ADMIN':
            for dispatcher in User.objects.filter(company=request.user.company,is_active=True,role__code='DISPATCHER').select_related('role','company'):service.generate_daily(dispatcher)
        service.sync_company(request.user.company)
        tasks=service.visible(request.user)
        mode=request.GET.get('status','active')
        if mode=='active':tasks=tasks.filter(status__in=service.ACTIVE)
        elif mode=='overdue':tasks=tasks.filter(status__in=service.ACTIVE,due_date__lt=timezone.localdate())
        elif mode in dict(WorkTask._meta.get_field('status').choices):tasks=tasks.filter(status=mode)
        scope=request.GET.get('scope','all' if role_code(request.user) in {'ADMIN','OBSERVER'} else 'mine')
        if scope=='outgoing':tasks=tasks.filter(created_by=request.user)
        elif scope=='incoming':tasks=tasks.filter(assigned_to=request.user)|tasks.filter(assigned_to__isnull=True,target_role=role_code(request.user))
        if request.GET.get('kind') in dict(WorkTask._meta.get_field('kind').choices):tasks=tasks.filter(kind=request.GET['kind'])
        from django.core.paginator import Paginator
        page=Paginator(tasks.select_related('assigned_to','created_by','construction_object').order_by('due_date','-created_at','-pk'),50).get_page(request.GET.get('page'))
        return render(request,'planning/task_list.html',{'page':page,'status':mode,'scope':scope,'kinds':WorkTask._meta.get_field('kind').choices,'can_create':role_code(request.user)!='OBSERVER','needs_objects':role_code(request.user)=='DISPATCHER' and not request.user.assigned_objects.exists()})


class TaskCreate(View):
    def display(self,request,bound=False):
        require_roles(request.user,ROLES-{'OBSERVER'})
        estimate=None;work_id=None
        if request.GET.get('estimate'):
            require_roles(request.user,{'ESTIMATOR','ADMIN'})
            query=EstimateReviewTask.objects.select_related('version','schedule').defer('version__snapshot').filter(company=request.user.company,status='OPEN')
            if role_code(request.user)!='ADMIN':query=query.filter(assigned_to=request.user)
            estimate=get_object_or_404(query,pk=request.GET['estimate'])
            from .estimate_review import work_ids,target
            if request.GET.get('work'):
                try:work_id=int(request.GET['work'])
                except ValueError:raise ValidationError('Неверная работа.')
                if work_id not in work_ids(target(estimate)):raise PermissionDenied
        initial={'assigned_to':estimate.created_by_id,'title':'Исправить работу' if work_id else 'Уточнить план'} if estimate else {}
        form=CreateForm(request.POST if bound else None,user=request.user,remark=bool(estimate),initial=initial)
        if bound and form.is_valid():
            d=form.cleaned_data
            try:
                if estimate:task=service.create_remark(request.user,estimate,d['assigned_to'],d['title'],d['description'],work_id=work_id,due=d['due_date'])
                else:
                    task=service.ensure(request.user.company,f'manual:{uuid4()}',kind='MANUAL',title=d['title'],description=d['description'],assigned_to=d['assigned_to'],created_by=request.user,due_date=d['due_date'],construction_object=d.get('construction_object'))
                return redirect('planning:task_detail',pk=task.pk)
            except ValidationError as e:form.add_error(None,e)
        return render(request,'planning/task_create.html',{'form':form,'estimate':estimate})
    def get(self,request):return self.display(request)
    def post(self,request):return self.display(request,True)


class TaskDetail(View):
    def get(self,request,pk):
        task=get_object_or_404(service.visible(request.user).select_related('assigned_to','created_by','construction_object','work'),pk=pk)
        return self.show(request,task)
    def show(self,request,task):
        from .daily_schedule_approval import next_action
        next_label=next_action(task.schedule) if task.kind=='SUBMISSION' and task.schedule_id else ''
        return render(request,'planning/task_detail.html',{'submission_action_label':next_label,'task':task,'target_url':link(task),'executor':role_code(request.user)!='OBSERVER' and service.is_executor(request.user,task),'author':role_code(request.user)!='OBSERVER' and task.created_by_id==request.user.pk,'events':task.events.select_related('actor'),'children':service.visible(request.user).filter(estimate_id=task.estimate_id,kind='REMARK') if task.kind=='ESTIMATE' and task.estimate_id else service.visible(request.user).filter(parent=task),'can_comment':role_code(request.user)!='OBSERVER','fact_sections':[('works','Работы'),('labor','Люди'),('equipment','Техника'),('fuel','ГСМ')]})
    def post(self,request,pk):
        if role_code(request.user)=='OBSERVER':raise PermissionDenied
        task=get_object_or_404(service.visible(request.user),pk=pk)
        action=request.POST.get('action');text=request.POST.get('text','')[:8000]
        try:
            if action in {'comment','resolve','accept','reopen','cancel'}:service.discussion_action(request.user,task,action,text)
            elif action=='fact_done':service.complete_fact(request.user,task,request.POST.getlist('confirmed'))
            elif action=='claim':
                estimate=service.claim_estimate(request.user,task)
                return redirect('planning:estimate_review_detail',pk=estimate.pk)
            elif action=='launch':
                if task.kind!='SUBMISSION' or task.status!='OPEN' or not service.is_executor(request.user,task):raise PermissionDenied
                from .daily_schedule_approval import transition
                if task.key.startswith('smg-apply:'):
                    from .daily_schedule import activate
                    activate(request.user,task.schedule)
                else:
                    from .daily_schedule_approval import requires_approval
                    if requires_approval(task.schedule):transition(request.user,task.schedule,'submit')
                    else:
                        from .daily_schedule import activate
                        activate(request.user,task.schedule)
            elif action in {'approve','reject'}:
                with transaction.atomic():
                    task=service.visible(request.user).select_for_update().select_related('version','review','schedule_review').defer('version__snapshot','review__snapshot','schedule_review__snapshot').get(pk=pk)
                    if task.kind!='APPROVAL' or task.status!='OPEN' or not service.is_executor(request.user,task):raise PermissionDenied
                    commands={'PRODUCTION_HEAD':'approve_production','HR_HEAD':'approve_hr','TECH_HEAD':'approve_tech','CEO':'approve'}
                    if task.schedule_id:
                        from .daily_schedule_approval import transition
                        schedule=task.schedule
                        if schedule.status!='SUBMITTED' or not task.schedule_review_id or schedule.approval_round!=task.schedule_review.number:raise ValidationError('Раунд согласования СМГ уже изменился.')
                        transition(request.user,schedule,commands[task.target_role] if action=='approve' else 'reject',text)
                    else:
                        version=task.version
                        if version.status!='SUBMITTED' or version.approval_round!=task.review.number:raise ValidationError('Раунд согласования уже изменился.')
                        from .global_services import GlobalPlanService
                        GlobalPlanService.transition(version,request.user,commands[task.target_role] if action=='approve' else 'reject',text)
            else:raise ValidationError('Неизвестное действие.')
            messages.success(request,'Задание обновлено.')
        except ValidationError as e:messages.error(request,'; '.join(e.messages))
        return redirect('planning:task_detail',pk=pk)
