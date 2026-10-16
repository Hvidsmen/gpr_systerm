from calendar import monthrange
from collections import defaultdict
from datetime import timedelta
from decimal import Decimal, ROUND_HALF_UP
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Sum, Q
from apps.production.models import DailyFact
from apps.works.models import ProjectWork
from apps.works.progress import quantity_from_totals
from core.permissions import require_roles, PLAN_ROLES
from .models import GlobalPlanVersion, WorkMonthAllocation, WorkCarryover
from .meeting_import import validate_target_period
from .report_projection import period_snapshot

ZERO = Decimal('0')


def previous_period(month):
    end = month - timedelta(days=1)
    return end.replace(day=1), end


def source_versions(version, month):
    start,end=previous_period(month)
    return GlobalPlanVersion.objects.filter(company=version.company,construction_object_id=version.construction_object_id,status__in=['APPROVED','COMPLETED'],start_date__lte=start,end_date__gte=end).filter(Q(planning_month__isnull=True)|Q(planning_month__lte=start)).exclude(pk=version.pk).defer('snapshot').order_by('-planning_month','-approved_at','-pk')


def preview(version, month, source):
    validate_target_period(version,month,month.replace(day=monthrange(month.year,month.month)[1]))
    if not source_versions(version,month).filter(pk=source.pk).exists():
        raise ValidationError('Выберите утвержденный план этого объекта за предыдущий месяц.')
    start,end=previous_period(month)
    snapshot=period_snapshot(source,start,end)
    specs=snapshot.get('works',[])
    work_ids=[s['id'] for s in specs]
    works={w.pk:w for w in ProjectWork.objects.filter(company=version.company,section__construction_object_id=version.construction_object_id,pk__in=work_ids).prefetch_related('items')}
    facts=defaultdict(dict)
    for row in DailyFact.objects.filter(company=version.company,project_work_id__in=work_ids,date__range=(start,end)).values('project_work_id','work_item_id').annotate(total=Sum('actual_quantity')):
        facts[row['project_work_id']][row['work_item_id']]=row['total']
    existing={r.work_id:r for r in version.work_allocations.filter(month=month)}
    transferred=set(WorkCarryover.objects.filter(version=version,month=month).values_list('work_id',flat=True))
    result=[]
    for spec in specs:
        work=works.get(spec['id'])
        if not work:continue
        plans=defaultdict(lambda:ZERO)
        for row in spec.get('plans',[]):
            if start.isoformat() <= str(row['date'])[:10] <= end.isoformat():
                plans[row.get('item_id')]+=Decimal(str(row['quantity']))
        precision = Decimal('0.001') if spec['kind']=='SIMPLE' else Decimal('0.000001')
        plans = defaultdict(lambda: ZERO, {key:value.quantize(precision,rounding=ROUND_HALF_UP) for key,value in plans.items()})
        facts[work.pk] = {key:value.quantize(Decimal('0.001'),rounding=ROUND_HALF_UP) for key,value in facts[work.pk].items()}
        if spec['kind']=='SIMPLE':
            planned=plans[None];actual=facts[work.pk].get(None,ZERO);quantity=max(planned-actual,ZERO);items={}
        else:
            if {i.pk for i in work.items.all()} != {i['id'] for i in spec['items']}:
                raise ValidationError(f'Изменен состав подработ «{work.name}». Перенос требует сопоставления.')
            items={str(i['id']):str(max(plans[i['id']]-facts[work.pk].get(i['id'],ZERO),ZERO)) for i in spec['items']}
            quantity=quantity_from_totals(spec,{int(k):Decimal(v) for k,v in items.items()})
            planned=quantity_from_totals(spec,plans);actual=quantity_from_totals(spec,facts[work.pk])
        if quantity<=0 and not any(Decimal(v)>0 for v in items.values()):continue
        children=[dict(name=i['name'],unit=i['unit'],planned=str(plans[i['id']]),actual=str(facts[work.pk].get(i['id'],ZERO)),quantity=items[str(i['id'])]) for i in spec.get('items',[]) if str(i['id']) in items]
        result.append(dict(children=children,work_id=work.pk,name=work.name,unit=work.unit,planned=str(planned),actual=str(actual),quantity=str(quantity),items=items,current=str(existing[work.pk].quantity if work.pk in existing else ZERO),exists=work.pk in existing,done=work.pk in transferred,spec=spec))
    return result


@transaction.atomic
def apply(user, version, month, source, mode, selected):
    require_roles(user,PLAN_ROLES)
    version=GlobalPlanVersion.objects.select_for_update().defer('snapshot').get(pk=version.pk,company=user.company)
    rows=preview(version,month,source)
    count=0
    for data in rows:
        if str(data['work_id']) not in selected or data['done']:continue
        delta=Decimal(data['quantity']).quantize(Decimal('0.001'),rounding=ROUND_HALF_UP)
        if data['exists']:
            WorkCarryover.objects.create(company=user.company,version=version,source_version=source,month=month,work_id=data['work_id'],quantity=delta,item_quantities=data['items'],imported_by=user,added=False)
            count+=1
            continue
        row,created=WorkMonthAllocation.objects.get_or_create(company=user.company,version=version,month=month,work_id=data['work_id'])
        if row.daily_override is not None:
            raise ValidationError(f'«{data["name"]}»: сохранен отдельный дневной план. Сначала согласуйте его изменение.')
        delta=Decimal(data['quantity']).quantize(Decimal('0.001'),rounding=ROUND_HALF_UP)
        if data['items']:
            spec=data['spec'];old=row.item_quantities or {str(i['id']):str(row.quantity*Decimal(i['norm'])) for i in spec['items']}
            row.item_quantities={k:str(Decimal(old.get(k,'0'))+Decimal(v)) for k,v in data['items'].items()}
            row.quantity=quantity_from_totals(spec,{int(k):Decimal(v) for k,v in row.item_quantities.items()})
        else:row.quantity+=delta
        try:
            row.full_clean();row.save()
        except ValidationError as exc:
            raise ValidationError(f'Не удалось сохранить остаток работы «{data["name"]}»: '+ '; '.join(exc.messages)) from exc
        WorkCarryover.objects.create(company=user.company,version=version,source_version=source,month=month,work_id=data['work_id'],quantity=delta,item_quantities=data['items'],imported_by=user)
        count+=1
    version.snapshot={};version.save(update_fields=['snapshot'])
    return count
