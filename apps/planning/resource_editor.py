"""Inline resource assignments in the GPR, scoped to saved planned works."""
from calendar import monthrange
from datetime import date
from decimal import Decimal
from django import forms
from django.core.exceptions import PermissionDenied, ValidationError
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.http import require_POST
from core.permissions import scope_queryset
from .models import GlobalPlanVersion, WorkResourceAssignment, DailySchedule
from .work_resource_views import AssignmentForm
from . import work_resources, estimate_review, preparation


def planned_targets(owner):
    if isinstance(owner,DailySchedule):
        from types import SimpleNamespace
        allocations=[SimpleNamespace(work_id=e.metadata['id'],selected_item_ids=[i['id'] for i in e.metadata.get('items',[])]) for e in owner.entries.filter(kind='work') if e.metadata.get('id') and (e.rows or e.original)]
    else:allocations=list(estimate_review.review_allocations(owner).exclude(quantity=0,item_quantities={}))
    ids={a.work_id for a in allocations}
    selected={work_id:set() for work_id in ids}; full=set()
    for allocation in allocations:
        if allocation.selected_item_ids:selected[allocation.work_id].update(allocation.selected_item_ids)
        else:full.add(allocation.work_id)
    from apps.works.models import ProjectWork
    works=list(ProjectWork.objects.filter(company=owner.company,pk__in=ids).prefetch_related('items').order_by('name','pk'))
    targets=[]
    for work in works:
        items=[item for item in work.items.all() if item.company_id==owner.company_id and (work.pk in full or item.pk in selected[work.pk])]
        targets.append({'id':work.pk,'name':work.name,'kind':work.kind,'items':[{'id':item.pk,'name':item.name} for item in items]})
    return targets


class InlineAssignmentForm(AssignmentForm):
    resource_kind=forms.ChoiceField(choices=[('equipment','Техника'),('labor','Должность'),('fuel','ГСМ')])

    def __init__(self,*args,owner,**kwargs):
        super().__init__(*args,owner=owner,**kwargs)
        targets=planned_targets(owner)
        self.fields['work'].queryset=self.fields['work'].queryset.filter(pk__in=[r['id'] for r in targets])
        self.fields['work_item'].queryset=self.fields['work_item'].queryset.filter(pk__in=[i['id'] for r in targets for i in r['items']])
        self.configure_targets()
        for name in ['work','work_item']:self.fields[name].widget=forms.HiddenInput()
        for name in ['count','shifts_per_day','hours_per_shift','daily_liters','fuel_price']:self.fields[name].required=False
        for field in self.fields.values():
            if isinstance(field.widget,forms.Select):field.widget.attrs['data-native-select']='true'
            if not isinstance(field.widget,(forms.HiddenInput,forms.CheckboxInput,forms.CheckboxSelectMultiple)):field.widget.attrs['class']='form-control'

    def clean(self):
        data=super().clean();kind=data.get('resource_kind')
        if kind=='fuel':
            data.update(equipment_type=None,position=None,count=1,shifts_per_day=1,hours_per_shift=8,include_crew=False)
            data['fuel_price']=data.get('fuel_price') or 0
            if not data.get('fuel_type'):self.add_error('fuel_type','Выберите вид ГСМ.')
        elif kind in ['equipment','labor']:
            resource='equipment_type' if kind=='equipment' else 'position'
            data['position' if kind=='equipment' else 'equipment_type']=None
            data.update(fuel_type='',daily_liters=0,fuel_price=0)
            if kind=='labor':data.update(include_crew=False,refresh_crew=False)
            if not data.get(resource):self.add_error(resource,'Выберите ресурс.')
            for name in ['count','shifts_per_day','hours_per_shift']:
                if data.get(name) is None:self.add_error(name,'Обязательное поле.')
        return data


def serialize(assignment):
    from .resource_targets import describe
    targets = describe(assignment)
    projected=[]
    if assignment.fuel_type:projected.append({'kind':'fuel','identity':assignment.fuel_type,'label':assignment.get_fuel_type_display(),'count':0,'liters':str(assignment.daily_liters)})
    if assignment.equipment_type_id:projected.append({'kind':'equipment','identity':assignment.equipment_type_id,'label':str(assignment.equipment_type),'count':assignment.count})
    for member in assignment.crew_snapshot:
        projected.append({'kind':'labor','identity':member['brigade_id'],'label':member['name']+(' *' if member['is_replacement'] else ''),'count':assignment.count*assignment.shifts_per_day*member['count']})
    return {'id':assignment.pk,'work':assignment.work_id or '', 'work_item':assignment.work_item_id or '',
        'targets':targets, 'shared':len(targets)>1,
        'legacy_package':bool(assignment.work_id and assignment.work.kind=='COMPOSITE' and not assignment.work_item_id),
        'resource_kind':'fuel' if assignment.fuel_type else 'equipment' if assignment.equipment_type_id else 'labor',
        'equipment_type':assignment.equipment_type_id or '', 'position':assignment.position_id or '',
        'fuel_type':assignment.fuel_type,'daily_liters':str(assignment.daily_liters),'fuel_price':str(assignment.fuel_price),
        'count':assignment.count,'shifts_per_day':assignment.shifts_per_day,'hours_per_shift':str(assignment.hours_per_shift),
        'revision':assignment.updated_at.isoformat(),
        'include_crew':assignment.include_crew,'start_date':assignment.start_date.isoformat(),'end_date':assignment.end_date.isoformat(),
        'label':str(assignment.equipment_type or assignment.position or assignment.get_fuel_type_display()),
        'target':'; '.join(t['label'] for t in targets) if len(targets)>1 else assignment.work_item.name if assignment.work_item_id else assignment.work.name if assignment.work_id else 'Вне работ',
        'projected':projected,'crew':', '.join(f"{m['name']} — {m['count']} чел." for m in assignment.crew_snapshot)}


def work_plan_volumes(owner,targets,snapshot=None):
    """Show the manager the same planned volumes used by the GPR calculation."""
    from .workspace_services import months_between
    start,end=estimate_review.review_period(owner)
    months=months_between(start,end)
    allocations=[] if isinstance(owner,DailySchedule) else list(estimate_review.review_allocations(owner))
    schedule_entries={e.metadata.get('id'):e for e in owner.entries.filter(kind='work')} if isinstance(owner,DailySchedule) else {}
    specs={str(spec['id']):spec for spec in (snapshot or {}).get('works',[])}
    from apps.works.models import ProjectWork
    works={work.pk:work for work in ProjectWork.objects.filter(company=owner.company,pk__in=[t['id'] for t in targets]).prefetch_related('items')}
    for target in targets:
        work=works[target['id']]
        spec=specs.get(str(work.pk))
        def volumes(item=None):
            result=[]
            for month in months:
                prefix=month.strftime('%Y-%m')
                if isinstance(owner,DailySchedule):
                    entry=schedule_entries.get(work.pk)
                    quantity=sum((Decimal(str(row.get('quantity',0))) for row in (entry.rows if entry else []) if str(row['date']).startswith(prefix) and (not item or str(row.get('item_id'))==str(item.pk))),Decimal(0))
                elif spec is not None:
                    rows=spec.get('plans',[]) if item else spec.get('daily',[])
                    quantity=sum((Decimal(str(row['quantity'])) for row in rows if str(row['date']).startswith(prefix) and start.isoformat()<=str(row['date'])<=end.isoformat() and (not item or str(row.get('item_id'))==str(item.pk))),Decimal(0))
                else:
                    quantity=Decimal(0)
                    for allocation in allocations:
                        if allocation.work_id!=work.pk or allocation.month!=month:continue
                        if item:
                            if allocation.selected_item_ids and item.pk not in allocation.selected_item_ids:continue
                            quantity+=Decimal(str(allocation.item_quantities.get(str(item.pk),0))) if allocation.item_quantities else allocation.quantity*item.quantity_per_unit
                        else:quantity+=allocation.quantity
                result.append({'month':month,'label':month.strftime('%m.%Y'),'quantity':str(quantity)})
            return result
        target.update(unit=work.unit,plan=volumes())
        target['plan_total']=str(sum((Decimal(row['quantity']) for row in target['plan']),Decimal(0)))
        items={item.pk:item for item in work.items.all()}
        for item in target['items']:
            item.update(unit=items[item['id']].unit,plan=volumes(items[item['id']]))
            item['plan_total']=str(sum((Decimal(row['quantity']) for row in item['plan']),Decimal(0)))
    return targets


def workflow_state(owner):
    record=preparation.record(owner)
    latest=owner.estimate_reviews.order_by('-pk').first()
    return {'handed':bool(record),
        'resources_ready':bool(record and record.resources_ready_at and record.resources_fingerprint==preparation.resource_signature(owner)),
        'estimator_approved':estimate_review.check_valid(owner,latest)}


def context(user,owner,month=None,snapshot=None):
    allowed=True
    try:work_resources.editable(user,owner)
    except (PermissionDenied,ValidationError):allowed=False
    confirm_allowed=True
    try:preparation.require_resource_editor(user,owner)
    except (PermissionDenied,ValidationError):confirm_allowed=False
    start,end=estimate_review.review_period(owner)
    if month:
        start=max(start,month);end=min(end,month.replace(day=monthrange(month.year,month.month)[1]))
    targets=work_plan_volumes(owner,planned_targets(owner),snapshot)
    record=preparation.record(owner)
    latest=owner.estimate_reviews.order_by('-pk').first()
    from core.permissions import role_code
    from .preparation_views import HandoffForm
    handoff_form=HandoffForm(company=owner.company,initial={'manager':record.manager_id if record else None,'estimator':latest.assigned_to_id if latest else None}) if isinstance(owner,GlobalPlanVersion) and owner.workspace_id and owner.status in {'DRAFT','REJECTED'} and role_code(user) in {'ADMIN','PLANNER'} else None
    from django.urls import reverse
    return {'resource_assignment_url':reverse('planning:work_resources_save_smg' if isinstance(owner,DailySchedule) else 'planning:preparation_add_assignment',args=[owner.pk]),'gpr_handoff_form':handoff_form,'resources_ready':bool(record and record.resources_ready_at and record.resources_fingerprint==preparation.resource_signature(owner)),
        'estimator_approved':estimate_review.check_valid(owner,latest),'can_prepare_resources':allowed,'can_confirm_resources':confirm_allowed,'resource_owner':owner,
        'inline_resource_form':InlineAssignmentForm(owner=owner,initial={'start_date':start,'end_date':end,'count':1,'shifts_per_day':1,'hours_per_shift':8,'include_crew':True}),
        'resource_editor_data':{'targets':targets,'assignments':[serialize(a) for a in work_resources.owner_rows(owner)],
            'start_date':start.isoformat(),'end_date':end.isoformat(),'can_edit':allowed},
        'resource_targets':targets,'resource_preparation':record,**preparation.task_context(user,owner)}


@login_required
@require_POST
def add_assignment(request,pk,kind='gpr'):
    model=DailySchedule if kind=='smg' else GlobalPlanVersion
    owner=get_object_or_404(scope_queryset(model.objects.all(),request.user),pk=pk)
    try:
        with transaction.atomic():
            owner=model.objects.select_for_update().get(pk=owner.pk,company=request.user.company)
            work_resources.editable(request.user,owner)
            assignment_id=request.POST.get('assignment_id')
            if assignment_id:
                try:assignment_id=int(assignment_id)
                except (TypeError,ValueError):raise ValidationError('Неверное назначение ресурса.')
            instance=get_object_or_404(owner.resource_assignments,pk=assignment_id) if assignment_id else WorkResourceAssignment()
            revision=request.POST.get('resource_revision')
            if instance.pk and revision and revision!=instance.updated_at.isoformat():return JsonResponse({'errors':{'__all__':[{'message':'Назначение уже изменено. Обновите страницу перед сохранением.'}]}},status=409)
            if request.POST.get('action')=='delete':
                if not instance.pk:raise ValidationError('Выберите назначение.')
                assignment_id=instance.pk;work_resources.remove(request.user,owner,instance)
                return JsonResponse({'deleted':assignment_id,'workflow':workflow_state(owner)})
            form=InlineAssignmentForm(request.POST,owner=owner,instance=instance)
            if not form.is_valid():return JsonResponse({'errors':form.errors.get_json_data()},status=400)
            saved=work_resources.save(request.user,owner,form.instance,form.cleaned_data['refresh_crew'],targets=form.resolved_targets)
            return JsonResponse({'assignment':serialize(saved),'workflow':workflow_state(owner)})
    except ValidationError as error:
        return JsonResponse({'errors':{'__all__':[{'message':message} for message in error.messages]}},status=400)


def attach_matrix_resources(context):
    """Annotate the existing plan rows instead of rendering another work tree."""
    import re
    parent_ids={t['id'] for t in context.get('resource_targets',[]) if t['kind']!='COMPOSITE'}
    owner=context.get('resource_owner')
    if owner:parent_ids.update(owner.resource_assignments.filter(work__kind='COMPOSITE',work_item__isnull=True).values_list('work_id',flat=True))
    targets={target['id']:{item['id'] for item in target['items']} for target in context.get('resource_targets',[])}
    def visit(nodes):
        for node in nodes:
            visit(node.get('children',[]))
            for row in node.get('rows',[]):
                match=re.search(r'(?:^|-)w(\d+)',row.get('key',''))
                if not match:continue
                work_id=int(match.group(1))
                if work_id not in targets:continue
                row['resource_work_id']=work_id
                row['resource_show_parent']=work_id in parent_ids
                for child in row.get('children',[]):
                    item=re.search(r'-i(\d+)',child.get('key',''))
                    if item and int(item.group(1)) in targets[work_id]:
                        child.update(resource_work_id=work_id,resource_item_id=int(item.group(1)))
    for obj in context.get('objects',[]):
        for section in obj.get('sections',[]):
            if section['kind']=='works':visit(section.get('groups',[]))
    context.update(show_matrix_resources=True,inline_approval_actions=True,resource_matrix_colspan=len(context.get('columns',[]))+2)
