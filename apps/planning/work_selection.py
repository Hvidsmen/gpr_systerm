"""Resolve explicitly selected catalogue branches without adding siblings."""
from copy import deepcopy
from decimal import Decimal
from django.core.exceptions import ValidationError
from apps.works.models import ProjectWorkItem
from apps.works.planning_access import planning_works, allowed_item_ids, planning_selection


def choices(works,include_construction_object=False,*,user=None):
    works=list(planning_works(works,user).select_related('complex_object','section__construction_object').prefetch_related('items'))
    objects={};packages=[];items=[]
    for work in works:
        try:planning_selection(work,[],user)
        except ValidationError:continue
        allowed=allowed_item_ids(work,user)
        parent=str(work.complex_object) if work.complex_object_id else 'Без комплексного объекта'
        if include_construction_object:parent=str(work.section.construction_object)+' · '+parent
        if work.complex_object_id:objects[work.complex_object_id]=parent
        label=f'{parent} → {work.name}'
        packages.append((f'package:{work.pk}',label))
        items.extend((f'item:{item.pk}',f'{label} → {item.name}') for item in work.items.all() if item.company_id==work.company_id and item.pk in allowed)
    return [('', 'Выберите объект, комплекс или работу'),('Комплексные объекты',[(f'object:{pk}',name) for pk,name in objects.items()]),('Комплексные / самостоятельные работы',packages),('Работы в составе комплекса',items)]


def resolve(works,selection,*,user=None):
    works=planning_works(works,user)
    try:kind,raw=selection.split(':');pk=int(raw)
    except (AttributeError,ValueError):raise ValidationError('Выберите позицию справочника.')
    if kind=='object':
        rows=list(works.filter(complex_object_id=pk))
        if not rows:raise ValidationError('В выбранном объекте нет доступных комплексов работ.')
        result=[]
        for work in rows:
            try:result.append((work,planning_selection(work,[],user)))
            except ValidationError:continue
        if not result:raise ValidationError('В выбранном объекте нет доступных работ.')
        return result
    if kind=='package':
        work=works.filter(pk=pk).first()
        if work:return [(work,planning_selection(work,[],user))]
    if kind=='item':
        item=ProjectWorkItem.objects.filter(pk=pk,project_work__in=works).first()
        if item and item.company_id==item.project_work.company_id:
            allowed=allowed_item_ids(item.project_work,user)
            if item.pk not in allowed:raise ValidationError('Работа недоступна для добавления в план.')
            children={}
            for child in item.project_work.items.all():
                if child.company_id!=item.company_id:raise ValidationError('Работа другой компании.')
                children.setdefault(child.parent_id,[]).append(child.pk)
            selected={pk};stack=[pk]
            while stack:
                for child in children.get(stack.pop(),[]):
                    if child in selected:raise ValidationError('В составе работ есть цикл.')
                    if child in allowed:selected.add(child);stack.append(child)
            return [(item.project_work,sorted(selected))]
    raise ValidationError('Позиция не относится к доступному строительному объекту.')


def select_spec(spec,selected):
    if not selected or spec['kind']!='COMPOSITE':return spec
    result=deepcopy(spec)
    result.setdefault('valuation_items',deepcopy(spec['items']))
    result['items']=[item for item in spec['items'] if item['id'] in selected]
    if not result['items']:raise ValidationError('Выбранные работы отсутствуют в составе комплекса.')
    return result


def union_selection(allocations):
    return sorted({pk for row in allocations for pk in row.selected_item_ids}) if all(row.selected_item_ids for row in allocations) else []


def extend_allocation(row,selected):
    """Add only missing branches; retain every previously entered child volume."""
    before=row.selected_item_ids
    if not before:return False
    after=sorted(set(before)|set(selected)) if selected else []
    if after==before:return False
    allowed=set(after) if after else set(row.work.items.values_list('pk',flat=True))
    previous={str(pk):str(row.quantity*item.quantity_per_unit) for item in row.work.items.all() if (pk:=item.pk) in before}
    if row.daily_override is not None:
        previous={str(pk):str(sum((Decimal(str(r['quantity'])) for r in row.daily_override if r.get('item_id')==pk),Decimal(0))) for pk in before}
    previous.update(row.item_quantities)
    row.selected_item_ids=after
    row.item_quantities={str(pk):previous.get(str(pk),'0') for pk in allowed}
    row._preserve_selected_override=True
    row.save()
    return True


def partial_remainder_rows(version,spec,current_rows,inputs,future):
    """Remainders of a partial package are quantities of its selected works."""
    from datetime import date
    from .workspace_services import work_fact_rows,allocate,work_rows
    target=version.planning_month;workspace=version.workspace
    facts=work_fact_rows(version,spec,target)
    context=[r for r in facts if date.fromisoformat(r['date'])<workspace.start_date]
    past=[r for r in facts if date.fromisoformat(r['date'])>=workspace.start_date]
    manual={row.month:row for row in inputs if row.work_id==spec['id'] and row.month>target}
    quantities={month:{} for month in future}
    for item in spec['items']:
        item_id=item['id']
        total=lambda rows:sum((Decimal(str(row['quantity'])) for row in rows if row['item_id']==item_id),Decimal(0))
        remaining=max(Decimal(0),total(spec.get('plans',[]))-total(past)-total(current_rows))
        if remaining and not future:raise ValidationError('Остаток выбранной работы не помещается в период: '+item['name'])
        weights=[total([r for r in spec.get('plans',[]) if r['date'][:7]==month.isoformat()[:7]]) for month in future]
        if len(future)==1:values=[remaining]
        elif remaining and not sum(weights):
            values=[Decimal(str(manual[month].item_quantities.get(str(item_id),manual[month].quantity*Decimal(item['norm'])))) if month in manual and (not manual[month].selected_item_ids or item_id in manual[month].selected_item_ids) else Decimal(0) for month in future]
            if sum(values)!=remaining:raise ValidationError('Распределите остаток выбранной работы по будущим месяцам: '+item['name'])
        else:values=allocate(remaining,weights,Decimal('.000001'))
        for month,value in zip(future,values):quantities[month][str(item_id)]=str(value)
    rows=list(current_rows)+past
    for month in future:
        selected=manual[month].selected_item_ids if month in manual else []
        if selected and any(Decimal(value)>0 and int(key) not in selected for key,value in quantities[month].items()):
            raise ValidationError('Добавьте оставшуюся работу в выбранную ветку будущего месяца: '+month.strftime('%m.%Y'))
        amounts={key:value for key,value in quantities[month].items() if not selected or int(key) in selected}
        rows+=work_rows(workspace,spec,month,0,manual[month].load_profile if month in manual else None,amounts,selected_item_ids=selected)
    return rows,context
