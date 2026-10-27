"""Grouped read-only daily SMG table with immutable GPR comparisons."""
import json
from collections import defaultdict
from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace
from apps.works.models import ProjectWork
from apps.resources.models import Brigade,EquipmentType
from . import daily_schedule as service


def matrix(schedule,entries):
    start,end=service.bounds(schedule.month)
    days=[start+timedelta(days=i) for i in range(end.day)]
    works={w.pk:w for w in ProjectWork.objects.filter(company_id=schedule.company_id,pk__in=[e.metadata.get('id') for e in entries if e.kind=='work']).select_related('section','complex_object')}
    brigades={b.pk:b for b in Brigade.objects.filter(company_id=schedule.company_id).select_related('group','macro_group')}
    equipment={e.pk:e for e in EquipmentType.objects.filter(company_id=schedule.company_id).select_related('category','parent__category')}
    grouped={kind:defaultdict(list) for kind in service.FIELDS};totals={kind:{} for kind in service.FIELDS}
    from .resource_catalogue_links import catalogue_links
    catalogue=catalogue_links(schedule.company)
    from .resource_group_totals import equipment_groups,labor_groups,group_label,attach_resource_totals
    equipment_categories=equipment_groups(equipment,schedule.company)
    position_groups=labor_groups(brigades,schedule.company)
    for entry in entries:
        # Hours remain in saved inputs and calculations, outside the editor.
        if entry.kind in {'labor','equipment'}:entry.comparison=[row for row in entry.comparison if row['field']=='planned_count']
        if not entry.comparison:continue
        first=next(iter(entry.rows or entry.original),{})
        entry.reference=first.get('equipment_number','')
        from .table_resource_crew import daily_recipe,display_recipe,daily_crew_counts,canonical_members
        entry.brigade_id=first.get('brigade_id','')
        vars(entry).update(catalogue(entry.kind,first.get('brigade_id') if entry.kind=='labor' else first.get('equipment_type_id')))
        captured=daily_recipe(entry) if entry.kind=='equipment' else []
        machine=equipment.get(first.get('equipment_type_id'))
        members=captured if captured is not None else display_recipe(schedule.company,machine,{}) if machine else []
        if entry.metadata.get('resource_assignment_id'):members=[]
        entry.crew_recipe_json=json.dumps([{'brigade_id':m['brigade_id'],'count':m['count']} for m in canonical_members(schedule.company,members)])
        entry.crew_counts_json=json.dumps(daily_crew_counts(entry)) if entry.kind=='labor' else '{}'
        entry.crew_values_json=json.dumps({r['date']:str(r.get('planned_count',0)) for r in entry.rows}) if entry.kind=='equipment' else '{}'
        entry.crew_note='Численность включает экипаж техники. Можно добавить людей сверх экипажа.' if entry.kind=='labor' and any(daily_crew_counts(entry).values()) else ''
        group='Без группы'
        if entry.kind=='work':
            work=works.get(entry.metadata.get('id'))
            from .plan_workbench import locate_work
            locate_work(entry,work)
            group=entry.metadata.get('complex_object_name') or 'Самостоятельные работы'
        elif entry.kind=='labor':
            group=position_groups.get(first.get('brigade_id'),'Без группы')
        elif entry.kind=='equipment':
            machine=equipment.get(first.get('equipment_type_id'))
            group=equipment_categories.get(first.get('equipment_type_id'),('Без категории','none'))
        else:group='ГСМ'
        if entry.kind not in {'work','labor','equipment'}:group='Назначения ресурсов' if entry.metadata.get('resource_assignment_id') else 'Общие ресурсы объекта'
        entry.has_deviation=bool(schedule.source_id) and any(row['delta'] for row in entry.comparison)
        grouped[entry.kind][group].append(entry)
        composites={}
        if entry.kind=='work' and entry.metadata.get('kind')=='COMPOSITE':
            composites['before']=entry.metadata.get('original_daily',[])
            composites['after']=service.finalize_work(entry.metadata,entry.rows,SimpleNamespace(start_date=start,end_date=end),entry.metadata.get('history',[]))['daily']
        for comparison in entry.comparison:
            comparison['norm']=next((item.get('norm','') for item in entry.metadata.get('items',[]) if str(item['id'])==comparison['item_id']),'') if entry.kind=='work' else ''
            comparison['item_id_value']=int(comparison['item_id']) if comparison['item_id'] not in (None,'None') else ''
            comparison['step']=str(Decimal(1).scaleb(-next((digits for name,_,digits in service.FIELDS[entry.kind] if name==comparison['field']),6)))
            values={side:defaultdict(Decimal) for side in ['before','after']}
            for side,rows in [('before',entry.original),('after',entry.rows)]:
                field=comparison['field']
                if field=='work_total':rows=composites[side];field='quantity'
                for row in rows:
                    if entry.kind=='work' and comparison['field']!='work_total' and str(row.get('item_id'))!=comparison['item_id']:continue
                    values[side][row['date']]+=Decimal(str(service.row_value(entry.kind,row,field)))
            comparison['cells']=[{'before':values['before'][day.isoformat()],'after':values['after'][day.isoformat()],'delta':values['after'][day.isoformat()]-values['before'][day.isoformat()]} for day in days]
            for cell,day in zip(comparison['cells'],days):
                cell['date']=day.isoformat();cell['locked']=bool(schedule.locked_before and day<schedule.locked_before)
            open_counts={cell['after'] for cell in comparison['cells'] if not cell['locked']}
            comparison['month_fill']=entry.kind in {'labor','equipment'} and comparison['field']=='planned_count' and bool(open_counts) and not entry.metadata.get('resource_assignment_id')
            comparison['month_fill_value']=next(iter(open_counts)) if len(open_counts)==1 else ''
            entry.has_deviation=entry.has_deviation or (bool(schedule.source_id) and any(cell['delta'] for cell in comparison['cells']))
            if entry.kind!='work':
                total=totals[entry.kind].setdefault(comparison['field'],{'label':comparison['label'],'before':Decimal(0),'after':Decimal(0),'delta':Decimal(0),'cells':[{'before':Decimal(0),'after':Decimal(0),'delta':Decimal(0)} for day in days]})
                for key in ['before','after','delta']:total[key]+=comparison[key]
                for cell,source in zip(total['cells'],comparison['cells']):
                    for key in ['before','after','delta']:cell[key]+=source[key]
    labels={'work':'Работы','labor':'Должность','equipment':'Техника','fuel':'ГСМ'}
    sections=[{'kind':kind,'label':labels[kind],'groups':[{'label':group_label(name),'entries':sorted(rows,key=lambda e:(e.label.casefold(),e.pk))} for name,rows in sorted(groups.items())],'totals':list(totals[kind].values()),'count':sum(len(rows) for rows in groups.values())} for kind,groups in grouped.items()]
    from .plan_workbench import attach_work_hierarchy
    attach_resource_totals(sections)
    attach_work_hierarchy(sections)
    return {'sections':sections,'days':days}
