"""One presentation order for people and equipment throughout the portal."""


def normalized(label):return ''.join(c for c in str(label or '').casefold() if c.isalnum())


def labor_group_order(label):
    name=normalized(label)
    priorities={'итрсостав':0,'итр':0,'водительспецтехники':1,'водителиспецтехники':1,
        'водителиспециальнойтехники':1,'водителивспомогательнойтехники':2,'водительвспомогательнойтехники':2,
        'основнойрабочийсостав':3,'вспомогательныйперсонал':4}
    return (priorities.get(name,6 if name in {'','безгруппы','безгруппыплана'} else 5),str(label or '').casefold())


def equipment_group_order(label):
    name=normalized(label)
    return ({'специализированнаятехника':0,'вспомогательнаятехника':1}.get(name,3 if name in {'','безкатегории','безгруппы'} else 2),str(label or '').casefold())


def position_group(position):
    mapping=getattr(position,'planning_mapping',None) if position else None
    brigade=getattr(mapping,'brigade',None)
    return brigade.group.name if brigade and brigade.group_id else 'Без группы'


def position_order(position,label=''):
    return labor_group_order(position_group(position)),str(label or getattr(position,'name','')).casefold()


def employee_order(employee):
    return position_order(employee.position if employee.position_id else None),employee.display_name.casefold(),employee.pk


def ordered(queryset,*,kind='labor',group_path=None,within=('name','pk')):
    """Sort before pagination, resolving real company group names only once."""
    from django.db.models import Case,When,Value,IntegerField
    order=equipment_group_order if kind=='equipment' else labor_group_order
    id_field=group_path+'_id' if group_path else 'pk'
    name_field=group_path+'__name' if group_path else 'name'
    names=list(queryset.order_by().values_list(id_field,name_field).distinct())
    names.sort(key=lambda row:(order(row[1]),row[0] or 0))
    expression=Case(*[When(**{id_field:pk},then=Value(index)) for index,(pk,_) in enumerate(names) if pk is not None],default=Value(len(names)),output_field=IntegerField())
    return queryset.order_by(expression,*within)


def machine_order(machine):
    category=machine.catalogue_group or machine.type.category or (machine.type.parent.category if machine.type.parent_id else None)
    root=machine.type.parent or machine.type
    return equipment_group_order(category.name if category else 'Без группы'),root.name.casefold(),machine.type.name.casefold(),machine.plate_number,machine.pk
