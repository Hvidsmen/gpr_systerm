from decimal import Decimal
from django import forms
from django.forms import formset_factory
from django.core.exceptions import ValidationError, PermissionDenied
from django.contrib import messages
from django.shortcuts import get_object_or_404, render, redirect
from django.views import View
from core.permissions import require_roles, READ_ROLES, PLAN_ROLES, role_code
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
            item=items.get(key.split(':')[0]);label=(item['name']+' · '+item['unit']) if item else entry.metadata.get('unit','Объем')
        a,b=before.get(key,Decimal(0)),after.get(key,Decimal(0))
        if field=='planned_count':
            count=Decimal(service.bounds(entry.schedule.month)[1].day)
            a,b=a/count,b/count
            label='Средняя численность, чел.' if entry.kind=='labor' else 'Среднее количество, ед.'
        result.append(dict(label=label,before=a,after=b,delta=b-a,field=field,item_id=key.split(':')[0] if entry.kind=='work' else None))
    if entry.kind=='work' and entry.metadata.get('kind')=='COMPOSITE':
        from types import SimpleNamespace
        generated=service.finalize_work(entry.metadata,entry.rows,SimpleNamespace(start_date=entry.schedule.month,end_date=service.bounds(entry.schedule.month)[1]),entry.metadata.get('history',[]))
        a=sum((Decimal(str(r['quantity'])) for r in entry.metadata.get('original_daily',[])),Decimal(0))
        b=sum((Decimal(str(r['quantity'])) for r in generated['daily']),Decimal(0))
        result.insert(0,dict(label='Работа, '+entry.metadata.get('unit',''),before=a,after=b,delta=b-a,field='work_total',item_id=None))
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
    def get(self,request,pk):
        require_roles(request.user,READ_ROLES)
        schedule=get_object_or_404(DailySchedule.objects.select_related('source__construction_object').defer('source__snapshot'),pk=pk,company=request.user.company)
        entries=list(schedule.entries.all())
        for entry in entries:
            entry.schedule=schedule
            entry.comparison=summary(entry)
        from .daily_schedule_matrix import matrix
        return render(request,'planning/daily_schedule_detail.html',dict(schedule=schedule,entries=entries,can_edit=role_code(request.user) in PLAN_ROLES,has_deviations=any(service.differences(e,e.rows) for e in entries),**matrix(schedule,entries)))


class DailyScheduleEdit(View):
    def display(self,request,pk,bound=False):
        require_roles(request.user,READ_ROLES)
        if bound:require_roles(request.user,PLAN_ROLES)
        entry=get_object_or_404(DailyScheduleEntry.objects.select_related('schedule__source__construction_object').defer('schedule__source__snapshot'),pk=pk,schedule__company=request.user.company)
        editable=entry.schedule.status=='DRAFT' and role_code(request.user) in PLAN_ROLES
        if bound and not editable:raise PermissionDenied('Примененный СМГ зафиксирован. Создайте новую редакцию.')
        fields=service.FIELDS[entry.kind]
        class DayForm(forms.Form):
            pass
        for key,label,digits in fields:DayForm.base_fields[key]=forms.DecimalField(label=label,min_value=0,max_digits=18,decimal_places=digits,widget=forms.NumberInput(attrs={'class':'form-control form-control-sm','step':str(Decimal(1).scaleb(-digits))}))
        identities=service.identities(entry)
        stored={(r['date'],r.get('item_id')):r for r in entry.rows};original={(r['date'],r.get('item_id')):r for r in entry.original}
        initial=[{key:stored.get((day.isoformat(),item['id']),{}).get(key,0) for key,_,_ in fields} for day,item in identities]
        FormSet=formset_factory(DayForm,extra=0,max_num=len(identities),validate_max=True,absolute_max=max(len(identities),1))
        formset=FormSet(request.POST if bound else None,initial=initial)
        if not editable:
            for form in formset:
                for field in form.fields.values():field.disabled=True
        error=None
        if bound and formset.is_valid():
            try:
                entry=service.save_entry(request.user,entry,[f.cleaned_data for f in formset],int(request.POST.get('revision','-1')))
                if service.differences(entry,entry.rows):messages.warning(request,'Месячный итог СМГ отличается от ГПР. Отклонение сохранено и показано в сравнении.')
                else:messages.success(request,'Дневной СМГ сохранен. Месячные итоги совпадают с ГПР.')
                return redirect('planning:daily_schedule_edit',pk=entry.pk)
            except (ValidationError,ValueError) as exc:error='; '.join(exc.messages) if isinstance(exc,ValidationError) else 'Обновите страницу.'
        display=[]
        for (day,item),form in zip(identities,formset):
            cells=[]
            for key,label,_ in fields:
                base=Decimal(str(original.get((day.isoformat(),item['id']),{}).get(key,0) or 0))
                try:value=Decimal(str(form[key].value() or 0).replace(',','.'));delta=value-base
                except Exception:delta=None
                cells.append(dict(field=form[key],base=base,delta=delta,key=key))
            display.append(dict(day=day,item=item,cells=cells))
        return render(request,'planning/daily_schedule_edit.html',dict(entry=entry,schedule=entry.schedule,formset=formset,rows=display,error=error,editable=editable,comparison=summary(entry),fields=fields))
    def get(self,request,pk):return self.display(request,pk)
    def post(self,request,pk):return self.display(request,pk,True)


class DailyScheduleApply(View):
    def post(self,request,pk):
        require_roles(request.user,PLAN_ROLES)
        schedule=get_object_or_404(DailySchedule,pk=pk,company=request.user.company)
        try:
            service.activate(request.user,schedule)
            messages.success(request,'Дневной СМГ применен к оперативным отчетам. Утвержденный ГПР сохранен.')
        except ValidationError as exc:messages.error(request,'; '.join(exc.messages))
        return redirect('planning:daily_schedule_detail',pk=schedule.pk)
