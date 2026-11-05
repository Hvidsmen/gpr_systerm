"""Work headings, dated SMG indicators and hierarchy for the daily fact form."""
from decimal import Decimal
from django import forms
from django.db.models import Sum
from apps.works.models import ProjectWork, ProjectWorkItem
from .models import DailyFact


def plan_before(obj,day):
    """Latest applied revision per month, strictly before the selected fact day."""
    from apps.planning.models import DailySchedule
    from datetime import date
    totals={};seen=set()
    schedules=DailySchedule.objects.filter(company=obj.company,construction_object=obj,status='APPLIED',month__lte=day.replace(day=1)).prefetch_related('entries').order_by('month','-revision','-pk')
    for schedule in schedules:
        if schedule.month in seen:continue
        seen.add(schedule.month)
        for entry in schedule.entries.all():
            if entry.kind!='work':continue
            work_id=entry.metadata.get('id')
            if work_id is None and entry.key.startswith('work:'):
                try:work_id=int(entry.key.split(':',1)[1])
                except ValueError:continue
            if work_id is None:continue
            for row in entry.rows:
                try:current=date.fromisoformat(row.get('date',''))
                except (TypeError,ValueError):continue
                if current>=day:continue
                key=(int(work_id),int(row['item_id']) if row.get('item_id') else None)
                totals[key]=totals.get(key,Decimal(0))+Decimal(str(row.get('quantity') or 0))
    return totals


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

    accumulated_plan=plan_before(obj,day)

    def display(key):
        work = works.get(key[0]); item = items.get(key[1]) if key[1] else None
        if work is None or key[1] and (item is None or item.project_work_id != work.pk):
            return None
        title = item.name if item else work.name
        path = [work.section.name]
        if work.complex_object_id: path.append(work.complex_object.name)
        # For a composite row the item itself is the summary work.  The parent
        # ProjectWork is an internal catalogue container, not a visible level.
        planned_before=accumulated_plan.get(key)
        actual_before=previous.get(key,Decimal(0))
        deviation=((actual_before/planned_before-1)*100).quantize(Decimal('.01')) if planned_before is not None and planned_before>0 else None
        return {'plan_before':planned_before,'deviation':deviation,'title': title, 'path': ' → '.join(path), 'unit': item.unit if item else work.unit,
            'has_plan': key in work_plan, 'plan': work_plan.get(key), 'previous': previous.get(key, Decimal(0))}

    catalogue_by_key={(choice['work'],choice['item']):choice for choice in catalogue}
    for choice in catalogue:
        choice['display'] = display((choice['work'], choice['item']))
    groups = {}; extra = []
    for row in formset:
        key = keys.get(row.prefix)
        row.work_display = display(key) if key else None
        row.work_choice = catalogue_by_key.get(key)
        if not row.work_display:
            extra.append(row); continue
        row.fields['project_work'].widget = forms.HiddenInput()
        row.fields['work_item'].widget = forms.HiddenInput()
        work = works[key[0]]
        if work.kind == 'COMPOSITE' and key[1]:
            # A work item is the summary work, while the complex object is the
            # place where its fact is entered.  ProjectWork is only a catalogue
            # container here and must not add another visible hierarchy level.
            item = items[key[1]]
            summary_key = (work.section_id, ' '.join(item.name.split()).casefold(), item.unit)
            group = groups.setdefault(summary_key, {'label': item.name, 'section': work.section.name,
                'is_complex': True, 'objects': {}, 'rows': [], 'count': 0})
            object_group = group['objects'].setdefault(work.complex_object_id or 0,
                {'label': work.complex_object.name if work.complex_object_id else 'Без комплексного объекта', 'rows': []})
            object_group['rows'].append(row)
        else:
            summary_key = (work.section_id, 'standalone', work.pk, key[1] or 0)
            group = groups.setdefault(summary_key, {'label': row.work_display['title'], 'section': work.section.name,
                'is_complex': False, 'objects': {}, 'rows': [], 'count': 0})
            group['rows'].append(row)
        group['count'] += 1
    result = sorted(groups.values(), key=lambda g: (g['section'].casefold(), g['label'].casefold()))
    for group in result:
        group['objects'] = sorted(group['objects'].values(), key=lambda obj: obj['label'].casefold())
        group['object_count'] = len(group['objects'])
        rows=[row for object_group in group['objects'] for row in object_group['rows']] if group['is_complex'] else group['rows']
        group['unit']=rows[0].work_display['unit'] if rows else ''
        planned=[row.work_display['plan'] for row in rows if row.work_display['has_plan']]
        group['has_plan']=bool(planned)
        group['plan_total']=sum(planned,Decimal(0))
        actual=[]
        for row in rows:
            value=row['actual_quantity'].value()
            if value not in (None,''):
                try:actual.append(Decimal(str(value).replace(',','.')))
                except (ValueError,ArithmeticError):pass
        group['actual_total']=sum(actual,Decimal(0))
    return result, extra
