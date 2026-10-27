"""Present only allowed object choices, with non-selectable ancestor context."""
import json
from apps.projects.models import ConstructionObject


def tree_options(options):
    objects = [o['value'].instance for o in options if isinstance(getattr(o.get('value'), 'instance', None), ConstructionObject)]
    if not objects: return options
    company_ids = {obj.company_id for obj in objects}
    catalog = {r['pk']:r for r in ConstructionObject.objects.filter(company_id__in=company_ids)
        .values('pk', 'name', 'parent_id', 'company_id', 'project_id')}
    selected = {obj.pk:option for obj,option in [(o['value'].instance,o) for o in options if isinstance(getattr(o.get('value'), 'instance', None), ConstructionObject)]}
    nodes = {}; example = next(iter(selected.values()))
    def ancestry(pk):
        row = catalog[pk]; path = [pk]; parent = row['parent_id']
        while parent in catalog and parent not in path:
            ancestor = catalog[parent]
            if (ancestor['company_id'],ancestor['project_id']) != (row['company_id'],row['project_id']): break
            path.insert(0,parent); parent=ancestor['parent_id']
        return path
    for pk in selected:
        if pk not in catalog: continue
        for ancestor in ancestry(pk):
            nodes.setdefault(ancestor, selected.get(ancestor))
    children = {}
    for pk in nodes:
        path = ancestry(pk); parent = path[-2] if len(path)>1 else None
        children.setdefault(parent,[]).append(pk)
    output = [o for o in options if not isinstance(getattr(o.get('value'), 'instance', None), ConstructionObject)]
    def append(parent):
        for pk in sorted(children.get(parent,[]),key=lambda k:(catalog[k]['name'].casefold(),k)):
            path = ancestry(pk); depth=len(path)-1; original=nodes[pk]
            option = dict(original or example); option['attrs']=dict(original['attrs']) if original else {'disabled':True}
            if original is None:
                option.update(value='',selected=False,index=f'context-{pk}')
            option['label'] = ('　' * depth + '└ ' if depth else '') + catalog[pk]['name']
            option['attrs'].update({'data-object-id':str(pk),'data-object-label':catalog[pk]['name'],
                'data-object-path':' → '.join(catalog[key]['name'] for key in path),
                'data-object-ancestors':json.dumps(path[:-1]),'data-object-depth':str(depth)})
            if original is None:option['attrs']['data-object-context']='true'
            output.append(option); append(pk)
    append(None)
    return output


def options_for(objects, selected=None):
    from django.forms.models import ModelChoiceIteratorValue
    return tree_options([{'name':'','value':ModelChoiceIteratorValue(obj.pk,obj),'label':obj.name,'selected':str(obj.pk)==str(selected),
        'index':str(index),'attrs':{},'type':'select','template_name':'django/forms/widgets/select_option.html','wrap_label':True}
        for index,obj in enumerate(objects)])
