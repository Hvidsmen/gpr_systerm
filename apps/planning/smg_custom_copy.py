"""Rename drafts and copy independent SMG variants into a chosen month."""
from calendar import monthrange
from copy import deepcopy
from datetime import date
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils.dateparse import parse_datetime
from .models import DailySchedule,DailyScheduleEntry,WorkResourceAssignment
from . import independent_smg as lifecycle,daily_schedule as daily


@transaction.atomic
def rename(user,schedule,name,version):
    lifecycle.check(user,schedule)
    schedule=DailySchedule.objects.select_for_update().get(pk=schedule.pk,company=user.company)
    if schedule.status not in {'DRAFT','REJECTED'}:raise ValidationError('Название можно изменить в черновике или возвращённом СМГ.')
    try:expected=parse_datetime(version or '')
    except (ValueError,TypeError):expected=None
    if expected!=schedule.updated_at:raise ValidationError('СМГ изменился. Обновите страницу перед переименованием.')
    name=name.strip()
    if len(name)>150:raise ValidationError('Название не должно быть длиннее 150 символов.')
    before=schedule.name;schedule.name=name;schedule.save(update_fields=['name','updated_at'])
    lifecycle.audit(user,schedule,'UPDATE',{'name_before':before,'name':name})
    return schedule


def shifted(day,month):
    old=date.fromisoformat(day)
    return month.replace(day=old.day).isoformat() if old.day<=monthrange(month.year,month.month)[1] else None


def remap(rows,month):
    return [{**deepcopy(row),'date':day} for row in rows if (day:=shifted(row['date'],month))]


@transaction.atomic
def copy_variant(user,schedule,month,name):
    lifecycle.check(user,schedule)
    if month.day!=1:raise ValidationError('Выберите месяц копии.')
    if len(name.strip())>150:raise ValidationError('Название не должно быть длиннее 150 символов.')
    schedule=DailySchedule.objects.select_for_update().select_related('source','construction_object').defer('source__snapshot').get(pk=schedule.pk,company=user.company)
    if month==schedule.month:
        new=lifecycle.revise(user,schedule,independent=True)
        new.name=name.strip();new.save(update_fields=['name','updated_at'])
        return new
    new=lifecycle.create(user,schedule.construction_object,month,name=name)
    for old in schedule.entries.select_for_update().exclude(metadata__has_key='resource_assignment_id'):
        metadata=deepcopy(old.metadata)
        metadata['history']=[];metadata['original_daily']=[]
        if 'table_crew_counts' in metadata:metadata['table_crew_counts']={day:value for key,value in metadata['table_crew_counts'].items() if (day:=shifted(key,month))}
        entry=DailyScheduleEntry(schedule=new,key=old.key,kind=old.kind,label=old.label,gpr_work_id=old.gpr_work_id,metadata=metadata)
        # Temporary originals carry resource IDs, rates and row metadata. The
        # comparison baseline is rebuilt for the target month below.
        entry.original=remap(old.rows,month)
        lookup={(r['date'],r.get('item_id')):r for r in entry.original}
        values=[]
        for day,item in daily.identities(entry):
            source=lookup.get((day.isoformat(),item['id']),{})
            values.append({field:daily.row_value(old.kind,source,field) for field,_,_ in daily.FIELDS[old.kind]})
        entry.rows=daily.value_rows(entry,values);entry.original=[];entry.save()
    from .work_resources import owner_rows,sync_schedule
    from .resource_targets import identities,replace
    last=monthrange(month.year,month.month)[1]
    for old in owner_rows(schedule):
        first_day=old.start_date.day if (old.start_date.year,old.start_date.month)==(schedule.month.year,schedule.month.month) else 1
        last_day=old.end_date.day if (old.end_date.year,old.end_date.month)==(schedule.month.year,schedule.month.month) else monthrange(schedule.month.year,schedule.month.month)[1]
        if first_day>last:continue
        data={field:deepcopy(getattr(old,field)) for field in ['key','work_id','work_item_id','equipment_type_id','position_id','count','shifts_per_day','hours_per_shift','fuel_type','daily_liters','fuel_price','include_crew','crew_snapshot']}
        copied=WorkResourceAssignment.objects.create(company=user.company,schedule=new,start_date=month.replace(day=first_day),end_date=month.replace(day=min(last,last_day)),**data)
        replace(copied,identities(old))
    sync_schedule(new)
    from .resource_links import restore_schedule
    restore_schedule(new)
    from .table_resource_crew import sync_daily
    sync_daily(user,new)
    if schedule.source_id and schedule.source.status in {'APPROVED','COMPLETED'} and schedule.source.start_date<=month<=schedule.source.end_date:
        new=lifecycle.reference(user,new,schedule.source)
    lifecycle.audit(user,new,'CREATE',{'copied_from':schedule.pk,'source_month':str(schedule.month),'month':str(month),'name':new.name})
    return new
