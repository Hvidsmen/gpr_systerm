from decimal import Decimal
import json
from django import forms
from django.forms import formset_factory
from django.core.exceptions import ValidationError, PermissionDenied
from django.contrib import messages
from django.shortcuts import get_object_or_404, render, redirect
from django.http import JsonResponse
from django.views import View
from core.permissions import require_roles, READ_ROLES, PLAN_ROLES, role_code,scope_queryset
from .models import GlobalPlanVersion, DailySchedule, DailyScheduleEntry
from . import daily_schedule as service


class MonthForm(forms.Form):
    month=forms.DateField(label='Месяц дневного СМГ',widget=forms.DateInput(attrs={'type':'date'},format='%Y-%m-%d'))


def summary(entry):
    before,after=service.totals(entry,entry.original),service.totals(entry,entry.rows)
    items={str(i['id']):i for i in entry.metadata.get('items',[])}
    result=[]
    for key in sorted(before.keys()|after.keys()):
        field=key.split(':')[-1]
        label=next(label for name,label,_ in service.FIELDS[entry.kind] if name==field)
        if entry.kind=='work':
            item=items.get(key.split(':')[0]);label=(item['name']+' · '+item['unit']) if item else 'Объём · '+entry.metadata.get('unit','')
        a,b=before.get(key,Decimal(0)),after.get(key,Decimal(0))
        if field=='planned_count':
            count=Decimal(service.bounds(entry.schedule.month)[1].day)
            a,b=a/count,b/count
            label='Средняя численность, чел.' if entry.kind=='labor' else 'Среднее количество, ед.'
        result.append(dict(label=label,before=a,after=b,delta=b-a,field=field,item_id=key.split(':')[0] if entry.kind=='work' else None))
    order={str(item['id']):index for index,item in enumerate(entry.metadata.get('items',[]))} if entry.kind=='work' else {field:index for index,(field,_,_) in enumerate(service.FIELDS[entry.kind])}
    result.sort(key=lambda row:order.get(row['item_id'] if entry.kind=='work' else row['field'],len(order)))
    if entry.kind=='work' and entry.metadata.get('kind')=='COMPOSITE':
        from types import SimpleNamespace
        generated=service.finalize_work(entry.metadata,entry.rows,SimpleNamespace(start_date=entry.schedule.month,end_date=service.bounds(entry.schedule.month)[1]),entry.metadata.get('history',[]))
        a=sum((Decimal(str(r['quantity'])) for r in entry.metadata.get('original_daily',[])),Decimal(0))
        b=sum((Decimal(str(r['quantity'])) for r in generated['daily']),Decimal(0))
        result.insert(0,dict(label='Готовые комплексы · '+entry.metadata.get('unit',''),before=a,after=b,delta=b-a,field='work_total',item_id=None))
    return result


class DailyScheduleCreate(View):
    def display(self,request,pk,bound=False):
        require_roles(request.user,PLAN_ROLES)
        source=get_object_or_404(GlobalPlanVersion.objects.defer('snapshot'),pk=pk,company=request.user.company)
        form=MonthForm(request.POST if bound else None,initial={'month':source.planning_month or (source.workspace.continuation_start if source.workspace_id else None) or source.start_date.replace(day=1)})
        if bound and form.is_valid():
            try:return redirect('planning:daily_schedule_detail',pk=service.create(request.user,source,form.cleaned_data['month']).pk)
            except ValidationError as exc:form.add_error(None,exc)
        return render(request,'planning/daily_schedule_create.html',dict(source=source,form=form,schedules=source.daily_schedules.order_by('-month','-revision')))
    def get(self,request,pk):return self.display(request,pk)
    def post(self,request,pk):return self.display(request,pk,True)


class DailyScheduleDetail(View):
    def get(self,request,pk,add_form=None):
        require_roles(request.user,READ_ROLES)
        schedule=get_object_or_404(scope_queryset(DailySchedule.objects.select_related('construction_object','source').defer('source__snapshot'),request.user),pk=pk)
        from .preparation import record
        preparation=record(schedule)
        resource_manager=role_code(request.user)=='ADMIN' or role_code(request.user)=='MANAGER' and preparation and preparation.manager_id==request.user.pk
        resource_editor=role_code(request.user)=='PLANNER' or resource_manager
        from .subobject_smg import parent_reference,comparison as parent_comparison
        is_subobject=parent_reference(schedule)
        entries=list(schedule.entries.select_related('gpr_work__section'))
        for entry in entries:
            entry.schedule=schedule
            entry.incomplete=entry.kind=='work' and len(entry.metadata.get('items',[]))<len(entry.metadata.get('valuation_items',entry.metadata.get('items',[])))
            entry.needs_gpr_mapping=is_subobject and entry.kind=='work' and (not entry.gpr_work_id or entry.gpr_work.section.construction_object_id!=schedule.source.construction_object_id)
            entry.can_edit=(entry.kind=='work' and role_code(request.user) in PLAN_ROLES) or (entry.kind!='work' and resource_editor)
            entry.comparison=summary(entry)
        from .daily_schedule_matrix import matrix
        editable=schedule.status=='DRAFT' and (role_code(request.user) in PLAN_ROLES or resource_editor)
        from .daily_schedule_approval import current,SECTIONS,requires_approval
        from .estimate_review import check_valid
        review=current(schedule)
        decisions=list(review.decisions.all()) if review else []
        approved={d.section for d in decisions if d.action=='APPROVE'}
        from core.plan_approval import REQUIRED_SECTIONS,SECTION_ACTIONS,DEPARTMENT_ROLES
        ready=REQUIRED_SECTIONS<=approved
        latest=schedule.estimate_reviews.order_by('-pk').first()
        checked=check_valid(schedule,latest)
        role=role_code(request.user)
        actions=[{'label':label,'action':SECTION_ACTIONS[section],'done':section in approved,'allowed':schedule.status=='SUBMITTED' and role in {assigned,'ADMIN'} and section not in approved} for section,label,assigned in SECTIONS if not (section in {'SECURITY','SBO'} and schedule.status in {'APPROVED','APPLIED','ARCHIVED'} and section not in approved)]
        approval={'smg_requires_approval':requires_approval(schedule),'smg_review':review,'smg_decisions':decisions,'smg_actions':actions,'smg_checked':checked,'smg_final_allowed':schedule.status=='SUBMITTED' and ready and role in {'CEO','ADMIN'},'smg_return_allowed':schedule.status=='SUBMITTED' and (role in DEPARTMENT_ROLES|{'ADMIN'} or role=='CEO' and ready)}
        from .daily_schedule_table_forms import AddEntryForm
        from .independent_smg_views import ReferenceForm
        reference_form=ReferenceForm(schedule=schedule,user=request.user) if editable and role_code(request.user) in PLAN_ROLES else None
        add_form=add_form or (AddEntryForm(schedule=schedule) if editable else None)
        if add_form and role_code(request.user)=='MANAGER':add_form.fields['kind'].choices=[v for v in add_form.fields['kind'].choices if v[0]!='work']
        from .object_plans import linked_plans
        from .smg_copy import CopyForm
        copy_form=CopyForm(user=request.user,schedule=schedule) if editable and (role_code(request.user) in PLAN_ROLES or schedule.source_id) else None
        if copy_form and not copy_form.fields['scope'].choices:copy_form=None
        from .smg_ui import editor_context
        from apps.works.models import ProjectWork
        parent_choices=ProjectWork.objects.filter(company=request.user.company,section__construction_object_id=schedule.source.construction_object_id,merged_source__isnull=True) if is_subobject else []
        from .work_selection_views import ScheduleBranchForm
        from django.urls import reverse
        editor=editor_context(request.user,schedule,approval)
        layout=matrix(schedule,entries)
        layout['sections'][1]['label']='Должности'
        parent_data=parent_comparison(schedule) if is_subobject else None
        if parent_data:parent_data['rows']=[row for row in parent_data['rows'] if not (row['kind'] in {'labor','equipment'} and row['unit']=='ч')]
        month_options=[{'month':s.month,'url':reverse('planning:daily_schedule_detail',args=[s.pk])} for s in editor['smg_month_choices']]
        unified={'plan_is_daily':True,'plan_kind_label':'СМГ','plan_sections':layout['sections'],'plan_month':schedule.month,
            'plan_month_options':month_options,'plan_years':sorted({s['month'].year for s in month_options}),
            'plan_can_add_work':editable and role_code(request.user) in PLAN_ROLES,'plan_can_add_resources':editable and bool(resource_editor),
            'plan_add_form':add_form,'plan_add_url':request.path,
            'branch_form':ScheduleBranchForm(schedule=schedule) if editable and role_code(request.user) in PLAN_ROLES else None,'branch_action':request.path}
        return render(request,'planning/daily_schedule_detail.html',dict(gpr_copy_form=copy_form,linked_object_plans=linked_plans(schedule,request.user),schedule=schedule,entries=entries,reference_form=reference_form,can_activate_smg=resource_manager and schedule.status=='APPROVED',can_edit=role_code(request.user) in PLAN_ROLES,table_editable=editable,add_form=add_form or (AddEntryForm(schedule=schedule) if editable else None),has_deviations=any(service.differences(e,e.rows) for e in entries),is_subobject_smg=is_subobject,has_direct_reference=bool(schedule.source_id) and not is_subobject,parent_work_choices=parent_choices,parent_comparison=parent_data,**approval,**layout,**editor,**unified))

    def post(self,request,pk):
        require_roles(request.user,PLAN_ROLES | {'MANAGER'})
        schedule=get_object_or_404(DailySchedule,pk=pk,company=request.user.company)
        if schedule.status!='DRAFT':raise PermissionDenied('СМГ зафиксирован. Создайте новую редакцию.')
        from .daily_schedule_table import save_table,add_entry
        if request.POST.get('action')=='remove_entry':
            from .plan_workbench import remove_smg_entry
            try:
                remove_smg_entry(request.user,schedule,int(request.POST.get('entry_id','')),int(request.POST.get('revision','')),removal_actions=request.POST.get('crew_removal_actions'))
                messages.success(request,'Строка удалена из СМГ. Справочник сохранён.')
            except (ValueError,TypeError,DailyScheduleEntry.DoesNotExist):messages.error(request,'Строка недоступна. Обновите страницу.')
            except ValidationError as exc:messages.error(request,'; '.join(exc.messages))
            return redirect('planning:daily_schedule_detail',pk=pk)
        if request.content_type=='application/json':
            try:
                payload=json.loads(request.body)
                if not isinstance(payload,dict):raise ValidationError('Неверный запрос.')
                count=save_table(request.user,schedule,payload.get('entries'))
                return JsonResponse({'saved':count})
            except (ValidationError,ValueError) as exc:
                return JsonResponse({'error':'; '.join(exc.messages) if isinstance(exc,ValidationError) else 'Неверный формат запроса.'},status=400)
        from .daily_schedule_table_forms import AddEntryForm
        form=AddEntryForm(request.POST,schedule=schedule)
        if form.is_valid():
            try:
                add_entry(request.user,schedule,form.cleaned_data)
                messages.success(request,'Строка добавлена. Заполните дневные значения в таблице и сохраните изменения.')
                return redirect('planning:daily_schedule_detail',pk=pk)
            except ValidationError as exc:form.add_error(None,exc)
        return self.get(request,pk,add_form=form)


class DailyScheduleEdit(View):
    def display(self,request,pk,bound=False):
        require_roles(request.user,READ_ROLES)
        if bound:require_roles(request.user,PLAN_ROLES | {'MANAGER'})
        entry=get_object_or_404(DailyScheduleEntry.objects.select_related('schedule__source__construction_object').defer('schedule__source__snapshot'),pk=pk,schedule__in=scope_queryset(DailySchedule.objects.all(),request.user))
        editable=entry.schedule.status=='DRAFT' and role_code(request.user) in (PLAN_ROLES if entry.kind=='work' else PLAN_ROLES|{'MANAGER'}) and not entry.metadata.get('resource_assignment_id')
        if editable and entry.kind!='work':
            from .preparation import require_resource_input
            try:require_resource_input(request.user,entry.schedule)
            except (PermissionDenied,ValidationError):editable=False
        if bound and not editable:raise PermissionDenied('Примененный СМГ зафиксирован. Создайте новую редакцию.')
        fields=[field for field in service.FIELDS[entry.kind] if entry.kind not in {'labor','equipment'} or field[0]=='planned_count']
        class DayForm(forms.Form):
            pass
        for key,label,digits in fields:DayForm.base_fields[key]=forms.DecimalField(label=label,min_value=0,max_digits=18,decimal_places=digits,widget=forms.NumberInput(attrs={'class':'form-control form-control-sm','step':str(Decimal(1).scaleb(-digits))}))
        identities=service.identities(entry)
        stored={(r['date'],r.get('item_id')):r for r in entry.rows};original={(r['date'],r.get('item_id')):r for r in entry.original}
        initial=[{key:service.row_value(entry.kind,stored.get((day.isoformat(),item['id']),{}),key) for key,_,_ in fields} for day,item in identities]
        FormSet=formset_factory(DayForm,extra=0,max_num=len(identities),validate_max=True,absolute_max=max(len(identities),1))
        formset=FormSet(request.POST if bound else None,initial=initial)
        if not editable:
            for form in formset:
                for field in form.fields.values():field.disabled=True
        if entry.schedule.locked_before:
            for (day,item),form in zip(identities,formset):
                if day<entry.schedule.locked_before:
                    for field in form.fields.values():field.disabled=True
        error=None
        if bound and formset.is_valid():
            try:
                # Hidden hours are preserved when a user edits the count.
                values=[{**{key:service.row_value(entry.kind,stored.get((day.isoformat(),item['id']),{}),key) for key,_,_ in service.FIELDS[entry.kind]},**form.cleaned_data} for (day,item),form in zip(identities,formset)]
                entry=service.save_entry(request.user,entry,values,int(request.POST.get('revision','-1')))
                if entry.schedule.source_id and service.differences(entry,entry.rows):messages.warning(request,'Месячный итог СМГ отличается от ГПР. Отклонение сохранено и показано в сравнении.')
                else:messages.success(request,'Дневной СМГ сохранен.')
                return redirect('planning:daily_schedule_edit',pk=entry.pk)
            except (ValidationError,ValueError) as exc:error='; '.join(exc.messages) if isinstance(exc,ValidationError) else 'Обновите страницу.'
        display=[]
        for (day,item),form in zip(identities,formset):
            cells=[]
            for key,label,_ in fields:
                base=Decimal(str(service.row_value(entry.kind,original.get((day.isoformat(),item['id']),{}),key)))
                try:value=Decimal(str(form[key].value() or 0).replace(',','.'));delta=value-base
                except Exception:delta=None
                cells.append(dict(field=form[key],base=base,delta=delta,key=key))
            display.append(dict(day=day,item=item,cells=cells))
        visible_summary=[row for row in summary(entry) if entry.kind not in {'labor','equipment'} or row['field']=='planned_count']
        return render(request,'planning/daily_schedule_edit.html',dict(entry=entry,schedule=entry.schedule,formset=formset,rows=display,error=error,editable=editable,comparison=visible_summary,fields=fields))
    def get(self,request,pk):return self.display(request,pk)
    def post(self,request,pk):return self.display(request,pk,True)


class DailyScheduleApply(View):
    def post(self,request,pk):
        require_roles(request.user,READ_ROLES)
        schedule=get_object_or_404(DailySchedule,pk=pk,company=request.user.company)
        try:
            action=request.POST.get('action','apply')
            if action=='apply':
                service.activate(request.user,schedule)
                messages.success(request,'Дневной СМГ применен к оперативным отчетам. Утвержденный ГПР сохранен.')
            else:
                from .daily_schedule_approval import transition
                transition(request.user,schedule,action,request.POST.get('comment',''))
                messages.success(request,'Решение по согласованию СМГ сохранено.')
        except ValidationError as exc:messages.error(request,'; '.join(exc.messages))
        return redirect('planning:daily_schedule_detail',pk=schedule.pk)
