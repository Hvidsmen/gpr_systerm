"""Day-entry hierarchy: catalogue group, planning type, physical daily facts."""
from collections import defaultdict
from decimal import Decimal,ROUND_CEILING
from apps.resources.models import EquipmentType
from apps.resources.group_order import equipment_group_order


def day_types(company,rows,resource_plans,*,query=''):
    from apps.resources.group_order import order_key
    equipment_group_order=order_key(company,'equipment')
    quantities=defaultdict(Decimal)
    for resources in resource_plans.values():
        for pk,value in resources['equipment'].items():
            if pk is not None:quantities[int(pk)]+=value
    ids=set(quantities)|{row['planning_type'].pk for row in rows}
    catalogue={t.pk:t for t in EquipmentType.objects.filter(company=company,pk__in=ids).select_related('parent__category','category')}
    types={}
    def bucket(t):
        root=t.parent if t.parent_id else t
        return types.setdefault(root.pk,{'id':root.pk,'name':root.name,'group':root.category.name if root.category_id else 'Без группы','quantity':Decimal(0),'has_plan':False,'rows':[],'choices':[],'available':0,'worked':0,'idle':0})
    for pk,value in quantities.items():
        if pk in catalogue:
            item=bucket(catalogue[pk]);item['quantity']+=value;item['has_plan']=True
    for row in rows:
        t=catalogue.get(row['planning_type'].pk)
        if t is None:continue
        item=bucket(t)
        actual=row.get('actual_planning_type',row['planning_type'])
        if row.get('at_camp',True):
            physical=bucket(actual);physical['available']+=1
        if row['status']:
            item['rows'].append(row);item['worked']+=int(row['status']=='WORKED');item['idle']+=int(row['status']=='IDLE')
        else:
            m=row['machine'];numbers=' · '.join(filter(None,[m.plate_number,m.inventory_number,m.vin]))
            item['choices'].append({'id':m.pk,'label':f'{item["name"]} · {m.name} · {numbers} · {row["condition_label"]}'})
    output=[]
    for item in types.values():
        if query and not item['available'] and not all(word.casefold() in item['name'].casefold() for word in query.split()):continue
        item['in_plan']=item['has_plan'] and item['quantity']>0
        item['plan']=int(item['quantity'].to_integral_value(rounding=ROUND_CEILING)) if item['has_plan'] else None
        item['remaining']=max(0,item['plan']-item['worked']) if item['has_plan'] else None
        output.append(item)
    return sorted(output,key=lambda item:(equipment_group_order(item['group']),item['name'].casefold(),item['id']))
