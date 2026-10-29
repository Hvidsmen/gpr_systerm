"""Read-only comparison of independent SMGs, using captured quantities and identities."""
from collections import defaultdict
from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from . import daily_schedule as daily

ZERO = Decimal(0)
LABELS = {'work': 'Работы', 'labor': 'Должности', 'equipment': 'Техника', 'fuel': 'ГСМ'}


def quantities(schedule):
    result = {}
    start, end = daily.bounds(schedule.month)

    def add(key, kind, label, unit, metric, values, operation='sum'):
        row = result.setdefault((kind, *key, metric, unit), {'kind': kind, 'label': label, 'unit': unit, 'metric': metric,
            'values': defaultdict(lambda: ZERO), 'operation': operation})
        for day, value in values:
            if start <= day <= end:
                row['values'][day] += Decimal(str(value))

    entries = list(schedule.entries.all())
    from .work_resources import owner_rows, expand
    for assignment in owner_rows(schedule):
        for token, (kind, label, rows) in expand(assignment).items():
            entries.append(SimpleNamespace(kind=kind, label=label, rows=rows, metadata={}, key=token))
    for entry in entries:
        rows = entry.rows
        if entry.kind == 'work':
            spec = entry.metadata
            wid = spec.get('id', entry.key)
            items = {i['id']: i for i in spec.get('items', [])}
            grouped = defaultdict(list)
            for row in rows:
                grouped[row.get('item_id')].append((date.fromisoformat(row['date']), row.get('quantity', 0)))
            if spec.get('kind') == 'COMPOSITE':
                generated = daily.finalize_work(spec, rows, SimpleNamespace(start_date=start, end_date=end), spec.get('history', []))
                add((wid, 'ready'), 'work', entry.label, spec.get('unit', ''), 'Готовые комплексы',
                    [(date.fromisoformat(r['date']), r['quantity']) for r in generated['daily']])
            for item_id, values in grouped.items():
                item = items.get(item_id, {})
                add((wid, item_id), 'work', entry.label + (' → ' + item.get('name', str(item_id)) if item_id else ''),
                    item.get('unit', spec.get('unit', '')), 'Объём', values)
        elif rows:
            identity = daily.resource_identity(rows[0], entry.kind)
            for field, caption, _ in daily.FIELDS[entry.kind]:
                if field == 'planned_count':
                    unit, operation = ('чел.' if entry.kind == 'labor' else 'ед.'), 'average'
                    caption = 'Средняя численность' if entry.kind == 'labor' else 'Среднее количество'
                else:
                    unit, operation = ('чел·ч' if field == 'planned_hours' else 'маш·ч' if field == 'planned_machine_hours' else 'л'), 'last' if field == 'planned_balance' else 'sum'
                    caption = 'Остаток на конец месяца' if field == 'planned_balance' else 'Расход' if field == 'planned_liters' else 'Часы'
                add(tuple(identity), entry.kind, entry.label, unit, caption,
                    [(date.fromisoformat(r['date']), daily.row_value(entry.kind, r, field)) for r in rows], operation)
    for row in result.values():
        values = row['values']
        row['total'] = values.get(end, ZERO) if row['operation'] == 'last' else sum(values.values(), ZERO) / (Decimal(end.day) if row['operation'] == 'average' else 1)
    return result


def compare(left, right, differences=True):
    first, second = quantities(left), quantities(right)
    rows = defaultdict(list)
    for key in first.keys() | second.keys():
        a, b = first.get(key), second.get(key)
        row = dict(a or b)
        row['left'], row['right'] = a['total'] if a else ZERO, b['total'] if b else ZERO
        days = sorted(set(a['values'] if a else {}) | set(b['values'] if b else {}))
        row['days'] = [{'date': day, 'left': a['values'].get(day, ZERO) if a else ZERO, 'right': b['values'].get(day, ZERO) if b else ZERO} for day in days]
        for day in row['days']:
            day['delta'] = day['right'] - day['left']
        row['delta'] = row['right'] - row['left']
        row['missing_left'], row['missing_right'] = a is None, b is None
        row['different'] = a is None or b is None or any(day['delta'] for day in row['days'])
        if not differences or row['different']:
            rows[row['kind']].append(row)
    return [{'kind': kind, 'label': label, 'rows': sorted(rows[kind], key=lambda row: (row['label'].casefold(), row['metric']))} for kind, label in LABELS.items() if rows[kind]]
