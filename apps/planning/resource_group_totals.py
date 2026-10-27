"""Count resources once per row, with daily subtotals and a monthly average."""
from decimal import Decimal, InvalidOperation


def number(value):
    try:return Decimal(str(value or 0).replace(',','.'))
    except InvalidOperation:return Decimal(0)


def equipment_group(equipment,company):
    from apps.resources.equipment_catalogue import category_for
    category=category_for(equipment,company)
    if category is None:return ('Без категории','none')
    return (category.name,str(category.pk))


def group_label(key):
    return key[0] if isinstance(key,tuple) else key


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
        entries=[]
        for group in section['groups']:
            group.update(kind=section['kind'],unit='чел.' if section['kind']=='labor' else 'ед.',totals=[total(group['entries'],section['kind'])])
            entries.extend(group['entries'])
        section['totals']=[total(entries,section['kind'])] if entries else []
        section['unit']='чел.' if section['kind']=='labor' else 'ед.'
