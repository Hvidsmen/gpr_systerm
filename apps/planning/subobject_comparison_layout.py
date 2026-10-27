"""Presentation hierarchy for parent GPR versus contributions of child SMGs."""
from collections import defaultdict
from decimal import Decimal
from types import SimpleNamespace
from apps.works.models import ProjectWork
from .plan_workbench import locate_work
from .resource_group_totals import labor_group_order,equipment_group_order


def node(key,label,level,row=None):
    return {'key':key,'label':label,'level':level,'row':row,'children':[]}


def resource_total(rows):
    """Counts share the same month denominator and can be added across resources."""
    if not rows:return None
    result={field:sum((row[field] for row in rows),Decimal(0)) for field in ['gpr','total','own','delta']}
    result.update(unit=rows[0]['unit'],cells=[sum((row['cells'][index] for row in rows),Decimal(0)) for index in range(len(rows[0]['cells']))])
    return result


def hierarchy(rows,specs,company,width):
    work_rows=defaultdict(list)
    for row in rows:
        if row['kind']=='work':work_rows[row['work_id']].append(row)
    works={work.pk:work for work in ProjectWork.objects.filter(company=company,pk__in=work_rows).select_related('section','complex_object')}
    sections={}
    for work_id,members in work_rows.items():
        spec=specs[work_id];entry=SimpleNamespace(metadata=spec,label=spec['name'])
        locate_work(entry,works.get(work_id))
        section_key='comparison:'+entry.hierarchy_section_key
        section=sections.setdefault(section_key,node(section_key,entry.hierarchy_section_name,'section'))
        objects=section.setdefault('objects',{})
        object_key='comparison:'+entry.hierarchy_object_key
        obj=objects.setdefault(object_key,node(object_key,entry.hierarchy_object_name,'object' if entry.hierarchy_has_object else 'standalone'))
        if spec['kind']=='COMPOSITE':
            total=next((row for row in members if row.get('package_total')),None)
            branch=node('comparison:work:'+str(work_id),spec['name'],'package',total)
            branch['measure']='Готовые комплексы'
            branch['children']=[node(branch['key']+':item:'+str(row['item_id']),row['label'],'work',row) for row in members if not row.get('package_total')]
            obj['children'].append(branch)
        else:
            obj['children'].extend(node('comparison:work:'+str(work_id),row['label'],'work',row) for row in members)
    work_root=node('comparison:work','Работы','kind')
    for section in sorted(sections.values(),key=lambda n:(n['label'].casefold(),n['key'])):
        section['children']=sorted(section.pop('objects').values(),key=lambda n:(n['label'].casefold(),n['key']))
        for obj in section['children']:obj['children'].sort(key=lambda n:(n['label'].casefold(),n['key']))
        work_root['children'].append(section)
    roots=[work_root] if work_root['children'] else []
    for kind,label,order in [('labor','Должности',labor_group_order),('equipment','Техника',equipment_group_order),('fuel','ГСМ',None)]:
        visible=[r for r in rows if r['kind']==kind and (kind=='fuel' or r['measure_field']=='planned_count')]
        if not visible:continue
        root=node('comparison:'+kind,label,'kind',resource_total(visible) if kind!='fuel' else None)
        if kind!='fuel':root['measure']='Среднее за месяц'
        groups={}
        for row in visible:
            leaf=node(root['key']+':'+str(row['resource_id'])+':'+row['measure_field'],row['label'] if kind!='fuel' else row['name'],'resource',row)
            if kind=='fuel':root['children'].append(leaf);continue
            group_key=root['key']+':group:'+row['group_key']
            group=groups.setdefault(group_key,node(group_key,row['group_label'],'position_group' if kind=='labor' else 'equipment_category'))
            group['children'].append(leaf)
        for group in sorted(groups.values(),key=lambda n:(order(n['label']),n['key'])):
            group['children'].sort(key=lambda n:(n['label'].casefold(),n['key']))
            group['measure']='Среднее за месяц'
            group['row']=resource_total([leaf['row'] for leaf in group['children']]);root['children'].append(group)
        roots.append(root)
    output=[]
    def flatten(branch,depth=0,parents=()):
        values=branch['row'] or {}
        output.append({**values,**branch,'depth':depth,'ancestors':' '.join(parents),'has_values':bool(values),
            'cells':values.get('cells',[None]*width),'has_children':bool(branch['children'])})
        for child in branch['children']:flatten(child,depth+1,parents+(branch['key'],))
    for root in roots:flatten(root)
    return output
