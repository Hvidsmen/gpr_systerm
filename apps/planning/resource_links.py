"""Connect existing table resources while preserving quantities, hours and costs."""
import hashlib,json
from copy import deepcopy
from calendar import monthrange
from decimal import Decimal
from django.core.exceptions import ValidationError
from django.forms.models import model_to_dict
from django.urls import reverse
from .models import ResourceWorkLink,ResourceMonthAllocation,DailySchedule
from . import resource_targets


def binding_values(owner):
    query=ResourceWorkLink.objects.filter(company_id=owner.company_id)
    query=query.filter(entry__schedule=owner) if isinstance(owner,DailySchedule) else query.filter(allocation__version=owner)
    return list(query.order_by('allocation_id','entry_id','work_id','work_item_id').values('allocation_id','entry_id','work_id','work_item_id'))


def pairs(source):return [(link.work_id,link.work_item_id) for link in source.graph_links.all()]


def targets(source):
    result=[]
    for link in source.graph_links.all():
        label=(link.work.complex_object.name+' → ' if link.work.complex_object_id else '')+link.work.name
        if link.work_item_id:label+=' → '+link.work_item.name
        result.append({'work_id':link.work_id,'work_item_id':link.work_item_id,'label':label,'work_name':link.work.name,'item_name':link.work_item.name if link.work_item_id else ''})
    return result


def revision(source):
    return hashlib.sha256(json.dumps({'row':model_to_dict(source),'links':pairs(source)},sort_keys=True,default=str).encode()).hexdigest()


def serialize(source,owner,user=None):
    monthly=isinstance(source,ResourceMonthAllocation);kind=source.kind;linked=targets(source) if kind!='fuel' else []
    if monthly:
        month=source.month;start,end=month,month.replace(day=monthrange(month.year,month.month)[1]);count=source.count
        label=str(source.brigade) if kind=='labor' else str(source.equipment_type) if kind=='equipment' else source.get_fuel_type_display()
        gear=source.equipment_type_id or '';brigade=source.brigade_id
        liters=source.liters;fuel=source.fuel_type
        edit=reverse('planning:workspace_edit' if owner.workspace_id else 'planning:global_detail',args=[owner.pk])+'?month='+month.isoformat()+'#resource-rows-'+kind
        if user:
            from core.permissions import role_code
            if role_code(user)=='MANAGER':edit=reverse('planning:preparation_gpr',args=[owner.pk])+'?legacy=1&month='+month.isoformat()
    else:
        month=owner.month;start,end=month,month.replace(day=monthrange(month.year,month.month)[1]);rows=source.rows
        row=rows[0] if rows else {};counts=[Decimal(str(r.get('planned_count',r.get('planned_workers',0)))) for r in rows]
        count=str(min(counts))+'–'+str(max(counts)) if counts and min(counts)!=max(counts) else str(counts[0]) if counts else '0'
        label=source.label;gear=row.get('equipment_type_id','');brigade=row.get('brigade_id');liters=sum(Decimal(str(r.get('planned_liters',0))) for r in rows);fuel=row.get('fuel_type','')
        edit=reverse('planning:daily_schedule_edit',args=[owner.pk])
    position=''
    if brigade:
        from apps.resources.models import PositionPlanningBrigade
        position=PositionPlanningBrigade.objects.filter(company_id=owner.company_id,brigade_id=brigade).values_list('position_id',flat=True).first() or ''
    return {'id':('allocation:' if monthly else 'entry:')+str(source.pk),'table_resource':True,'revision':revision(source),
        'resource_kind':kind,'label':label,'count':count,'equipment_type':gear,'position':position,'crew_people':[],'crew':'','include_crew':False,
        'targets':linked,'shared':len(linked)>1,'work':linked[0]['work_id'] if linked else '', 'work_item':linked[0]['work_item_id'] if linked else '',
        'start_date':start.isoformat(),'end_date':end.isoformat(),'projected':[], 'open_table_url':edit,
        'quantity_label':str(liters)+' л/месяц' if kind=='fuel' else str(count)+(' ед.' if kind=='equipment' else ' чел.'),'fuel_type':fuel,'daily_liters':'0'}


def resources(owner,month,user=None):
    if isinstance(owner,DailySchedule):
        rows=owner.entries.exclude(kind='work').exclude(key__startswith='assignment:').exclude(rows=[],original=[]).prefetch_related('graph_links__work__complex_object','graph_links__work_item')
    else:
        rows=owner.resource_allocations.filter(month=month).exclude(count=0,hours=0,liters=0,balance=0).select_related('brigade','equipment_type').prefetch_related('graph_links__work__complex_object','graph_links__work_item')
    return [serialize(row,owner,user) for row in rows]


def replace(source,owner,selected):
    selected=resource_targets.normalize(owner,selected)
    if source.kind=='fuel' and selected:raise ValidationError('ГСМ назначается только на объект.')
    source.graph_links.all().delete()
    field='allocation' if isinstance(source,ResourceMonthAllocation) else 'entry'
    ResourceWorkLink.objects.bulk_create([ResourceWorkLink(company_id=owner.company_id,**{field:source},work_id=w,work_item_id=i) for w,i in selected])
    source._prefetched_objects_cache={}


def annotate_rows(rows,selected):
    for row in rows:
        row.pop('work_targets',None);row.pop('work_id',None);row.pop('work_item_id',None)
        if selected:
            row['work_targets']=[{'work_id':w,'work_item_id':i} for w,i in selected]
            row['work_id'],row['work_item_id']=selected[0]
    return rows


def restore_schedule(schedule):
    for entry in schedule.entries.exclude(kind='work').exclude(kind='fuel').exclude(key__startswith='assignment:'):
        if entry.graph_links.exists():continue
        selected=set()
        for row in entry.rows:
            if schedule.locked_before and row['date']<schedule.locked_before.isoformat():continue
            for t in row.get('work_targets',[]):selected.add((t['work_id'],t.get('work_item_id')))
        if selected:replace(entry,schedule,selected)


def copy_links(source,target,owner):
    if source.kind=='fuel':return
    selected=set(pairs(source))|set(pairs(target))
    if selected:replace(target,owner,selected)
