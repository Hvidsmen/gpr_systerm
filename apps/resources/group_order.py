"""Company-owned presentation order, shared by catalogues, plans, fact and people."""
NONE=2147483647


def normalized(label):return ''.join(c for c in str(label or '').casefold() if c.isalnum())


def default_priority(label,kind='labor'):
    name=normalized(label)
    if kind=='equipment':
        return {'специализированнаятехника':1,'вспомогательнаятехника':2}.get(name,1000)
    return {'итрсостав':1,'итр':1,'основнойитр':2,'основнойперсонал':3,
        'водительскийсостав':4,'основнойрабочийсостав':5,'вспомогательныйитр':6,
        'водительспецтехники':2,'водителиспецтехники':2,'водителиспециальнойтехники':2,
        'водителивспомогательнойтехники':3,'водительвспомогательнойтехники':3,
        'вспомогательныйперсонал':6}.get(name,1000)


def key(group,kind='labor'):
    label=getattr(group,'name',group) or ''
    name=normalized(label)
    if name in {'','безгруппы','безгруппыплана','безкатегории'}:return NONE,str(label).casefold()
    return getattr(group,'sort_order',default_priority(label,kind)),str(label).casefold()


def labor_group_order(group):return key(group)
def equipment_group_order(group):return key(group,'equipment')


def order_key(company,kind='labor'):
    """Capture configuration once, then sort strings without queries per key."""
    from .models import BrigadeGroup,EquipmentCategory
    model=EquipmentCategory if kind=='equipment' else BrigadeGroup
    ranks={normalized(name):priority for name,priority in model.objects.filter(company=company).values_list('name','sort_order')}
    def sort(label):
        rank,name=key(label,kind)
        return (NONE if rank==NONE else ranks.get(normalized(label),rank)),name
    return sort


def position_group(position):
    mapping=getattr(position,'planning_mapping',None) if position else None
    brigade=getattr(mapping,'brigade',None)
    return brigade.group.name if brigade and brigade.group_id else 'Без группы'


def position_order(position,label=''):
    mapping=getattr(position,'planning_mapping',None) if position else None
    brigade=getattr(mapping,'brigade',None)
    group=brigade.group if brigade and brigade.group_id else None
    return labor_group_order(group),str(label or getattr(position,'name','')).casefold()


def employee_order(employee):
    return position_order(employee.position if employee.position_id else None),employee.display_name.casefold(),employee.pk


def ordered(queryset,*,kind='labor',group_path=None,within=('name','pk')):
    from django.db.models import F
    if group_path:
        return queryset.order_by(F(group_path+'__sort_order').asc(nulls_last=True),F(group_path+'__name').asc(nulls_last=True),*within)
    return queryset.order_by('sort_order',*within)


def machine_order(machine):
    root=machine.type.parent or machine.type
    return equipment_group_order(root.category),root.name.casefold(),machine.type.name.casefold(),machine.plate_number,machine.pk
