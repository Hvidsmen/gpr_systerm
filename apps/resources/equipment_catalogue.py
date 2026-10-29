"""Presentation hierarchy: category, planning type, then model variants."""


def with_machine_counts(queryset, company):
    from django.db.models import Count, OuterRef, Q, Subquery, Value
    from django.db.models.functions import Coalesce
    from .models import Equipment
    machines = Equipment.objects.filter(company=company).filter(
        Q(type_id=OuterRef('pk')) | Q(type__parent_id=OuterRef('pk'))).order_by().values('company').annotate(total=Count('pk'))
    return queryset.annotate(machine_count=Coalesce(Subquery(machines.values('total')[:1]), Value(0)))


def category_for(equipment, company):
    if equipment is None:
        return None
    root = equipment.parent if equipment.parent_id else equipment
    if root.company_id != company.pk:return None
    category = root.category
    return category if category and category.company_id == company.pk else None


def hierarchy(rows, roots, company):
    categories = {}
    for row in rows:
        root = roots.get(row.parent_id or row.pk)
        if root is None:
            continue
        category = category_for(row, company)
        key = category.pk if category else None
        group = categories.setdefault(key, {'category': category, 'label': category.name if category else 'Без группы', 'nodes': {}, 'count': 0})
        node = group['nodes'].setdefault(root.pk, {'root': root, 'children': [], 'root_matches': False})
        group['count'] += 1
        if row.parent_id:
            node['children'].append(row)
        else:
            node['root_matches'] = True
    from .group_order import equipment_group_order
    groups = sorted(categories.values(), key=lambda group: equipment_group_order(group['category']))
    for group in groups:
        group['nodes'] = sorted(group['nodes'].values(), key=lambda node: (node['root'].name.casefold(), node['root'].pk))
    return groups


def planning_hierarchy(rows, roots):
    """Keep each planning type together with the group inherited by its models."""
    nodes = {}
    for row in rows:
        root = roots.get(row.parent_id or row.pk)
        if root is None:
            continue
        node = nodes.setdefault(root.pk, {'root': root, 'children': [], 'root_matches': False})
        if row.parent_id:
            node['children'].append(row)
        else:
            node['root_matches'] = True
    for node in nodes.values():
        node['children'].sort(key=lambda row: (row.name.casefold(), row.pk))
    return sorted(nodes.values(), key=lambda node: (node['root'].name.casefold(), node['root'].pk))
