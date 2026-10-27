"""Presentation hierarchy: category, planning type, then model variants."""


def category_for(equipment, company):
    if equipment is None:
        return None
    category = equipment.category
    if category is None and equipment.parent_id and equipment.parent.company_id == company.pk:
        category = equipment.parent.category
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
    groups = sorted(categories.values(), key=lambda group: (group['category'] is None, group['label'].casefold()))
    for group in groups:
        group['nodes'] = sorted(group['nodes'].values(), key=lambda node: (node['root'].name.casefold(), node['root'].pk))
    return groups
