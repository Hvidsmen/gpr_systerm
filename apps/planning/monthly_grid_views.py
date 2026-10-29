import json
from datetime import date
from django.contrib import messages
from django.core.exceptions import ValidationError,PermissionDenied
from django.db import transaction, OperationalError
from core.sqlite_busy import retry_sqlite_busy, is_sqlite_busy, BUSY_MESSAGE
from django.http import JsonResponse,Http404
from django.shortcuts import render,redirect
from django.urls import reverse
from django.views import View
from core.permissions import require_planning_object,role_code
from .workspace_views import version_for
from .models import GlobalPlanVersion,WorkMonthAllocation
from . import monthly_grid


class WorkspaceMonths(View):
    def version(self,request,pk):
        version=version_for(request,pk)
        require_planning_object(request.user,version.construction_object_id)
        if version.workspace.parent_gpr_id:raise Http404('Помесячный ввод предназначен для ГПР.')
        return version

    def get(self,request,pk):
        version=self.version(request,pk)
        if version.status not in {'DRAFT','REJECTED'}:return redirect('planning:workspace_edit',pk=pk)
        return self.display(request,version)

    def display(self,request,version,add_form=None,add_kind=None,status=200):
        data=monthly_grid.matrix(request.user,version)
        from .resource_editor import context
        from .approval_workflow import panel
        from .gpr_ui import path_context
        info={**context(request.user,version),**panel(version,request.user)}
        from .work_selection_views import BranchForm
        from .daily_schedule_table_forms import AddEntryForm
        months=data['months'];initial={'months':[m.isoformat() for m in months]}
        branch=BranchForm(version=version,user=request.user,initial=initial,auto_id='months-work-%s')
        resource=AddEntryForm(schedule=version,user=request.user)
        for field in resource.fields.values():field.widget.attrs.setdefault('class','form-select' if hasattr(field,'queryset') or hasattr(field,'choices') else 'form-control')
        return render(request,'planning/workspace_months.html',{
            **info,**path_context(version,info,request.user),**data,'version':version,'object':version,'workspace':version.workspace,
            'plan_sections':data['sections'],'plan_can_add_resources':data['can_resource'],
            'plan_add_url':reverse('planning:workspace_months',args=[version.pk]),
            'branch_action':reverse('planning:workspace_months',args=[version.pk]),'branch_post_action':'add_work',
            'branch_open_level':request.POST.get('branch_level','object') if add_kind=='work' else '',
            'gpr_single_page':True,'gpr_period_grid':True,'table_editable':data['can_work'] or data['can_resource'],'plan_can_add_work':data['can_work'],
            'selected_month':months[0] if months else version.start_date.replace(day=1),
            'add_work_form':add_form if add_kind=='work' else branch,'add_resource_form':add_form if add_kind=='resource' else resource,
            'add_kind':add_kind,'grid_colspan':len(months)+2,'resource_month_options':[{'month':month,'checked':add_kind!='resource' or month.isoformat() in request.POST.getlist('months')} for month in months],
        },status=status)

    def add_form(self,request,version,action):
        can_work,can_resource=monthly_grid.permissions(request.user,version)
        if action=='add_work':
            if not can_work:raise PermissionDenied
            from .work_selection_views import BranchForm
            return BranchForm(request.POST,version=version,user=request.user,auto_id='months-work-%s')
        if action=='add_resource':
            if not can_resource:raise PermissionDenied
            from .daily_schedule_table_forms import AddEntryForm
            return AddEntryForm(request.POST,schedule=version,user=request.user)
        raise PermissionDenied('Неизвестное действие.')

    @retry_sqlite_busy
    @transaction.atomic
    def add_rows(self,request,version,action):
        locked=GlobalPlanVersion.objects.select_for_update().select_related('workspace').get(pk=version.pk,company=request.user.company)
        require_planning_object(request.user,locked.construction_object_id)
        # Rebuild choices on every attempt. A rolled-back attempt must not reuse
        # catalogue objects or permissions captured before another user's commit.
        form=self.add_form(request,locked,action)
        if not form.is_valid():
            raise ValidationError([str(error) for errors in form.errors.values() for error in errors])
        if action=='add_work':
            from .work_selection import resolve,extend_allocation
            for work,selected in resolve(form.works,form.cleaned_data['selection'],user=request.user):
                for month in form.cleaned_data['months']:
                    row,created=WorkMonthAllocation.objects.get_or_create(company=locked.company,version=locked,work=work,month=date.fromisoformat(month),defaults={'selected_item_ids':selected})
                    if not created:extend_allocation(row,selected)
        else:
            from .plan_workbench import add_month_resource
            months={m.isoformat():m for m in monthly_grid.editable_months(locked)}
            selected=request.POST.getlist('months')
            if not selected or any(m not in months for m in selected):raise ValidationError('Выберите доступные месяцы.')
            if form.cleaned_data['kind']=='work':raise ValidationError('Для работы используйте кнопку «Добавить работы».')
            for month in sorted(set(selected)):add_month_resource(request.user,locked,months[month],form.cleaned_data)
        locked.snapshot={};locked.save(update_fields=['snapshot'])

    def post(self,request,pk):
        version=self.version(request,pk)
        if request.content_type=='application/json':
            try:
                payload=json.loads(request.body)
                if not isinstance(payload,dict):raise ValidationError('Некорректные изменения.')
                count=monthly_grid.save(request.user,version,payload.get('changes'))
                return JsonResponse({'saved':True,'rows':count})
            except (json.JSONDecodeError,UnicodeDecodeError,ValidationError) as exc:return JsonResponse({'error':'; '.join(exc.messages) if isinstance(exc,ValidationError) else 'Некорректные изменения.'},status=400)
            except OperationalError as exc:
                if not is_sqlite_busy(exc):raise
                return JsonResponse({'error':BUSY_MESSAGE,'retryable':True},status=503,headers={'Retry-After':'3'})
        action=request.POST.get('action')
        form=self.add_form(request,version,action)
        status=400
        if form.is_valid():
            try:
                self.add_rows(request,version,action)
                messages.success(request,'Строки добавлены в выбранные месяцы. Существующие значения сохранены.')
                return redirect('planning:workspace_months',pk=version.pk)
            except ValidationError as exc:form.add_error(None,exc)
            except OperationalError as exc:
                if not is_sqlite_busy(exc):raise
                form.add_error(None,BUSY_MESSAGE);status=503
        response=self.display(request,version,form,'work' if action=='add_work' else 'resource',status)
        if status==503:response['Retry-After']='3'
        return response
