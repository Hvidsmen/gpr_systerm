from decimal import Decimal
from django import forms
from django.db.models import Q
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.views import View
from core.permissions import require_roles,role_code
from apps.accounts.models import User
from apps.works.models import ProjectWork
from .models import EstimateReviewTask, GlobalPlanVersion, DailySchedule
from . import estimate_review as service


class AssignForm(forms.Form):
    estimator=forms.ModelChoiceField(label='Сметчик',queryset=User.objects.none())
    def __init__(self,*args,company,**kwargs):
        super().__init__(*args,**kwargs)
        self.fields['estimator'].queryset=User.objects.filter(company=company,is_active=True).filter(Q(role__code__in=['ESTIMATOR','ADMIN'])|Q(is_superuser=True))


class WorkChangeForm(forms.Form):
    name=forms.CharField(label='Название работы',max_length=255)
    price=forms.DecimalField(label='Новая цена, ₽ (необязательно)',max_digits=12,decimal_places=2,min_value=0,required=False)
    effective_from=forms.DateField(label='Действует с',widget=forms.DateInput(attrs={'type':'date'}),required=False)
    reason=forms.CharField(label='Основание изменения цены',max_length=500,required=False)
    def clean(self):
        d=super().clean()
        if d.get('price') is not None and (not d.get('effective_from') or not d.get('reason')):raise forms.ValidationError('Для цены укажите дату действия и основание.')
        return d


class ItemForm(forms.ModelForm):
    class Meta:
        from apps.works.models import ProjectWorkItem
        model=ProjectWorkItem
        fields=['name','unit','quantity_per_unit','weight','load_profile','sequence','labor_hourly_rate','equipment_hourly_rate','fuel_price']
    def __init__(self,*args,company,**kwargs):
        super().__init__(*args,**kwargs)
        self.fields['load_profile'].queryset=self.fields['load_profile'].queryset.filter(company=company)


def tasks(user):
    require_roles(user,{'ADMIN','PLANNER','ESTIMATOR'})
    q=EstimateReviewTask.objects.filter(company=user.company).select_related('assigned_to','version__construction_object','schedule__source__construction_object').defer('version__snapshot','schedule__source__snapshot')
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
                task=service.assign(request.user,self.obj,form.cleaned_data['estimator'])
                return redirect('planning:estimate_review_detail',pk=task.pk)
            except ValidationError as e:form.add_error(None,e)
        return self.show(request,form)


class ReviewDetail(View):
    def get(self,request,pk):return self.show(request,get_object_or_404(tasks(request.user),pk=pk))
    def show(self,request,task,change_form=None,item_form=None,work_id=None):
        obj=service.target(task)
        works=list(ProjectWork.objects.filter(company=request.user.company,pk__in=service.work_ids(obj)).select_related('section__construction_object').prefetch_related('items','price_history'))
        from .estimate_catalog import alternatives
        replacements=list(alternatives(obj))
        from .models import MacroCarryoverWork
        version=obj.source if isinstance(obj,DailySchedule) else obj
        aliases=dict(MacroCarryoverWork.objects.filter(company=request.user.company,workspace_id=version.workspace_id).values_list('source_work_id','target_work_id'))
        for w in works:
            from .estimate_similarity import ranked_replacements
            w.replacement_options=ranked_replacements(w,[r for r in replacements if aliases.get(r.pk)!=w.pk])
            w.replacements=[row['work'] for row in w.replacement_options]
            w.change_form=WorkChangeForm(initial={'name':w.name})
            w.item_form=ItemForm(company=request.user.company)
            for item in w.items.all():item.edit_form=ItemForm(instance=item,company=request.user.company)
            w.review_decision=task.decisions.get(str(w.pk),{})
            w.review_valid=w.review_decision.get('approved') and w.review_decision.get('signature')==service.decision_signature(w,obj)
        works.sort(key=lambda w:(bool(w.review_valid),w.section.name.casefold(),w.name.casefold(),w.pk))
        start,end=service.review_period(obj)
        for w in works:
            w.review_status='checked' if w.review_valid else 'rejected' if w.review_decision.get('approved') is False else 'pending'
            w.review_quantity=sum((r.quantity for r in service.review_allocations(obj).filter(work=w)),Decimal(0)) if isinstance(obj,GlobalPlanVersion) else None
        extra={'review_start':start,'review_end':end,'sections':sorted({w.section.name for w in works}),'checked_count':sum(bool(w.review_valid) for w in works),'total_count':len(works)}
        active_ids={w.pk for w in works};pairs={}
        for w in works:
            for option in w.replacement_options:
                if option['score']<64:continue
                candidate=option['work'];key=tuple(sorted((w.pk,candidate.pk)))
                if key not in pairs:pairs[key]={'first':w,'second':candidate,'score':option['score'],'catalog':candidate.pk not in active_ids}
        extra['similar_works']=sorted(pairs.values(),key=lambda row:(-row['score'],row['first'].pk,row['second'].pk))[:20]
        return render(request,'planning/estimate_review_detail.html',{**extra,'task':task,'works':works,'can_review':(role_code(request.user)=='ADMIN' or role_code(request.user)=='ESTIMATOR' and task.assigned_to_id==request.user.pk) and task.status=='OPEN' and obj.status in {'DRAFT','REJECTED','SUBMITTED'},'change_form':change_form,'item_form':item_form,'editing_work_id':work_id,'events':task.events.select_related('actor').order_by('-pk'),'replacements':ProjectWork.objects.filter(company=request.user.company,section__construction_object_id=(obj.source.construction_object_id if isinstance(obj,DailySchedule) else obj.construction_object_id))})
    def post(self,request,pk):
        task=get_object_or_404(tasks(request.user),pk=pk);action=request.POST.get('action');work_id=request.POST.get('work_id')
        try:
            if action in {'composite_setup','composite_confirm'}:
                from apps.works.merge_views import MergeForm
                from apps.works.merge_service import source_works
                service.lock_task(request.user,task)
                works=source_works(request.user,request.POST.getlist('selected'))
                if not {w.pk for w in works}<=service.work_ids(service.target(task)):
                    from django.core.exceptions import PermissionDenied
                    raise PermissionDenied
                form=MergeForm(request.POST if action=='composite_confirm' else None,company=request.user.company,works=works,initial={'name':(works[0].name+' — составная')[:255],'section':works[0].section_id,'unit':'шт','allow_fractional':True})
                if action=='composite_confirm' and form.is_valid():
                    service.merge_composite(request.user,task,[w.pk for w in works],form.cleaned_data)
                    messages.success(request,'Комплекс работ создана в проверяемом периоде. Проверьте ее название, цену и работы в составе комплекса.')
                    return redirect('planning:estimate_review_detail',pk=task.pk)
                return render(request,'planning/estimate_review_merge.html',{'task':task,'form':form,'sources':works,'norm_rows':form.norm_rows})
            elif action=='bulk_merge':service.merge_duplicates(request.user,task,request.POST.getlist('selected'),request.POST.get('replacement'))
            elif action=='bulk_approve':service.review_selected(request.user,task,request.POST.getlist('selected'))
            elif action in {'approve','reject'}:service.finish(request.user,task,action=='approve',request.POST.get('comment',''))
            elif action=='work_approve':service.review_work(request.user,task,work_id,True,request.POST.get('comment',''))
            elif action=='work_reject':
                from .task_services import create_remark
                planner=task.created_by
                if not planner or role_code(planner) not in {'PLANNER','ADMIN'}:raise ValidationError('Назначьте замечание планировщику через кнопку создания задания.')
                comment=service.replacement_comment(request.user,task,work_id,request.POST.get('recommended_work'),request.POST.get('comment',''))
                create_remark(request.user,task,planner,'Исправить замечание по работе',comment,work_id=work_id)

            elif action=='replace':service.replace_work(request.user,task,work_id,request.POST.get('replacement'))
            elif action=='change':
                form=WorkChangeForm(request.POST)
                if not form.is_valid():return self.show(request,task,change_form=form,work_id=int(work_id))
                service.change(request.user,task,work_id,**form.cleaned_data)
            elif action=='item':
                from apps.works.models import ProjectWorkItem
                instance=get_object_or_404(ProjectWorkItem,pk=request.POST['item_id'],project_work_id=work_id,company=request.user.company) if request.POST.get('item_id') else ProjectWorkItem(company=request.user.company,project_work_id=work_id)
                form=ItemForm(request.POST,instance=instance,company=request.user.company)
                if not form.is_valid():return self.show(request,task,item_form=form,work_id=int(work_id))
                service.change(request.user,task,work_id,item_data={'id':instance.pk,**form.cleaned_data})
            else:raise ValidationError('Неизвестное действие.')
            messages.success(request,'Изменения сохранены.')
        except ValidationError as e:messages.error(request,'; '.join(e.messages))
        return redirect('planning:estimate_review_detail',pk=task.pk)
