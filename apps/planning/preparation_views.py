from datetime import date
from django import forms
from django.contrib import messages
from django.core.exceptions import ValidationError,PermissionDenied
from django.db import transaction
from django.shortcuts import get_object_or_404,render,redirect
from django.http import Http404
from django.urls import reverse
from django.views import View
from core.permissions import READ_ROLES,require_roles,scope_queryset,role_code
from apps.accounts.models import User
from .models import GlobalPlanVersion,DailySchedule
from .workspace_forms import ResourceAllocationSet
from .workspace_services import months_between
from . import preparation,estimate_review


class HandoffForm(forms.Form):
    manager=forms.ModelChoiceField(label='Руководитель проекта',queryset=User.objects.none())
    estimator=forms.ModelChoiceField(label='Сметчик',queryset=User.objects.none())
    def __init__(self,*args,company,**kwargs):
        super().__init__(*args,**kwargs)
        self.fields['manager'].queryset=User.objects.filter(company=company,is_active=True,role__code__in=['MANAGER','ADMIN'])
        self.fields['estimator'].queryset=User.objects.filter(company=company,is_active=True,role__code__in=['ESTIMATOR','ADMIN'])
        for field in self.fields.values():field.widget.attrs['data-native-select']='true'


class ManagerResourceSet(ResourceAllocationSet):
    extra=3


class PreparationView(View):
    kind=None
    def display(self,request,pk,bound=False):
        require_roles(request.user,READ_ROLES)
        model=DailySchedule if self.kind=='smg' else GlobalPlanVersion
        owner=get_object_or_404(scope_queryset(model.objects.all(),request.user),pk=pk)
        record=preparation.record(owner);action=request.POST.get('action') if bound else None
        can_handoff=role_code(request.user) in {'ADMIN','PLANNER'} and owner.status in {'DRAFT','REJECTED'}
        can_resources=owner.status in {'DRAFT','REJECTED'} and (role_code(request.user)=='ADMIN' or role_code(request.user)=='MANAGER' and record and record.manager_id==request.user.pk)
        if not bound and self.kind=='gpr' and can_resources and record and request.GET.get('legacy')!='1' and not (can_handoff and request.GET.get('handoff')=='1'):
            return redirect(reverse('planning:workspace_edit' if owner.workspace_id else 'planning:global_detail',args=[owner.pk])+'#work-resources')
        latest_review=owner.estimate_reviews.order_by('-pk').first()
        form=HandoffForm(request.POST if action=='handoff' else None,company=request.user.company,initial={'manager':record.manager_id if record else None,'estimator':latest_review.assigned_to_id if latest_review else None})
        resource_forms=None;months=[];month=None
        if self.kind=='gpr':
            start,end=estimate_review.review_period(owner);months=months_between(start,end)
            try:month=date.fromisoformat(request.POST.get('month') or request.GET.get('month') or months[0].isoformat())
            except ValueError:raise Http404('Неизвестный месяц.')
            if month not in months:raise PermissionDenied
            resource_forms=ManagerResourceSet(request.POST if action=='save_resources' else None,prefix='resources',queryset=owner.resource_allocations.filter(month=month),form_kwargs={'user':request.user,'version':owner,'month':month})
        resource_add_form=None
        if self.kind=='smg':
            from .daily_schedule_table_forms import AddEntryForm
            resource_add_form=AddEntryForm(request.POST if action=='add_resource' else None,schedule=owner,user=request.user)
            resource_add_form.fields['kind'].choices=[v for v in resource_add_form.fields['kind'].choices if v[0]!='work']
            resource_add_form.fields.pop('work',None)
        if bound:
            try:
                if action=='handoff':
                    if not can_handoff:raise PermissionDenied
                    if form.is_valid():
                        preparation.handoff(request.user,owner,form.cleaned_data['manager'],form.cleaned_data['estimator'])
                        messages.success(request,"План передан руководителю и сметчику. Задания созданы или обновлены.")
                        if self.kind=='gpr' and owner.workspace_id and request.POST.get('return_to_gpr')=='1':
                            return redirect(reverse('planning:workspace_edit',args=[owner.pk])+'?month='+month.isoformat())
                        if self.kind=='smg' and request.POST.get('return_to_smg')=='1':return redirect('planning:daily_schedule_detail',pk=owner.pk)
                        return redirect(request.path+'?handoff=1')
                elif action=='save_resources':
                    with transaction.atomic():
                        locked=model.objects.select_for_update().get(pk=owner.pk,company=request.user.company)
                        preparation.require_resource_input(request.user,locked)
                        if resource_forms is None:raise ValidationError('Используйте дневной ввод ресурсов СМГ.')
                        if resource_forms.is_valid():
                            crew_edits={f.instance.pk:f.cleaned_data['count'] for f in resource_forms if f.instance.pk and not f.cleaned_data.get('DELETE') and f.cleaned_data.get('kind')=='labor' and 'count' in f.changed_data}
                            removed=set(locked.resource_allocations.filter(pk__in=[f.instance.pk for f in resource_forms if f.cleaned_data.get('DELETE')],kind='labor').values_list('brigade_id',flat=True))
                            resource_forms.save()
                            from .table_resource_crew import sync_monthly
                            sync_monthly(request.user,locked,month,edited_counts=crew_edits,removed_brigades=removed,removal_actions=request.POST.get('crew_removal_actions'))
                            locked.snapshot={};locked.save(update_fields=['snapshot'])
                            messages.success(request,'Ресурсы сохранены. Проверка работ сметчиком сохраняет силу, если сами работы не менялись.');return redirect(request.path+'?month='+month.isoformat())
                elif action=='add_resource':
                    preparation.require_resource_editor(request.user,owner)
                    if resource_add_form and resource_add_form.is_valid():
                        from .daily_schedule_table import add_entry
                        add_entry(request.user,owner,resource_add_form.cleaned_data);return redirect(request.path)
                elif action=='ready':
                    preparation.ready(request.user,owner)
                    if self.kind=='smg' and request.POST.get('return_to_smg')=='1':return redirect('planning:daily_schedule_detail',pk=owner.pk)
                    return redirect(reverse('planning:workspace_edit' if owner.workspace_id else 'planning:global_detail',args=[owner.pk])+'#work-resources' if self.kind=='gpr' else request.path)
                elif action=='submit':
                    if self.kind=='gpr':
                        from .approval_workflow import transition
                        transition(owner,request.user,'submit')
                    else:
                        from .daily_schedule_approval import transition
                        transition(request.user,owner,'submit')
                        if request.POST.get('return_to_smg')=='1':return redirect('planning:daily_schedule_detail',pk=owner.pk)
                    messages.success(request,'Руководитель запустил согласование плана.');return redirect(request.path)
                else:raise ValidationError('Неизвестное действие.')
            except ValidationError as error:
                for message in error.messages:messages.error(request,message)
        latest=owner.estimate_reviews.order_by('-pk').first()
        from apps.resources.catalogue_review import context as catalogue_context
        return render(request,'planning/preparation.html',{**catalogue_context(request.user,owner),**preparation.task_context(request.user,owner),'owner':owner,'kind':self.kind,'preparation':record,'handoff_form':form,'resource_forms':resource_forms,'resource_add_form':resource_add_form,'resource_entries':owner.entries.exclude(kind='work') if self.kind=='smg' else [],'months':months,'month':month,'can_handoff':can_handoff,'show_handoff':can_handoff and (request.GET.get('handoff')=='1' or action=='handoff'),'can_resources':can_resources,'estimator_approved':estimate_review.check_valid(owner,latest),'resources_ready':bool(record and record.resources_ready_at and record.resources_fingerprint==preparation.resource_signature(owner))})
    def get(self,request,pk):return self.display(request,pk)
    def post(self,request,pk):return self.display(request,pk,True)
