"""Two different measures of production value; neither represents cash receipts."""
from decimal import Decimal
ZERO=Decimal(0)


def weighted_value(spec, quantities, price):
    if spec['kind']=='SIMPLE':return quantities.get(None,ZERO)*price
    return sum((quantities.get(item['id'],ZERO)*price*Decimal(str(item['weight']))/100/Decimal(str(item['norm'])) for item in spec.get('items',[]) if Decimal(str(item['norm']))>0),ZERO)


def add_valuations(row,children,spec,chosen,plan_index,work_id,actual_by_item,actual_prices,work,all_days,buckets,real_plan,real_fact,plan_only,basis):
    from apps.works.prices import rate_on
    from .report_services import aggregate
    row['money_precision']='2g' if basis=='estimated' else '0g'
    estimated_plan={};estimated_fact={};item_money={i['id']:({}, {}, {}, {}) for i in spec.get('items',[])}
    for day in all_days:
        source=chosen[day];entry=plan_index[id(source)].get(work_id) if source else None
        current=entry['spec'] if entry else spec
        rate=rate_on(current.get('revenue_prices',[]),day,Decimal(str(current.get('unit_price') or 0)))
        fact_rate=rate_on(actual_prices[work_id],day,work.unit_price if work else Decimal(str(current.get('unit_price') or 0)))
        if current['kind']=='SIMPLE':
            estimated_plan[day]=real_plan.get(day);estimated_fact[day]=real_fact.get(day)
            continue
        quantities={i['id']:entry['items'][i['id']].get(day,ZERO) if entry else ZERO for i in current.get('items',[])}
        facts={i['id']:actual_by_item[i['id']].get(day,ZERO) for i in current.get('items',[])}
        estimated_plan[day]=weighted_value(current,quantities,rate) if real_plan.get(day) is not None else None
        estimated_fact[day]=weighted_value(current,facts,fact_rate) if day in real_fact else None
        for item in current.get('items',[]):
            if item['id'] not in item_money:item_money[item['id']]=({}, {}, {}, {})
            a,b,c,d=item_money[item['id']]
            ratio=Decimal(str(item['weight']))/100/Decimal(str(item['norm']))
            weight=Decimal(str(item['weight']))/100
            valid_weights=sum((Decimal(str(i['weight'])) for i in current.get('items',[])),ZERO)==100
            c[day]=real_plan[day]*weight if real_plan.get(day) is not None and (valid_weights or real_plan[day]==0) else None
            d[day]=real_fact[day]*weight if real_fact.get(day) is not None and (valid_weights or real_fact[day]==0) else None
            a[day]=quantities[item['id']]*rate*ratio if real_plan.get(day) is not None else None
            b[day]=facts[item['id']]*fact_rate*ratio if day in real_fact else None
    cells=[*zip(row['cells'],[b['dates'] for b in buckets]),(row['total'],all_days)]
    def total(values,dates):
        value=aggregate(values,dates,'sum')[0]
        return value.quantize(Decimal('.01')) if value is not None else None
    for cell,dates in cells:
        cell.update(real_amount=total(real_plan,dates),real_fact_amount=total(real_fact,dates),estimated_amount=total(estimated_plan,dates),estimated_fact_amount=total(estimated_fact,dates))
        if basis=='estimated':
            cell['amount']=cell['estimated_amount'];cell['fact_amount']=cell['estimated_fact_amount']
            cell['delta_amount']=cell['fact_amount']-cell['amount'] if cell['fact_amount'] is not None and cell['amount'] is not None else None
    by_id={i['id']:i for i in spec.get('items',[])}
    for child in children:
        item_id=int(child['key'].rsplit('-i',1)[-1]);item=by_id.get(item_id)
        if item:child['valuation_weight']=Decimal(str(item['weight']))/100
        child['has_amount']=bool(row.get('has_amount') and basis=='estimated')
        child['money_precision']=row['money_precision']
        child['show_estimated_amount']=bool(row.get('has_amount') and basis!='estimated')
        for child_cell,parent_cell,dates in [(c,p,b['dates']) for c,p,b in zip(child['cells'],row['cells'],buckets)]+[(child['total'],row['total'],all_days)]:
            a,b,c,d=item_money.get(item_id,({},{},{},{}))
            real_a=total(c,dates) if not any(real_plan.get(day) is not None and day in c and c[day] is None for day in dates) else None
            real_b=total(d,dates) if not any(real_fact.get(day) is not None and day in d and d[day] is None for day in dates) else None
            child_cell.update(real_amount=real_a,real_fact_amount=real_b,estimated_amount=total(a,dates),estimated_fact_amount=total(b,dates))
    # Rounding of constituent display amounts must not create or lose kopecks.
    if children:
        for index in range(len(row['cells'])+1):
            parent=row['cells'][index] if index<len(row['cells']) else row['total']
            cells=[child['cells'][index] if index<len(row['cells']) else child['total'] for child in children]
            for key in ['real_amount','real_fact_amount','estimated_amount','estimated_fact_amount']:
                known=[cell for cell in cells if cell.get(key) is not None]
                if known and parent.get(key) is not None:
                    difference=parent[key]-sum((cell[key] for cell in known),ZERO)
                    dates=buckets[index]['dates'] if index<len(row['cells']) else all_days
                    component={'estimated_amount':0,'estimated_fact_amount':1,'real_amount':2,'real_fact_amount':3}[key]
                    eligible=[]
                    for child,cell in zip(children,cells):
                        item_id=int(child['key'].rsplit('-i',1)[-1])
                        series=item_money.get(item_id,({},{},{},{}))[component]
                        if cell.get(key) is not None and sum((series.get(day) or ZERO for day in dates),ZERO)!=0:eligible.append(cell)
                    if eligible and abs(difference)<=Decimal('.01')*len(known):eligible[-1][key]+=difference
        if basis=='estimated':
            for child in children:
                for cell in [*child['cells'],child['total']]:
                    cell['amount']=cell['estimated_amount'];cell['fact_amount']=cell['estimated_fact_amount']
                    cell['delta_amount']=cell['fact_amount']-cell['amount'] if cell['fact_amount'] is not None and cell['amount'] is not None else None
