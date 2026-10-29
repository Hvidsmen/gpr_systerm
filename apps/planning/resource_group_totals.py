"""Count resources once per row, with daily subtotals and a monthly average."""
from decimal import Decimal, InvalidOperation


def number(value):
    try:return Decimal(str(value or 0).replace(',','.'))
    except InvalidOperation:return Decimal(0)


def equipment_group(equipment,company):
    from apps.resources.equipment_catalogue import category_for
    category=category_for(equipment,company)
    if category is None:return ('Без группы','none')
    return (category.name,str(category.pk))


def equipment_groups(catalogue,company):
    """Old assignments can retain a merged ID while the card opens its target."""
    from apps.resources.equipment_merge import equipment_aliases
    aliases=equipment_aliases(company)
    result={}
    for pk,equipment in catalogue.items():
        current=pk;seen=set()
        while current in aliases and current not in seen:
            seen.add(current);current=aliases[current]
        result[pk]=equipment_group(catalogue.get(current,equipment),company)
    return result


def labor_groups(catalogue,company):
    from apps.resources.brigade_merge import brigade_aliases
    aliases=brigade_aliases(company);result={}
    for pk,brigade in catalogue.items():
        current=pk;seen=set()
        while current in aliases and current not in seen:
            seen.add(current);current=aliases[current]
        resolved=catalogue.get(current,brigade)
        result[pk]=resolved.group.name if resolved.group_id else 'Без группы'
    return result


def group_label(key):
    return key[0] if isinstance(key,tuple) else key


from apps.resources.group_order import labor_group_order,equipment_group_order


def total(entries,kind):
    unit='чел.' if kind=='labor' else 'ед.'
    comparisons=[entry.comparison[0] for entry in entries if entry.comparison]
    cells=[]
    if comparisons:
        for index,cell in enumerate(comparisons[0].get('cells',[])):
            before=sum((number(c['cells'][index]['before']) for c in comparisons),Decimal(0))
            after=sum((number(c['cells'][index]['after']) for c in comparisons),Decimal(0))
            cells.append({'date':cell.get('date'),'before':before,'after':after,'delta':after-before})
    def monthly(side):
        return sum((cell[side] for cell in cells),Decimal(0))/len(cells) if cells else sum((number(c.get(side)) for c in comparisons),Decimal(0))
    before,after=monthly('before'),monthly('after')
    return {'label':('Средняя численность' if kind=='labor' else 'Среднее количество')+' · '+unit,
            'before':before,'after':after,'delta':after-before,'cells':cells,'field':''}


def attach_resource_totals(sections):
    for section in sections:
        if section['kind'] not in {'labor','equipment'}:continue
        order=labor_group_order if section['kind']=='labor' else equipment_group_order
        section['groups'].sort(key=lambda group:order(group['label']))
        entries=[]
        for group in section['groups']:
            group.update(kind=section['kind'],unit='чел.' if section['kind']=='labor' else 'ед.',totals=[total(group['entries'],section['kind'])])
            entries.extend(group['entries'])
        section['totals']=[total(entries,section['kind'])] if entries else []
        section['unit']='чел.' if section['kind']=='labor' else 'ед.'
