"""Presentation groups and totals shared by day/month HTML and Excel reports."""
from decimal import Decimal
from apps.resources.group_order import NONE, labor_group_order


def metric_totals(rows):
    result = {'unit':rows[0]['unit'] if rows else '', 'row_count':len(rows)}
    for field in ['plan','actual']:
        values=[row[field] for row in rows if row.get(field) is not None]
        result[field]=sum(values,Decimal(0)) if values else None
        result[field+'_count']=len(values)
    complete=all(result[f+'_count']==len(rows) for f in ['plan','actual'])
    result['deviation']=result['actual']-result['plan'] if complete and rows else None
    result['percent']=result['actual']/result['plan']*100 if complete and rows and result['plan']>0 else None
    return result



def daily_rows(row, dates):
    row['daily_rows']=[]
    for day in dates:
        value=row.get('daily',{}).get(day.isoformat())
        metrics=metric_totals([value]) if value else dict(plan=None,actual=None,deviation=None,percent=None,unit=row['unit'])
        row['daily_rows'].append(dict(metrics,date=day))


def peak_daily_totals(row):
    """Peak daily resource counts; group peaks use daily sums, not summed row peaks."""
    for field in ['plan', 'actual']:
        days=[day for day in row['daily'].values() if day.get(field) is not None]
        row[field]=max((day[field] for day in days),default=None)
        if 'row_count' in row:
            row[field+'_count']=max((day.get(field+'_count',1) for day in days
                if day[field]==row[field]),default=0)
    complete=all(row.get(field+'_count',1)==row.get('row_count',1)
        for field in ['plan','actual'])
    plan,actual=row['plan'],row['actual']
    known=complete and plan is not None and actual is not None
    row['deviation']=actual-plan if known else None
    row['percent']=actual/plan*100 if known and plan>0 else None
    if 'row_count' in row:
        for day in row['daily_rows']:
            # Keep the group's original coverage and missing comparisons.
            day.update(row['daily'].get(day['date'].isoformat(),{}))
    return row


def totals(rows, dates=()):
    result=metric_totals(rows)
    grouped={}
    for row in rows:
        for day,values in row.get('daily',{}).items():grouped.setdefault(day,[]).append(values)
    result['daily']={day:metric_totals(values) for day,values in grouped.items()}
    daily_rows(result,dates)
    return result

def resource_groups(rows, dates=()):
    grouped={}
    for row in rows:
        key=(row.get('group_id') or 0,row['unit'])
        group=grouped.setdefault(key,{'label':row.get('group') or 'Без группы',
            'order':row.get('group_order',NONE),'rows':[]})
        group['rows'].append(row)
    for group in grouped.values():
        group['rows'].sort(key=lambda row:row['label'].casefold())
        group['total']=totals(group['rows'],dates)
        subgrouped={}
        for row in group['rows']:
            if row.get('subgroup'):
                subgrouped.setdefault(row['subgroup'],[]).append(row)
        if subgrouped:
            group['subgroups']=[]
            for label,rows_in_group in subgrouped.items():
                group['subgroups'].append({'label':label,'rows':rows_in_group,
                    'total':totals(rows_in_group,dates)})
            group['subgroups'].sort(key=lambda item:(labor_group_order(item['label']),item['label'].casefold()))
    return sorted(grouped.values(),key=lambda group:(group['order'],group['label'].casefold()))


def work_groups(rows, dates=()):
    sections={}
    for row in rows:
        section=sections.setdefault(row.get('section_id') or 0,
            {'label':row.get('section') or 'Без раздела','works':{}})
        # Work items are local to each complex. The same caption and unit form
        # one report line across complexes; standalone works remain separate.
        key=(' '.join(row['label'].split()).casefold(),row['unit'],row.get('kind','SIMPLE'))
        work=section['works'].setdefault(key,{'label':row['label'],'unit':row['unit'],'objects':{}})
        complex_key=row.get('complex_id') or 0
        obj=work['objects'].setdefault(complex_key,{'label':row.get('complex') or 'Без комплексного объекта','rows':[]})
        obj['rows'].append(row)
    for section in sections.values():
        for work in section['works'].values():
            leaves=[]
            for obj in work['objects'].values():
                obj['total']=totals(obj['rows'],dates)
                obj['comments']=sorted((comment for row in obj['rows'] for comment in row.get('comments',[])),key=lambda comment:(comment['date'],comment['text']))
                leaves.extend(obj['rows'])
            work['total']=totals(leaves,dates)
            work['objects']=sorted(work['objects'].values(),key=lambda obj:obj['label'].casefold())
        section['works']=sorted(section['works'].values(),key=lambda work:(work['label'].casefold(),work['unit']))
    return sorted(sections.values(),key=lambda section:section['label'].casefold())


def work_display_rows(groups):
    result=[]
    for section in groups:
        result.append({'label':section['label'],'level':0,'heading':True})
        for work in section['works']:
            sources=[row for obj in work['objects'] for row in obj['rows']]
            result.append(dict(work['total'],label=work['label'],context=section['label'],level=1,heading=True,source_rows=sources))
            for obj in work['objects']:
                result.append(dict(obj['total'],label=obj['label'],context=' → '.join([section['label'],work['label']]),level=2,source_rows=obj['rows']))
    return result


def resource_display_rows(groups):
    result=[]
    for group in groups:
        result.append(dict(group['total'],label='Итого · '+group['label'],context=group['label'],level=0,heading=True))
        if group.get('subgroups'):
            for subgroup in group['subgroups']:
                result.append(dict(subgroup['total'],label=subgroup['label'],context=group['label'],level=1,heading=True))
                result.extend(dict(row,context=' → '.join([group['label'],subgroup['label']]),level=2) for row in subgroup['rows'])
        else:
            result.extend(dict(row,context=group['label'],level=1) for row in group['rows'])
    return result


def day_matrix(report):
    """Shared dates and section-tagged rows; no totals across incompatible units."""
    from datetime import timedelta
    dates=[report['start']+timedelta(days=n) for n in range((report['end']-report['start']).days+1)]
    blocks=[]
    direction='works'

    def heading(label, section_id=None):
        blocks.append({'heading':True,'label':label,'section_id':section_id,'direction':direction})

    def block(label, total, *, level=0, toggle=None, parent=None, context=''):
        metrics=[]
        for field,caption in [('plan','План'),('actual','Факт'),('deviation','Факт − план'),('percent','Выполнение, %')]:
            cells=[{'value':day.get(field),'partial':field=='actual' and day.get('actual') is not None and day.get('actual_count',0)<day.get('row_count',0)} for day in total['daily_rows']]
            partial=(direction in {'labor','equipment'} and field=='actual' and total.get(field) is not None
                and total.get('actual_count',1)<total.get('row_count',1))
            metrics.append({'label':caption,'field':field,'total':total.get(field),'cells':cells,'total_partial':partial})
        blocks.append({'label':label,'unit':total['unit'],'metrics':metrics,'level':level,'toggle':toggle,'parent':parent,'context':context,'direction':direction})

    if not report.get('equipment_only'):
        heading('Работы','smg-period-works')
        for section_index,section in enumerate(report['work_groups']):
            heading(section['label'])
            for work_index,work in enumerate(section['works']):
                key=f'work-{section_index}-{work_index}'
                block(work['label'],work['total'],toggle=key)
                for obj in work['objects']:
                    block(obj['label'],obj['total'],level=1,parent=key)
        direction='labor'
        heading('Люди','smg-period-labor')
        for index,group in enumerate(report['labor_groups']):
            key=f'labor-{index}'
            block(group['label'],group['total'],toggle=key)
            if group.get('subgroups'):
                for subgroup_index,subgroup in enumerate(group['subgroups']):
                    subgroup_key=f'{key}-subgroup-{subgroup_index}'
                    block(subgroup['label'],subgroup['total'],level=1,toggle=subgroup_key,parent=key)
                    for row in subgroup['rows']:
                        block(row['label'],row,level=2,parent=subgroup_key)
            else:
                for row in group['rows']:block(row['label'],row,level=1,parent=key)
    direction='equipment'
    heading('Техника','smg-period-equipment')
    for index,group in enumerate(report['equipment_groups']):
        key=f'equipment-{index}'
        block(group['label'],group['total'],toggle=key)
        for row in group['rows']:block(row['label'],row,level=1,parent=key)
    return {'dates':dates,'blocks':blocks,'columns':len(dates)+3,'numeric_width':len(dates)*88+108}
