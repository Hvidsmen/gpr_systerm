"""Captured crew ratios for editable table equipment and total position counts."""
import hashlib
import json
from collections import defaultdict
from copy import deepcopy
from decimal import Decimal
from django.core.exceptions import ValidationError, PermissionDenied
from django.db import transaction
from apps.resources.models import Brigade, EquipmentType
from apps.resources.position_catalog import brigade_for
from .models import ResourceMonthAllocation, DailyScheduleEntry


def shifts(value=1):
    try:
        count=int(value)
        if Decimal(str(value))!=count or not 1<=count<=24:raise ValueError
    except (ValueError,TypeError):raise ValidationError('Количество смен должно быть целым числом от 1 до 24.')
    return count


def daily_shifts(entry):
    first=next(iter(entry.rows or entry.original),{})
    return shifts(entry.metadata.get('shifts_per_day',first.get('shifts_per_day',1)))


def crew_positions(company, equipment):
    rows=equipment.crew_positions.filter(company=company,position__company=company).select_related('position').order_by('position_id')
    for row in rows:
        yield Brigade.objects.get(company=company,pk=brigade_for(row.position)),row.position.default_hourly_rate,row.count


def canonical_members(company,members):
    if not members:return []
    from apps.resources.brigade_merge import brigade_aliases
    aliases=brigade_aliases(company)
    return [{**m,'brigade_id':aliases.get(m['brigade_id'],m['brigade_id'])} for m in members]


def recipe(company,equipment,captured):
    if 'members' in captured:return canonical_members(company,deepcopy(captured['members']))
    return [{'brigade_id':b.pk,'count':count,'rate':str(rate)} for b,rate,count in crew_positions(company,equipment)]


def daily_recipe(entry):
    captured=entry.metadata.get('table_crew')
    if captured is not None:return deepcopy(captured)
    return next((deepcopy(r['table_crew']) for r in entry.rows+entry.original if 'table_crew' in r),None)


def daily_crew_counts(entry):
    if 'table_crew_counts' in entry.metadata:return entry.metadata['table_crew_counts']
    return {r['date']:int(r.get('table_crew_count',0)) for r in entry.rows}


def daily_crew_input(entry):
    if entry.kind!='equipment' or entry.metadata.get('resource_assignment_id'):return []
    captured=daily_recipe(entry)
    if captured is not None:return canonical_members(entry.schedule.company,captured)
    first=next(iter(entry.rows or entry.original),{})
    equipment=EquipmentType.objects.filter(company_id=entry.schedule.company_id,pk=first.get('equipment_type_id')).first()
    return recipe(entry.schedule.company,equipment,{}) if equipment else []


@transaction.atomic
def sync_monthly(user,version,month,*,edited_counts=None,removed_brigades=(),removal_actions=None):
    if version.company_id!=user.company_id:raise PermissionDenied
    edited_counts=edited_counts or {}
    rows=list(version.resource_allocations.select_for_update().filter(company=user.company,month=month).select_related('equipment_type','brigade'))
    if removed_brigades:
        from .crew_removal import monthly
        monthly(user,rows,removed_brigades,removal_actions)
        rows=list(version.resource_allocations.filter(company=user.company,month=month).select_related('equipment_type','brigade'))
    labor={r.brigade_id:r for r in rows if r.kind=='labor'}
    wanted=defaultdict(int);members={};created=0
    for source in rows:
        if source.kind!='equipment':continue
        captured=recipe(user.company,source.equipment_type,source.crew_data)
        if source.crew_data.get('members')!=captured:
            source.crew_data={**source.crew_data,'members':captured};source.save(update_fields=['crew_data','updated_at'])
        for member in captured:
            members[member['brigade_id']]=member
            wanted[member['brigade_id']]+=source.count*shifts(source.crew_data.get('shifts_per_day',1))*member['count']
    valid=set(Brigade.objects.filter(company=user.company,pk__in=members).values_list('pk',flat=True))
    if valid!=set(members):raise ValidationError('Должность экипажа недоступна в компании. Проверьте состав техники.')
    for brigade_id,member in members.items():
        if brigade_id not in labor:
            row=ResourceMonthAllocation.objects.create(company=user.company,version=version,month=month,kind='labor',brigade_id=brigade_id,rate=member['rate'])
            labor[brigade_id]=row;created+=1
    for brigade_id,row in labor.items():
        old=int(row.crew_data.get('count',0));new=wanted[brigade_id]
        if row.pk in edited_counts:
            total=int(edited_counts[row.pk])
            if total<new:raise ValidationError(f'«{row.brigade}»: численность не может быть меньше экипажа техники ({new}).')
        else:total=max(0,row.count-old)+new
        data={'count':new} if new or old or brigade_id in members else row.crew_data
        if (row.count,row.crew_data)!=(total,data):
            row.count=total;row.crew_data=data;row.save(update_fields=['count','crew_data','updated_at'])
    return created


def add_monthly_crew(user,version,month,equipment):
    return sync_monthly(user,version,month)


def new_daily_labor(schedule,brigade,rate):
    from .daily_schedule import bounds
    from datetime import timedelta
    start,end=bounds(schedule.month)
    rows=[{'date':(start+timedelta(days=i)).isoformat(),'brigade_id':brigade.pk,'label':str(brigade),'planned_count':'0','planned_workers':'0','planned_hours':'0','hourly_rate':str(rate),'origin':'PLAN','comment':''} for i in range(end.day)]
    key='labor:'+hashlib.sha256(json.dumps((brigade.pk,)).encode()).hexdigest()
    return DailyScheduleEntry.objects.create(schedule=schedule,key=key,kind='labor',label=str(brigade),rows=rows,original=deepcopy(rows))


@transaction.atomic
def sync_daily(user,schedule,*,edited_counts=None):
    if schedule.company_id!=user.company_id:raise PermissionDenied
    edited_counts=edited_counts or {};entries=list(schedule.entries.select_for_update().exclude(kind='work'))
    labor={};wanted=defaultdict(lambda:defaultdict(int));members={};created=0
    cutoff=schedule.locked_before.isoformat() if schedule.locked_before else ''
    for entry in entries:
        first=next(iter(entry.rows or entry.original),{})
        if entry.metadata.get('resource_assignment_id'):continue
        if entry.kind=='labor':labor.setdefault(first.get('brigade_id'),entry)
        if entry.kind!='equipment':continue
        captured=daily_crew_input(entry)
        if entry.metadata.get('table_crew')!=captured:
            entry.metadata={**entry.metadata,'table_crew':captured};entry.save(update_fields=['metadata'])
        for member in captured:
            members[member['brigade_id']]=member
            for row in entry.rows:
                wanted[member['brigade_id']][row['date']]+=int(Decimal(str(row.get('planned_count',0))))*daily_shifts(entry)*member['count']
    brigades={b.pk:b for b in Brigade.objects.filter(company=user.company,pk__in=members)}
    if set(brigades)!=set(members):raise ValidationError('Должность экипажа недоступна в компании. Проверьте состав техники.')
    for brigade_id,member in members.items():
        if brigade_id not in labor:
            labor[brigade_id]=new_daily_labor(schedule,brigades[brigade_id],member['rate']);created+=1
    for brigade_id,entry in labor.items():
        old=daily_crew_counts(entry);cache=dict(old);rows=deepcopy(entry.rows)
        for row in rows:
            day=row['date']
            if cutoff and day<cutoff:continue
            new=wanted[brigade_id][day]
            explicit=edited_counts.get(entry.pk,{})
            if day in explicit:
                total=int(Decimal(str(explicit[day]).replace(',','.')))
                if total<new:raise ValidationError(f'«{entry.label}», {day}: численность не может быть меньше экипажа техники ({new}).')
            else:total=max(0,int(Decimal(str(row.get('planned_workers',row.get('planned_count',0)))))-int(old.get(day,0)))+new
            row['planned_count']=row['planned_workers']=str(total)
            if new or day in old or brigade_id in members:row['table_crew_count']=new;cache[day]=new
        metadata={**entry.metadata,'table_crew_counts':cache} if cache else entry.metadata
        if rows!=entry.rows or metadata!=entry.metadata:
            entry.rows=rows;entry.metadata=metadata;entry.revision+=1;entry.save(update_fields=['rows','metadata','revision'])
    return created


def add_daily_crew(user,schedule,equipment):
    return sync_daily(user,schedule)


def display_recipe(company,equipment,captured,*,mappings=None):
    """Read legacy defaults without creating catalogue bridges during a GET."""
    if 'members' in captured:return [{'brigade_id':m['brigade_id'],'count':m['count']} for m in canonical_members(company,captured['members'])]
    from apps.resources.models import PositionPlanningBrigade
    if mappings is None:
        mappings=dict(PositionPlanningBrigade.objects.filter(company=company,brigade__company=company).values_list('position_id','brigade_id'))
    return [{'brigade_id':mappings[row.position_id],'count':row.count} for row in equipment.crew_positions.all() if row.company_id==company.pk and row.position_id in mappings]
