"""Keep child SMGs and parent GPRs distinct: overlapping plans are never summed."""
from calendar import monthrange
from collections import defaultdict
from datetime import date,timedelta
from decimal import Decimal
from apps.planning.report_services import select_sources,empty_plan_value
from apps.resources.models import Brigade
from apps.resources.brigade_merge import brigade_aliases
from apps.projects.models import ConstructionObject
from .housing import present_stays


def add_month(month,offset):
    number=month.year*12+month.month-1+offset
    return date(number//12,number%12+1,1)


def monthly_plan(user,camp,day,*,camps=None):
    months=[add_month(day.replace(day=1),offset) for offset in range(5)]
    end=add_month(months[-1],1)-timedelta(days=1)
    selected=camps if camps is not None else [camp]
    linked=list(ConstructionObject.objects.filter(company=user.company,housing_camps__in=selected).distinct().order_by('name','pk'))
    catalogue={obj.pk:obj for obj in ConstructionObject.objects.filter(company=user.company)}
    paths={};objects={}
    for obj in linked:
        path=[];current=obj
        while current and current.pk not in path:
            path.append(current.pk);objects[current.pk]=current
            parent=catalogue.get(current.parent_id)
            current=parent if parent and parent.project_id==obj.project_id else None
        paths[obj.pk]=path
    sources=select_sources(user,{'start':months[0],'end':end,'mode':'current'},list(objects.values())) if objects else {}
    aliases=brigade_aliases(user.company)
    def canonical(pk):
        seen=set()
        while pk in aliases and pk not in seen:seen.add(pk);pk=aliases[pk]
        return pk
    index={};labels={}
    for available in sources.values():
        for source in available:
            values=defaultdict(lambda:defaultdict(Decimal))
            for row in source.snapshot.get('resources',{}).get('labor',[]):
                pk=canonical(row.get('brigade_id'))
                if pk is None:continue
                try:when=date.fromisoformat(row['date']);quantity=Decimal(str(row.get('planned_workers',row.get('planned_count',0)) or 0))
                except (KeyError,ValueError):continue
                values[pk][when]+=quantity;labels[pk]=row.get('label','Должность')
            index[id(source)]=values
    actual=defaultdict(set)
    for stay in present_stays(user.company,day).filter(camp__in=selected).select_related('person__employee__position__planning_mapping'):
        employee=stay.person.employee if stay.person.employee_id else None
        mapping=getattr(employee.position,'planning_mapping',None) if employee and employee.position_id else None
        actual[canonical(mapping.brigade_id) if mapping else None].add(employee.pk if employee else 'person:'+str(stay.person_id))
    groups={}
    for month_index,month in enumerate(months):
        used=set()
        for obj in linked:
            for offset in range(monthrange(month.year,month.month)[1]):
                when=month+timedelta(days=offset);chosen=None;origin=None
                for object_id in paths[obj.pk]:
                    chosen=next((source for source in sources.get(object_id,[]) if source.start<=when<=source.end),None)
                    if chosen is not None:origin=objects[object_id];break
                if chosen is None or empty_plan_value(chosen,when) is None:continue
                marker=(origin.pk,id(chosen),when)
                if marker in used:
                    kind='СМГ' if chosen.snapshot.get('daily_schedule_applied') else 'ГПР'
                    if (origin.pk,kind) in groups:groups[origin.pk,kind]['used_for'].add(obj.name)
                    continue
                used.add(marker)
                kind='СМГ' if chosen.snapshot.get('daily_schedule_applied') else 'ГПР'
                group=groups.setdefault((origin.pk,kind),{'object':origin,'kind':kind,'months':[{'days':set(),'values':defaultdict(lambda:defaultdict(Decimal)),'versions':set()} for _ in months], 'used_for':set()})
                group['used_for'].add(obj.name)
                bucket=group['months'][month_index];bucket['days'].add(when)
                bucket['versions'].add(getattr(chosen.version,'revision',getattr(chosen.version,'version_number','')))
                for pk,values in index[id(chosen)].items():bucket['values'][pk][when]+=values.get(when,Decimal(0))
    keys={pk for group in groups.values() for bucket in group['months'] for pk in bucket['values']}
    labels.update(Brigade.objects.filter(company=user.company,pk__in=keys).values_list('pk','name'))
    output=[]
    for group in sorted(groups.values(),key=lambda g:(g['object'].name.casefold(),g['kind'])):
        keys={pk for bucket in group['months'] for pk in bucket['values']}
        rows=[]
        for pk in sorted(keys,key=lambda pk:labels.get(pk,'Должность').casefold()):
            values=[sum(bucket['values'][pk].values(),Decimal(0))/len(bucket['days']) if bucket['days'] else None for bucket in group['months']]
            rows.append({'id':pk,'name':labels.get(pk,'Должность'),'actual':len(actual[pk]),'values':values})
        output.append({'object':group['object'],'kind':group['kind'],'rows':rows,'used_for':sorted(group['used_for']),
            'columns':[{'month':month,'versions':', '.join(str(v) for v in sorted(bucket['versions'])),'available':bool(bucket['days'])} for month,bucket in zip(months,group['months'])]})
    return {'housing_plan_months':months,'housing_plan_groups':output}


def position_headers(company,rows,plan_context,*,query='',position=None,state=''):
    """Attach each source's monthly plan to its position; keep source totals separate."""
    from apps.resources.models import Position
    aliases=brigade_aliases(company)
    def canonical(pk):
        seen=set()
        while pk in aliases and pk not in seen:seen.add(pk);pk=aliases[pk]
        return pk
    def normalized(value):return ' '.join(str(value).split()).casefold()
    sources=plan_context['housing_plan_groups'];months=plan_context['housing_plan_months']
    by_name=defaultdict(set);plans={}
    for source in sources:
        totals=[]
        for index,column in enumerate(source['columns']):
            totals.append({'month':column['month'],'value':sum((row['values'][index] or Decimal(0) for row in source['rows']),Decimal(0)) if column['available'] else None,'versions':column['versions']})
        source['totals']=totals;source['current_total']=totals[0]['value']
        for row in source['rows']:
            by_name[normalized(row['name'])].add(row['id'])
            plans.setdefault(row['id'],{'name':row['name'],'sources':[]})['sources'].append({'object':source['object'],'kind':source['kind'],'used_for':source['used_for'],
                'months':[{'month':month,'value':value,'versions':column['versions']} for month,value,column in zip(months,row['values'],source['columns'])]})
    position_ids={row['position'].pk for row in rows if row.get('position')}
    if position:position_ids.add(position.pk)
    mappings={}
    for item in Position.objects.filter(company=company,pk__in=position_ids).select_related('planning_mapping'):
        mapping=getattr(item,'planning_mapping',None)
        if mapping:mappings[item.pk]=canonical(mapping.brigade_id)
    groups={};matched=set()
    for row in rows:
        label=row['position_label'];key=normalized(label)
        group=groups.setdefault(key,{'name':label,'rows':[],'plan_ids':set()});group['rows'].append(row)
        mapped=mappings.get(row['position'].pk) if row.get('position') else None
        if mapped in plans:group['plan_ids'].add(mapped)
        elif len(by_name[key])==1:group['plan_ids'].update(by_name[key])
    for group in groups.values():matched.update(group['plan_ids'])
    if not query and not state:
        selected=mappings.get(position.pk) if position else None
        for pk,plan in plans.items():
            if pk in matched or position and pk!=selected and normalized(plan['name'])!=normalized(position.name):continue
            key=normalized(plan['name']);group=groups.setdefault(key,{'name':plan['name'],'rows':[],'plan_ids':set()});group['plan_ids'].add(pk)
    output=[]
    for group in groups.values():
        members=group['rows'];group['people']=sum(bool(r['person']) for r in members);group['on_shift']=sum(r['state']=='MV' for r in members)
        group['rest']=sum(r['state']=='MO' for r in members);group['vacancies']=sum(not r['person'] for r in members);group['present']=sum(bool(r.get('verified')) for r in members)
        group['plans']=[{**source,'position':plans[pk]['name']} for pk in sorted(group['plan_ids']) for source in plans[pk]['sources']]
        output.append(group)
    return sorted(output,key=lambda group:normalized(group['name']))
