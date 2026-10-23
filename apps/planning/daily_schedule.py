"""A separately versioned daily schedule; approved GPR snapshots are immutable."""
from calendar import monthrange
from collections import defaultdict
from copy import deepcopy
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP
import hashlib
import json
from types import SimpleNamespace
from django.core.exceptions import ValidationError, PermissionDenied
from django.db import transaction, connection
from django.db.models import Max
from core.permissions import PLAN_ROLES, require_roles
from .models import DailySchedule, DailyScheduleEntry, GlobalPlanVersion, PlanningWorkspace
from .report_projection import period_snapshot
from .workspace_services import finalize_work, allocate, RESOURCE_CONFIG, resource_identity

ZERO=Decimal('0')
# Monthly deviations are allowed and compared to the immutable monthly GPR.
ALLOW_MONTH_CHANGES=True
FIELDS={'work':[('quantity','Объем',6)],'labor':[('planned_count','Должность, чел.',0),('planned_hours','Чел·ч',2)],'equipment':[('planned_count','Техника, ед.',0),('planned_machine_hours','Маш·ч',2)],'fuel':[('planned_liters','Расход, л',2),('planned_balance','Остаток, л',2)]}


def bounds(month):return month,month.replace(day=monthrange(month.year,month.month)[1])


def validate_source(source,month):
    if source.workspace_id and PlanningWorkspace.objects.filter(pk=source.workspace_id,is_archived=True).exists():
        raise ValidationError("Исходный план в архиве. Сначала восстановите его.")
    if source.workspace_id and source.workspace.continuation_start and month < source.workspace.continuation_start:
        raise ValidationError('Прошлый период сохранен из предыдущего ГПР. Создайте СМГ для нового периода.')
    start,end=bounds(month)
    if source.status not in {'APPROVED','COMPLETED'}:raise ValidationError('Дневной СМГ создается из утвержденного ГПР.')
    if month.day!=1 or start<source.start_date or end>source.end_date:raise ValidationError('Выберите полный месяц внутри периода ГПР.')
    if source.planning_month and month!=source.planning_month:raise ValidationError('Для дневного СМГ выберите уточняемый месяц этой версии ГПР.')


def row_value(kind,row,field):
    if kind=='labor' and field=='planned_count':return row.get('planned_workers',row.get('planned_count')) or 0
    return row.get(field) or 0


def totals(entry,rows):
    result=defaultdict(lambda:ZERO)
    for row in rows:
        for field,_,digits in FIELDS[entry.kind]:
            key=f"{row.get('item_id')}:{field}" if entry.kind=='work' else field
            if field=='planned_balance':
                if row['date']==bounds(entry.schedule.month)[1].isoformat():result[key]=Decimal(str(row_value(entry.kind,row,field)))
            else:result[key]+=Decimal(str(row_value(entry.kind,row,field)))
    return {k:v.quantize(Decimal('.000001'),rounding=ROUND_HALF_UP) for k,v in result.items()}


def differences(entry,rows):
    old,new=totals(entry,entry.original),totals(entry,rows)
    return {k:new.get(k,ZERO)-old.get(k,ZERO) for k in old.keys()|new.keys() if new.get(k,ZERO)!=old.get(k,ZERO)}


def history(source,month):
    """Aggregate prior composite quantities in SQL, without decoding the full plan."""
    if connection.vendor!='sqlite':
        result=defaultdict(lambda:defaultdict(lambda:ZERO))
        for spec in source.snapshot.get('works',[]):
            for row in spec.get('context_plans',[])+spec.get('plans',[]):
                if str(row['date'])[:10]<month.isoformat():result[spec['id']][row.get('item_id')]+=Decimal(str(row['quantity']))
        return result
    result=defaultdict(lambda:defaultdict(lambda:ZERO))
    table=connection.ops.quote_name(GlobalPlanVersion._meta.db_table)
    with connection.cursor() as cursor:
        for field in ['plans','context_plans']:
            cursor.execute(f"SELECT json_extract(w.value,'$.id'),json_extract(r.value,'$.item_id'),SUM(CAST(json_extract(r.value,'$.quantity') AS REAL)) FROM {table} p,json_each(p.snapshot,'$.works') w,json_each(w.value,'$.{field}') r WHERE p.id=%s AND json_extract(w.value,'$.kind')='COMPOSITE' AND json_extract(r.value,'$.date')<%s GROUP BY 1,2",[source.pk,month.isoformat()])
            for work_id,item_id,value in cursor.fetchall():result[work_id][item_id]+=Decimal(str(value)).quantize(Decimal('.000001'))
    return result


@transaction.atomic
def create(user,source,month,*,_skip_rebase=False):
    from core.permissions import require_planning_object
    require_planning_object(user,source.construction_object_id)
    require_roles(user,PLAN_ROLES)
    from apps.projects.models import ConstructionObject
    ConstructionObject.objects.select_for_update().get(pk=source.construction_object_id,company=user.company)
    source=GlobalPlanVersion.objects.select_for_update().defer('snapshot').get(pk=source.pk,company=user.company)
    validate_source(source,month)
    if not _skip_rebase:
        from .models import CarryoverReviewTask
        from .smg_rebase import sync as sync_rebase,start as start_rebase
        record=CarryoverReviewTask.objects.filter(company=user.company,correction_version=source,month=month).select_related('correction_version','version','assigned_to').first()
        if record:
            task=sync_rebase(record)
            if task and task.status=='OPEN':return start_rebase(user,task)
    draft=DailySchedule.objects.filter(construction_object_id=source.construction_object_id,month=month,status__in=['DRAFT','SUBMITTED','APPROVED']).first()
    if draft:
        if draft.source_id!=source.pk:raise ValidationError('На этот месяц уже есть незавершенный СМГ. Откройте его в разделе «Планирование СМГ» и обновите ссылку на ГПР.')
        return draft
    snapshot=period_snapshot(source,*bounds(month));prior=history(source,month)
    active=DailySchedule.objects.filter(source=source,month=month,status='APPLIED').first()
    previous={e.key:e.rows for e in active.entries.all()} if active else {}
    schedule=DailySchedule.objects.create(company=user.company,source=source,month=month,revision=(DailySchedule.objects.filter(construction_object=source.construction_object,month=month).aggregate(n=Max('revision'))['n'] or 0)+1,created_by=user)
    if active and active.locked_before:
        schedule.locked_before=active.locked_before;schedule.replaces=active.replaces;schedule.save(update_fields=['locked_before','replaces'])
    entries=[]
    for spec in snapshot.get('works',[]):
        key='work:'+str(spec['id']);original=deepcopy(spec.get('plans',[]))
        if not original and not previous.get(key):continue
        metadata={k:v for k,v in spec.items() if k not in {'plans','daily','context_plans'}}
        metadata['history']=[dict(date=(month-timedelta(days=1)).isoformat(),item_id=i,quantity=str(q)) for i,q in prior[spec['id']].items()]
        metadata['original_daily']=spec.get('daily',[])
        entries.append(DailyScheduleEntry(schedule=schedule,key=key,kind='work',label=spec['name'],metadata=metadata,original=original,rows=deepcopy(previous.get(key,original))))
    for kind in ['labor','equipment','fuel']:
        grouped=defaultdict(list)
        for row in snapshot.get('resources',{}).get(kind,[]):grouped[resource_identity(row,kind)].append(row)
        for identity,original in grouped.items():
            key=kind+':'+hashlib.sha256(json.dumps(identity).encode()).hexdigest()
            entries.append(DailyScheduleEntry(schedule=schedule,key=key,kind=kind,label=original[0].get('label',str(identity)),metadata={},original=deepcopy(original),rows=deepcopy(previous.get(key,original))))
    if active:
        included={e.key for e in entries}
        for entry in active.entries.all():
            if entry.key not in included and (entry.original or entry.rows):
                entries.append(DailyScheduleEntry(schedule=schedule,key=entry.key,kind=entry.kind,label=entry.label,metadata=deepcopy(entry.metadata),original=deepcopy(entry.original),rows=deepcopy(entry.rows)))
    DailyScheduleEntry.objects.bulk_create(entries)
    if active and active.resource_assignments.exists():
        from .work_resources import copy_assignments
        copy_assignments(active,schedule)
    elif source.resource_assignments.exists():
        from .work_resources import copy_assignments
        copy_assignments(source,schedule,copy_original=True)
    return schedule


def identities(entry):
    start,end=bounds(entry.schedule.month)
    items=entry.metadata.get('items',[]) if entry.kind=='work' and entry.metadata.get('kind')=='COMPOSITE' else [{'id':None,'name':entry.label,'unit':entry.metadata.get('unit','')}]
    return [(start+timedelta(days=i),item) for i in range((end-start).days+1) for item in items]


def value_rows(entry,values):
    original={(r['date'],r.get('item_id')):r for r in entry.original}
    template=entry.original[0] if entry.original else {}
    rows=[]
    for (day,item),fields in zip(identities(entry),values):
        row=deepcopy(original.get((day.isoformat(),item['id']),template))
        row.update(date=day.isoformat(),origin='PLAN')
        if entry.kind=='work':row.update(item_id=item['id'])
        allowed={key:digits for key,_,digits in FIELDS[entry.kind]}
        if set(fields)!=set(allowed):raise ValidationError('Заполните все показатели дня.')
        for key,value in fields.items():
            value=Decimal(str(value))
            if not value.is_finite() or value<0 or value>Decimal('999999999999') or value!=value.quantize(Decimal(1).scaleb(-allowed[key])):raise ValidationError('Недопустимый дневной объем.')
            row[key]=str(value)
        if entry.kind=='labor':row['planned_workers']=row['planned_count']
        rows.append(row)
    if entry.kind=='work':
        for item in {r['item_id'] for r in rows}:
            selected=[r for r in rows if r['item_id']==item];old=[r for r in entry.original if r.get('item_id')==item]
            oldq=sum((Decimal(str(r['quantity'])) for r in old),ZERO);newq=sum((Decimal(r['quantity']) for r in selected),ZERO)
            oldvalue=sum((Decimal(str(r.get('value') or 0)) for r in old),ZERO)
            if oldq:amount=oldvalue*newq/oldq
            else:
                spec=entry.metadata;i=next((i for i in spec.get('items',[]) if i['id']==item),None)
                amount=newq*Decimal(str(spec.get('unit_price',0)))*(Decimal(str(i['weight']))/100/Decimal(str(i['norm'])) if i else 1)
            for row,value in zip(selected,allocate(amount.quantize(Decimal('.01'),rounding=ROUND_HALF_UP),[Decimal(r['quantity']) for r in selected],Decimal('.01'))):row['value']=str(value)
    return rows


@transaction.atomic
def save_entry(user,entry,values,revision):
    require_roles(user,PLAN_ROLES)
    schedule=DailySchedule.objects.select_for_update().get(pk=entry.schedule_id,company=user.company)
    from core.permissions import require_planning_object
    require_planning_object(user,schedule.construction_object_id)
    if schedule.status!='DRAFT':raise PermissionDenied('Примененный СМГ зафиксирован. Создайте новую редакцию.')
    entry=DailyScheduleEntry.objects.select_for_update().select_related('schedule').get(pk=entry.pk,schedule=schedule)
    if entry.metadata.get('resource_assignment_id'):raise ValidationError('Измените количество техники, смены или экипаж в разделе «Ресурсы работ и объекта».')
    if entry.revision!=revision:raise ValidationError('Строка изменена в другой вкладке. Обновите страницу.')
    if len(values)!=len(identities(entry)):raise ValidationError('Заполните все дни.')
    rows=value_rows(entry,values)
    if schedule.locked_before:
        cutoff=schedule.locked_before.isoformat()
        old={(r['date'],r.get('item_id')):r for r in entry.rows if r['date']<cutoff}
        for row in rows:
            if row['date']>=cutoff:continue
            previous=old.get((row['date'],row.get('item_id')), {})
            if any(Decimal(str(row_value(entry.kind,row,field)))!=Decimal(str(row_value(entry.kind,previous,field))) for field,_,_ in FIELDS[entry.kind]):
                raise ValidationError('Прошедшие дни сохранены из действовавшего СМГ. Изменяйте только оставшиеся дни.')
        rows=[deepcopy(r) for r in entry.rows if r['date']<cutoff]+[r for r in rows if r['date']>=cutoff]
    if differences(entry,rows) and not ALLOW_MONTH_CHANGES:raise ValidationError('Месячные итоги отличаются от ГПР. Перераспределите объемы между днями, сохранив итог месяца.')
    entry.rows=rows;entry.revision+=1;entry.save(update_fields=['rows','revision'])
    return entry


@transaction.atomic
def activate(user,schedule):
    require_roles(user,PLAN_ROLES)
    from apps.projects.models import ConstructionObject
    ConstructionObject.objects.select_for_update().get(pk=schedule.construction_object_id,company=user.company)
    schedule=DailySchedule.objects.select_for_update().select_related('source').defer('source__snapshot').get(pk=schedule.pk,company=user.company)
    from core.permissions import require_planning_object
    require_planning_object(user,schedule.construction_object_id)
    from .daily_schedule_approval import current,requires_approval
    if schedule.status not in {'DRAFT','APPROVED'}:raise ValidationError('СМГ зафиксирован или находится на согласовании.')
    needs_approval=requires_approval(schedule)
    if needs_approval and schedule.status!='APPROVED':raise ValidationError('Для этого СМГ сначала получите согласование служб и генерального директора.')
    review=current(schedule)
    from .estimate_review import signature
    if needs_approval and (not review or not review.decisions.filter(section='CEO',action='APPROVE').exists() or review.fingerprint!=signature(schedule)):raise ValidationError('Нет действующего утверждения дневного СМГ.')
    # The GPR reference is optional and never controls the SMG lifecycle.
    from .estimate_review import require_approved
    require_approved(schedule)
    if not ALLOW_MONTH_CHANGES and any(differences(e,e.rows) for e in schedule.entries.select_related('schedule')):raise ValidationError('Месячные итоги СМГ должны совпадать с ГПР.')
    DailySchedule.objects.filter(construction_object_id=schedule.construction_object_id,month=schedule.month,status='APPLIED').update(status='ARCHIVED')
    schedule.status='APPLIED';schedule.save(update_fields=['status','updated_at'])
    from .daily_schedule_approval import sync
    sync(schedule)
    from .smg_rebase import sync as sync_rebase
    from .models import CarryoverReviewTask
    for record in CarryoverReviewTask.objects.filter(correction_version_id=schedule.source_id,month=schedule.month).exclude(correction_version__isnull=True).select_related('version','correction_version','assigned_to'):sync_rebase(record)
    return schedule


def overlay(source,snapshot,start,end):
    from .smg_rebase import effective_schedules
    schedules=effective_schedules(source,start,end)
    return overlay_schedules(snapshot,schedules,start,end)


def overlay_schedules(snapshot,schedules,start,end):
    if not schedules:return snapshot
    result=deepcopy(snapshot);specs={s['id']:s for s in result.get('works',[])}
    for schedule in schedules:
        a,b=bounds(schedule.month);a,b=max(a,start),min(b,end)
        # An operative SMG is a complete monthly plan, not a patch of its GPR.
        for spec in specs.values():
            for field in ['plans','daily']:
                spec[field]=[r for r in spec.get(field,[]) if not a.isoformat()<=r['date'][:10]<=b.isoformat()]
        for rows in result.get('resources',{}).values():
            rows[:]=[r for r in rows if not a.isoformat()<=r['date'][:10]<=b.isoformat()]
        for entry in schedule.entries.all():
            if entry.kind=='work':
                spec=specs.get(entry.metadata['id'])
                if not spec:
                    spec=deepcopy(entry.metadata);spec.update(plans=[],daily=[])
                    specs[spec['id']]=spec;result.setdefault('works',[]).append(spec)
                generated=finalize_work(entry.metadata,entry.rows,SimpleNamespace(start_date=schedule.month,end_date=bounds(schedule.month)[1]),entry.metadata.get('history',[]))
                for field in ['name','unit_price','revenue_prices','items']:
                    if field in entry.metadata:spec[field]=deepcopy(entry.metadata[field])
                for field in ['plans','daily']:
                    spec[field]=[r for r in spec.get(field,[]) if not a.isoformat()<=r['date'][:10]<=b.isoformat()]+[r for r in generated[field] if a.isoformat()<=r['date'][:10]<=b.isoformat()]
            else:
                if not entry.original and not entry.rows:continue
                rows=result.setdefault('resources',{}).setdefault(entry.kind,[])
                identity=resource_identity((entry.original or entry.rows)[0],entry.kind)
                rows.extend(r for r in entry.rows if a.isoformat()<=r['date'][:10]<=b.isoformat())
    result['daily_schedule_applied']=True
    result['daily_schedule_ids']=[s.pk for s in schedules]
    return result


def schedule_differences(schedule):
    """Compare monthly resource totals, independent of assignment decomposition."""
    before=defaultdict(Decimal);after=defaultdict(Decimal)
    for entry in schedule.entries.select_related('schedule'):
        for output,rows in [(before,entry.original),(after,entry.rows)]:
            if entry.kind=='work':identity=('work',entry.metadata['id'])
            else:
                sample=(entry.original or entry.rows)
                if not sample:continue
                identity=(entry.kind,*resource_identity(sample[0],entry.kind))
            for field,value in totals(entry,rows).items():output[(*identity,field)]+=value
    return {key:after[key]-before[key] for key in before.keys()|after.keys() if after[key]!=before[key]}
