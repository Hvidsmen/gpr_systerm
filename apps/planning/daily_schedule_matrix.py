"""Grouped read-only daily SMG table with immutable GPR comparisons."""
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
    works={w.pk:w for w in ProjectWork.objects.filter(company_id=schedule.company_id,pk__in=[e.metadata.get('id') for e in entries if e.kind=='work']).select_related('section')}
    brigades={b.pk:b for b in Brigade.objects.filter(company_id=schedule.company_id).select_related('group','macro_group')}
    equipment={e.pk:e for e in EquipmentType.objects.filter(company_id=schedule.company_id).select_related('category')}
    grouped={kind:defaultdict(list) for kind in service.FIELDS};totals={kind:{} for kind in service.FIELDS}
    for entry in entries:
        if not entry.comparison:continue
        first=next(iter(entry.rows or entry.original),{})
        entry.reference=first.get('equipment_number','')
        group='Без группы'
        if entry.kind=='work':
            work=works.get(entry.metadata.get('id'))
            group=entry.metadata.get('complex_object_name') or entry.metadata.get('section_name') or (work.section.name if work else 'Работы')
        elif entry.kind=='labor':
            brigade=brigades.get(first.get('brigade_id'))
            if brigade:group=' · '.join(str(v) for v in [brigade.macro_group,brigade.group] if v) or 'Должность'
        elif entry.kind=='equipment':
            machine=equipment.get(first.get('equipment_type_id'))
            group=str(machine.category) if machine and machine.category_id else 'Техника'
        else:group='ГСМ'
        if entry.metadata.get('target_label'):group=entry.metadata['target_label']+' · '+group
        entry.has_deviation=bool(schedule.source_id) and any(row['delta'] for row in entry.comparison)
        grouped[entry.kind][group].append(entry)
        composites={}
        if entry.kind=='work' and entry.metadata.get('kind')=='COMPOSITE':
            composites['before']=entry.metadata.get('original_daily',[])
            composites['after']=service.finalize_work(entry.metadata,entry.rows,SimpleNamespace(start_date=start,end_date=end),entry.metadata.get('history',[]))['daily']
        for comparison in entry.comparison:
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
            entry.has_deviation=entry.has_deviation or (bool(schedule.source_id) and any(cell['delta'] for cell in comparison['cells']))
            if entry.kind!='work':
                total=totals[entry.kind].setdefault(comparison['field'],{'label':comparison['label'],'before':Decimal(0),'after':Decimal(0),'delta':Decimal(0),'cells':[{'before':Decimal(0),'after':Decimal(0),'delta':Decimal(0)} for day in days]})
                for key in ['before','after','delta']:total[key]+=comparison[key]
                for cell,source in zip(total['cells'],comparison['cells']):
                    for key in ['before','after','delta']:cell[key]+=source[key]
    labels={'work':'Работы','labor':'Должность','equipment':'Техника','fuel':'ГСМ'}
    sections=[{'kind':kind,'label':labels[kind],'groups':[{'label':name,'entries':sorted(rows,key=lambda e:(e.label.casefold(),e.pk))} for name,rows in sorted(groups.items())],'totals':list(totals[kind].values()),'count':sum(len(rows) for rows in groups.values())} for kind,groups in grouped.items()]
    return {'sections':sections,'days':days}
