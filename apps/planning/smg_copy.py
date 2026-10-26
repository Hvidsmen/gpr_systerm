"""Explicit copying of an approved GPR into an existing draft SMG."""
from copy import deepcopy
from decimal import Decimal
from django import forms
from django.core.exceptions import ValidationError,PermissionDenied
from django.db import transaction
from django.db.models import Q
from django.contrib import messages
from django.shortcuts import get_object_or_404,redirect
from django.views.decorators.http import require_POST
from django.contrib.auth.decorators import login_required
from core.permissions import PLAN_ROLES,require_roles,role_code,scope_queryset,require_planning_object
from .models import DailySchedule,DailyScheduleEntry,GlobalPlanVersion
from . import daily_schedule as daily, independent_smg as independent,preparation


class CopyForm(forms.Form):
    source=forms.ModelChoiceField(label='Утверждённый ГПР',queryset=GlobalPlanVersion.objects.none())
    scope=forms.ChoiceField(label='Скопировать')
    replace_work=forms.BooleanField(label='Заменить открытые дни заполненных работ',required=False)
    def __init__(self,*args,user,schedule,**kwargs):
        super().__init__(*args,**kwargs)
        from .subobject_smg import reference_choices,parent_reference
        self.fields['source'].queryset=reference_choices(user,schedule.construction_object).filter(start_date__lte=schedule.month,end_date__gte=daily.bounds(schedule.month)[1]).filter(Q(planning_month__isnull=True)|Q(planning_month=schedule.month))
        self.fields['source'].label_from_instance=lambda v:f'{v.title or "ГПР"} · v{v.version_number} · {v.start_date:%m.%Y} — {v.end_date:%m.%Y}'
        choices=[]
        if role_code(user) in PLAN_ROLES:choices.extend([('all','Работы и все ресурсы'),('work','Работы')])
        try:preparation.require_resource_editor(user,schedule);resources=True
        except (PermissionDenied,ValidationError):resources=False
        if resources and not parent_reference(schedule):choices.append(('resources','Техника, должности и ГСМ'))
        if role_code(user) not in PLAN_ROLES:self.fields['replace_work'].widget=forms.HiddenInput()
        self.fields['scope'].choices=choices
        if role_code(user) not in PLAN_ROLES:self.fields['source'].disabled=True
        if len(choices)==1:self.fields['scope'].widget=forms.HiddenInput()
        self.initial.update(source=schedule.source_id,scope=choices[0][0] if choices else '')
        for field in self.fields.values():
            if isinstance(field.widget,forms.Select):field.widget.attrs['class']='form-select form-select-sm'


def blank(entry):
    return entry.revision==0 and not any(Decimal(str(daily.row_value(entry.kind,row,field))) for row in entry.rows for field,_,_ in daily.FIELDS[entry.kind])


@transaction.atomic
def copy_from_gpr(user,schedule,source,scope='work',replace_work=False):
    require_roles(user,PLAN_ROLES|{'MANAGER'})
    schedule=get_object_or_404(scope_queryset(DailySchedule.objects.select_for_update(),user),pk=schedule.pk)
    require_planning_object(user,schedule.construction_object_id)
    if schedule.status!='DRAFT':raise ValidationError('Копирование доступно только в черновике СМГ.')
    if scope not in ['work','resources','all']:raise ValidationError('Выберите состав копирования.')
    if scope in ['work','all']:require_roles(user,PLAN_ROLES)
    if scope=='resources' or scope=='all' and role_code(user) not in PLAN_ROLES:preparation.require_resource_editor(user,schedule)
    from .subobject_smg import valid_reference
    source=get_object_or_404(GlobalPlanVersion.objects.filter(company=user.company),pk=source.pk)
    if not valid_reference(schedule.construction_object,source):raise PermissionDenied('ГПР другого объекта.')
    daily.validate_source(source,schedule.month)
    entries_before=list(schedule.entries.all())
    untouched_before={entry.key for entry in entries_before if blank(entry)}
    resource_blank=not schedule.resource_assignments.exists() and all(blank(entry) for entry in entries_before if entry.kind!='work')
    if schedule.locked_before and schedule.locked_before>daily.bounds(schedule.month)[1]:raise ValidationError('Все дни этого СМГ закрыты. Копирование недоступно.')
    if schedule.source_id!=source.pk:
        if role_code(user) not in PLAN_ROLES:raise ValidationError('Планировщик должен сначала связать СМГ с выбранным ГПР.')
        schedule=independent.reference(user,schedule,source)
    if source.construction_object_id!=schedule.construction_object_id:
        if scope=='resources':raise ValidationError('Ресурсы общего ГПР не распределены между субобъектами. Добавьте ресурсы этого СМГ отдельно.')
        from .subobject_smg import add_parent_structure
        count=add_parent_structure(user,schedule)
        independent.audit(user,schedule,'UPDATE',{'copy_structure_from_parent':source.pk,'works':count})
        return {'works':count,'resources':0,'kept_works':schedule.entries.filter(kind='work').count()-count,'kept_resources':schedule.entries.exclude(kind='work').count(),'structure_only':True}
    specs=independent.source_specs(source,schedule.month)
    result={'works':0,'resources':0,'kept_works':0,'kept_resources':0}
    cutoff=schedule.locked_before.isoformat() if schedule.locked_before else schedule.month.isoformat()
    if scope in ['work','all']:
        for key,(kind,label,metadata,original) in specs.items():
            if kind!='work':continue
            entry=schedule.entries.filter(key=key).first()
            if entry and not replace_work and entry.key not in untouched_before and not blank(entry):result['kept_works']+=1;continue
            if not entry:
                entry=DailyScheduleEntry.objects.create(schedule=schedule,key=key,kind=kind,label=label,metadata=deepcopy(metadata),original=deepcopy(original))
            else:
                entry.original=deepcopy(original)
                old_items={item['id']:item for item in entry.metadata.get('items',[])}
                entry.metadata=deepcopy(metadata)
                ids={item['id'] for item in entry.metadata.get('items',[])}
                entry.metadata.setdefault('items',[]).extend(item for pk,item in old_items.items() if pk not in ids)
                entry.save(update_fields=['original','metadata'])
            totals={}
            for row in original:
                index=(row['date'],row.get('item_id'));totals[index]=totals.get(index,Decimal(0))+Decimal(str(row['quantity']))
            stored={(row['date'],row.get('item_id')):row for row in entry.rows}
            values=[{'quantity':daily.row_value('work',stored.get((day.isoformat(),item['id']),{}),'quantity') if day.isoformat()<cutoff else totals.get((day.isoformat(),item['id']),Decimal(0))} for day,item in daily.identities(entry)]
            daily.save_entry(user,entry,values,entry.revision)
            result['works']+=1
    if scope in ['resources','all']:
        entries=list(schedule.entries.exclude(kind='work'))
        # Existing resource decisions belong to the assigned manager. Never merge
        # frozen assignments into populated rows, which would count people twice.
        if not resource_blank:
            result['kept_resources']=len(entries) or 1
        else:
            for key,(kind,label,metadata,original) in specs.items():
                if kind=='work':continue
                entry=schedule.entries.filter(key=key).first()
                if entry is None:entry=DailyScheduleEntry(schedule=schedule,key=key,kind=kind,label=label,metadata=deepcopy(metadata),original=deepcopy(original))
                past=[deepcopy(row) for row in entry.rows if row['date']<cutoff]
                entry.rows=past+[deepcopy(row) for row in original if row['date']>=cutoff]
                entry.original=deepcopy(original);entry.revision+=1
                entry.save();result['resources']+=1
            from .work_resources import copy_assignments
            copy_assignments(source,schedule,copy_original=True,start_on=schedule.locked_before)
            result['resources']=sum(any(row['date']>=cutoff and any(Decimal(str(daily.row_value(entry.kind,row,field))) for field,_,_ in daily.FIELDS[entry.kind]) for row in entry.rows) for entry in schedule.entries.exclude(kind='work'))
    independent.audit(user,schedule,'UPDATE',{'copy_from_gpr':source.pk,'scope':scope,'replace_work':replace_work,**result})
    return result


@login_required
@require_POST
def copy_view(request,pk):
    schedule=get_object_or_404(scope_queryset(DailySchedule.objects.all(),request.user),pk=pk)
    form=CopyForm(request.POST,user=request.user,schedule=schedule)
    if form.is_valid():
        try:
            result=copy_from_gpr(request.user,schedule,form.cleaned_data['source'],form.cleaned_data['scope'],form.cleaned_data['replace_work'])
            if result.get('structure_only'):messages.success(request,f"Из общего ГПР добавлен состав работ: {result['works']}. Новые строки имеют нулевые объёмы. Введённые значения и ресурсы этого СМГ сохранены.")
            else:messages.success(request,f"Скопировано из ГПР: работ — {result['works']}, строк ресурсов — {result['resources']}. Сохранено ранее заполненных: работ — {result['kept_works']}, строк ресурсов — {result['kept_resources']}.")
        except ValidationError as error:messages.error(request,'; '.join(error.messages))
    else:
        for errors in form.errors.values():
            for error in errors:messages.error(request,error)
    return redirect('planning:daily_schedule_detail',pk=pk)
