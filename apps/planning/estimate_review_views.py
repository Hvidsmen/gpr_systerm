from decimal import Decimal
import json
from django import forms
from django.db.models import Q
from django.contrib import messages
from django.core.exceptions import ValidationError, PermissionDenied
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views import View
from core.permissions import require_roles,role_code
from apps.accounts.models import User
from apps.works.models import ProjectWork
from .models import EstimateReviewTask, GlobalPlanVersion, DailySchedule
from . import estimate_review as service


class AssignForm(forms.Form):
    manager=forms.ModelChoiceField(label='Руководитель проекта',queryset=User.objects.none())
    estimator=forms.ModelChoiceField(label='Сметчик',queryset=User.objects.none())
    def __init__(self,*args,company,**kwargs):
        super().__init__(*args,**kwargs)
        self.fields['manager'].queryset=User.objects.filter(company=company,is_active=True,role__code__in=['MANAGER','ADMIN'])
        self.fields['estimator'].queryset=User.objects.filter(company=company,is_active=True).filter(Q(role__code__in=['ESTIMATOR','ADMIN'])|Q(is_superuser=True))


class WorkChangeForm(forms.Form):
    price=forms.DecimalField(label='Цена за единицу, ₽',max_digits=12,decimal_places=2,min_value=0)
    effective_from=forms.DateField(label='Действует с',widget=forms.DateInput(attrs={'type':'date'}))
    reason=forms.CharField(label='Основание изменения цены',max_length=500)


class PriceBatchForm(forms.Form):
    effective_from=forms.DateField(label='Дата действия новых цен',widget=forms.DateInput(format='%Y-%m-%d',attrs={'type':'date','class':'form-control'}))
    reason=forms.CharField(label='Основание для изменённых цен',max_length=500,widget=forms.TextInput(attrs={'class':'form-control','placeholder':'Например, согласованная расценка по смете'}))
    snapshot=forms.CharField(widget=forms.HiddenInput)


def tasks(user):
    require_roles(user,{'ADMIN','PLANNER','ESTIMATOR'})
    q=EstimateReviewTask.objects.filter(company=user.company).select_related('assigned_to','version__construction_object__parent','schedule__construction_object__parent','schedule__source__construction_object').defer('version__snapshot','schedule__source__snapshot')
    from core.permissions import scope_queryset
    q=scope_queryset(q,user)
    return q.filter(assigned_to=user) if role_code(user)=='ESTIMATOR' else q


class ReviewList(View):
    def get(self,request):
        return render(request,'planning/estimate_reviews.html',{'tasks':tasks(request.user).order_by('-pk')})


class ReviewAssign(View):
    def dispatch(self,request,*args,**kwargs):
        require_roles(request.user,{'ADMIN','PLANNER'})
        model=DailySchedule if kwargs['kind']=='smg' else GlobalPlanVersion
        self.obj=get_object_or_404(model.objects.defer('source__snapshot') if model==DailySchedule else model.objects.defer('snapshot'),pk=kwargs['pk'],company=request.user.company)
        return super().dispatch(request,*args,**kwargs)
    def get(self,request,**kwargs):
        return self.show(request,AssignForm(company=request.user.company))
    def show(self,request,form):
        return render(request,'planning/estimate_review_assign.html',{'form':form,'target':self.obj,'reviews':self.obj.estimate_reviews.select_related('assigned_to').order_by('-pk')})
    def post(self,request,**kwargs):
        form=AssignForm(request.POST,company=request.user.company)
        if form.is_valid():
            try:
                from .preparation import handoff
                handoff(request.user,self.obj,form.cleaned_data['manager'],form.cleaned_data['estimator'])
                task=self.obj.estimate_reviews.order_by('-pk').first()
                return redirect('planning:estimate_review_detail',pk=task.pk)
            except ValidationError as e:form.add_error(None,e)
        return self.show(request,form)


def can_review(user,task,obj):
    return (role_code(user)=='ADMIN' or role_code(user)=='ESTIMATOR' and task.assigned_to_id==user.pk) and task.status=='OPEN' and obj.status in {'DRAFT','REJECTED','SUBMITTED'}


def review_rows(task,obj):
    works=list(ProjectWork.objects.filter(company_id=task.company_id,pk__in=service.work_ids(obj)).select_related('section__construction_object','complex_object').prefetch_related('items','price_history'))
    start,end=service.review_period(obj)
    from django.db.models import Sum
    quantities=dict(service.review_allocations(obj).values('work_id').annotate(total=Sum('quantity')).values_list('work_id','total')) if isinstance(obj,GlobalPlanVersion) else {}
    from apps.works.prices import rate_on
    completed_valid=service.check_valid(obj,task)
    for work in works:
        work.review_decision=task.decisions.get(str(work.pk),{})
        work.review_valid=work.review_decision.get('approved') and (completed_valid or work.review_decision.get('signature')==service.decision_signature(work,obj))
        work.review_status='checked' if work.review_valid else 'rejected' if work.review_decision.get('approved') is False else 'pending'
        work.review_price=rate_on(sorted(work.price_history.all(),key=lambda row:(row.effective_from,row.pk)),start,work.unit_price)
        work.review_quantity=quantities.get(work.pk,Decimal(0)) if isinstance(obj,GlobalPlanVersion) else None
    works.sort(key=lambda work:(bool(work.review_valid),work.section.name.casefold(),work.name.casefold(),work.pk))
    return works,start,end


def live_state(user,task):
    task=get_object_or_404(tasks(user),pk=task.pk);obj=service.target(task)
    works,start,end=review_rows(task,obj);editable=can_review(user,task,obj)
    return {'task_status':task.status,'task_label':task.get_status_display(),'can_review':editable,
        'snapshot':service.price_edit_token(user,task) if editable else '',
        'checked':sum(bool(work.review_valid) for work in works),'total':len(works),
        'rows':[{'id':work.pk,'price':str(work.review_price),'status':work.review_status,'comment':work.review_decision.get('comment',''),'history':[{'date':row.effective_from.strftime('%d.%m.%Y'),'price':str(row.price),'reason':row.reason} for row in work.price_history.all()]} for work in works]}


class ReviewDetail(View):
    def get(self,request,pk):return self.show(request,get_object_or_404(tasks(request.user),pk=pk))

    def show(self,request,task):
        obj=service.target(task);works,start,end=review_rows(task,obj)
        from .estimate_review_tree import hierarchy
        editable=can_review(request.user,task,obj)
        form=PriceBatchForm(initial={'effective_from':start,'snapshot':service.price_edit_token(request.user,task) if editable else ''})
        return render(request,'planning/estimate_review_detail.html',{
            'task':task,'works':works,'review_tree':hierarchy(works),'review_object':obj.construction_object,
            'review_start':start,'review_end':end,'sections':sorted({work.section.name for work in works}),
            'checked_count':sum(bool(work.review_valid) for work in works),'total_count':len(works),
            'pending_count':sum(not bool(work.review_valid) for work in works),'can_review':editable,
            'price_batch_form':form,'events':task.events.select_related('actor').order_by('-pk')})

    def post(self,request,pk):
        task=get_object_or_404(tasks(request.user),pk=pk);action=request.POST.get('action');work_id=request.POST.get('work_id')
        ajax=request.headers.get('X-Requested-With')=='XMLHttpRequest'
        if action not in {'save_prices','bulk_approve','approve','reject','work_approve','work_reject','change'}:
            raise PermissionDenied('В проверке сметчика можно изменять и подтверждать только цены.')
        if request.POST.get('name') or request.POST.get('item_id') or request.POST.get('recommended_work'):
            raise PermissionDenied('Название, состав и замены работ недоступны в проверке цен.')
        try:
            if action=='save_prices':
                form=PriceBatchForm(request.POST)
                if not form.is_valid():return JsonResponse({'ok':False,'message':'Заполните дату и основание для изменённых цен.','errors':form.errors.get_json_data()},status=400)
                try:changes=json.loads(request.POST.get('changes','[]'))
                except (ValueError,TypeError):raise ValidationError('Не удалось прочитать изменённые цены.')
                count=service.change_prices(request.user,task,changes,**form.cleaned_data)
                message=f'Цены сохранены: {count}.'
            elif action=='bulk_approve':
                count=service.review_selected(request.user,task,request.POST.getlist('selected'));message=f'Цены подтверждены: {count}.'
            elif action in {'approve','reject'}:
                service.finish(request.user,task,action=='approve',request.POST.get('comment',''));message='Проверка цен завершена.' if action=='approve' else 'Проверка возвращена на доработку.'
            elif action=='work_approve':
                service.review_work(request.user,task,work_id,True,request.POST.get('comment',''));message='Цена подтверждена.'
            elif action=='work_reject':
                from .task_services import create_remark
                planner=task.created_by
                if not planner or role_code(planner) not in {'PLANNER','ADMIN'}:raise ValidationError('Назначьте замечание планировщику через создание задания.')
                create_remark(request.user,task,planner,'Уточнить цену работы',request.POST.get('comment',''),work_id=work_id);message='Замечание по цене направлено планировщику.'
            else:
                form=WorkChangeForm(request.POST)
                if not form.is_valid():return JsonResponse({'ok':False,'message':'Проверьте цену, дату и основание.','errors':form.errors.get_json_data()},status=400)
                service.change(request.user,task,work_id,**form.cleaned_data);message='Цена сохранена.'
        except ValidationError as exc:
            if ajax or action=='save_prices':
                errors={key:[{'message':str(text)} for text in value] for key,value in exc.message_dict.items()} if hasattr(exc,'message_dict') else {}
                return JsonResponse({'ok':False,'message':'; '.join(exc.messages),'errors':errors},status=400)
            messages.error(request,'; '.join(exc.messages))
        else:
            if ajax or action=='save_prices':return JsonResponse({'ok':True,'message':message,'review':live_state(request.user,task)})
            messages.success(request,message)
        return redirect('planning:estimate_review_detail',pk=task.pk)
