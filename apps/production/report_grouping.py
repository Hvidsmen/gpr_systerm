"""Presentation groups and totals shared by day/month HTML and Excel reports."""
from decimal import Decimal
from apps.resources.group_order import NONE


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
                obj['total']=totals(obj['rows'],dates);leaves.extend(obj['rows'])
                obj['packages']='; '.join(sorted({row['package'] for row in obj['rows'] if row.get('package')}))
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
                result.append(dict(obj['total'],label=obj['label'],context=' → '.join([section['label'],work['label'],obj['packages']]),level=2,source_rows=obj['rows']))
    return result


def resource_display_rows(groups):
    result=[]
    for group in groups:
        result.append(dict(group['total'],label='Итого · '+group['label'],context=group['label'],level=0,heading=True))
        result.extend(dict(row,context=group['label'],level=1) for row in group['rows'])
    return result
