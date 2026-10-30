"""Read-only dated work prices in the GPR/SMG editors, fetched in bulk."""
from collections import defaultdict
from calendar import monthrange
from datetime import date
from decimal import Decimal,ROUND_HALF_UP
from .work_selection import select_spec
from apps.works.prices import rate_on
from apps.works.progress import quantity_from_totals

ZERO=Decimal(0)
CENT=Decimal('.01')


def prices_for(company,ids):
    from apps.works.models import WorkPrice
    result=defaultdict(list)
    for row in WorkPrice.objects.filter(company=company,work_id__in=ids).order_by('effective_from','pk'):
        result[row.work_id].append({'effective_from':row.effective_from.isoformat(),'price':str(row.price)})
    return result


def total(finances):
    finances=list(finances)
    return {'amount':sum((f['amount'] for f in finances),ZERO),'unpriced':sum(f['unpriced'] for f in finances),'unavailable':sum(f.get('unavailable',0) for f in finances)}


def info(spec,daily,start,end):
    rates={rate_on(spec.get('revenue_prices',[]),day,spec['unit_price']) for day in [start,end]+[date.fromisoformat(p['effective_from']) for p in spec.get('revenue_prices',[]) if start.isoformat()<p['effective_from']<=end.isoformat()]}
    amount=ZERO;unpriced=False
    for day,quantity in daily:
        price=rate_on(spec.get('revenue_prices',[]),day,spec['unit_price'])
        amount+=quantity*price
        unpriced=unpriced or bool(quantity and not price)
    return {'price':min(rates),'max_price':max(rates),'variable':len(rates)>1,'unit':spec.get('unit',''),'amount':amount.quantize(CENT,rounding=ROUND_HALF_UP),'unpriced':unpriced}


def allocation_finance(version,row,prices):
    work=row.work;start=row.month;end=start.replace(day=monthrange(start.year,start.month)[1])
    spec={'id':work.pk,'kind':work.kind,'unit':work.unit,'unit_price':str(work.unit_price),'allow_fractional':work.allow_fractional,
        'items':[{'id':i.pk,'name':i.name,'norm':str(i.quantity_per_unit),'unit':i.unit,'weight':str(i.weight)} for i in work.items.all()],
        'revenue_prices':prices[work.pk]}
    spec=select_spec(spec,row.selected_item_ids)
    totals={i['id']:Decimal(str(row.item_quantities.get(str(i['id']),row.quantity*Decimal(i['norm'])))) for i in spec['items']}
    quantity=quantity_from_totals(spec,totals) if work.kind=='COMPOSITE' else row.quantity
    changes=[p for p in prices[work.pk] if start.isoformat()<p['effective_from']<=end.isoformat()]
    if changes:
        # Only months with an intra-month rate change need daily profiles.
        from .workspace_services import specification,work_rows,finalize_work
        from types import SimpleNamespace
        captured=specification(work,row.selected_item_ids)
        rows=work_rows(version.workspace,captured,start,row.quantity,row.load_profile,row.item_quantities,selected_item_ids=row.selected_item_ids)
        daily=[(date.fromisoformat(r['date']),Decimal(r['quantity'])) for r in finalize_work(captured,rows,SimpleNamespace(start_date=start,end_date=end))['daily']]
    else:daily=[(start,quantity)]
    return info(spec,daily,start,end)


def gpr_finances(version,allocations):
    from django.core.exceptions import ValidationError
    allocations=list(allocations);prices=prices_for(version.company,{r.work_id for r in allocations})
    result={}
    for row in allocations:
        try:result[row.pk]=allocation_finance(version,row,prices)
        except ValidationError as exc:
            result[row.pk]={'amount':ZERO,'unpriced':False,'unavailable':True,'error':'; '.join(exc.messages)}
    return result


def smg_finance(schedule,entry,prices):
    from copy import deepcopy
    spec=deepcopy(entry.metadata)
    if schedule.status in {'DRAFT','REJECTED'}:
        cutoff=schedule.locked_before
        current=prices.get(spec['id'],[])
        if cutoff:
            old=spec.get('revenue_prices',[])
            current=[p for p in old if p['effective_from']<cutoff.isoformat()]+[{'effective_from':cutoff.isoformat(),'price':str(rate_on(current,cutoff,spec['unit_price']))}]+[p for p in current if p['effective_from']>cutoff.isoformat()]
        spec['revenue_prices']=current
    ready=next((r for r in entry.comparison if r['field'] in {'work_total','quantity'}),None)
    start=schedule.month;end=start.replace(day=monthrange(start.year,start.month)[1])
    daily=[(start.replace(day=index+1),cell['after']) for index,cell in enumerate(ready['cells'])] if ready else []
    return info(spec,daily,start,end)
