"""Work headings, dated SMG indicators and hierarchy for the daily fact form."""
from decimal import Decimal
from django import forms
from django.db.models import Sum
from apps.works.models import ProjectWork, ProjectWorkItem
from .models import DailyFact


def prepare_rows(formset, obj, day, work_plan, catalogue):
    keys = {}
    for row in formset:
        try:
            keys[row.prefix] = (int(row['project_work'].value()), int(row['work_item'].value()) if row['work_item'].value() else None)
        except (TypeError, ValueError):
            pass
    ids = {choice['work'] for choice in catalogue} | {key[0] for key in keys.values()}
    works = {w.pk: w for w in ProjectWork.objects.filter(company=obj.company, section__construction_object=obj, pk__in=ids).select_related('section', 'complex_object')}
    items = {i.pk: i for i in ProjectWorkItem.objects.filter(company=obj.company, project_work_id__in=works)}
    previous = {(r['project_work_id'], r['work_item_id']): r['quantity'] for r in
        DailyFact.objects.filter(company=obj.company, project_work__section__construction_object=obj, date__lt=day)
        .values('project_work_id', 'work_item_id').annotate(quantity=Sum('actual_quantity'))}

    from .work_opening import opening_rows
    from datetime import timedelta
    for wid, _, item_id, quantity in opening_rows(obj.company, works, day - timedelta(days=1)):
        key = (wid, item_id)
        previous[key] = previous.get(key, Decimal(0)) + quantity

    def display(key):
        work = works.get(key[0]); item = items.get(key[1]) if key[1] else None
        if work is None or key[1] and (item is None or item.project_work_id != work.pk):
            return None
        title = item.name if item else work.name
        path = [work.section.name]
        if work.complex_object_id: path.append(work.complex_object.name)
        if item and work.name != title: path.append(work.name)
        return {'title': title, 'path': ' → '.join(path), 'unit': item.unit if item else work.unit,
            'has_plan': key in work_plan, 'plan': work_plan.get(key), 'previous': previous.get(key, Decimal(0))}

    for choice in catalogue:
        choice['display'] = display((choice['work'], choice['item']))
    groups = {}; extra = []
    for row in formset:
        key = keys.get(row.prefix)
        row.work_display = display(key) if key else None
        if not row.work_display:
            extra.append(row); continue
        row.fields['project_work'].widget = forms.HiddenInput()
        row.fields['work_item'].widget = forms.HiddenInput()
        work = works[key[0]]
        group = groups.setdefault((work.section_id, work.complex_object_id), {'label': work.complex_object.name if work.complex_object_id else 'Самостоятельные работы',
            'section': work.section.name, 'is_complex': bool(work.complex_object_id), 'rows': [], 'packages': {}, 'count': 0})
        group['count'] += 1
        if work.kind == 'COMPOSITE':
            group['packages'].setdefault(work.pk, {'label': work.name, 'rows': []})['rows'].append(row)
        else:
            group['rows'].append(row)
    result = sorted(groups.values(), key=lambda g: (g['section'].casefold(), g['label'].casefold()))
    for group in result:
        group['packages'] = sorted(group['packages'].values(), key=lambda p: p['label'].casefold())
    return result, extra
