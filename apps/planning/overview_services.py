"""Compact overview totals; detailed matrices remain available in Plan/Fact."""
from decimal import Decimal
from .report_services import resource_subtotal_rows


def money_row(rows, label):
    def total(cells):
        result = {}
        for key, source in [('plan', 'amount'), ('fact', 'fact_amount')]:
            known = [cell[source] for cell in cells if cell.get(source) is not None]
            result[key] = sum(known, Decimal(0)) if known else None
        result['delta'] = result['fact'] - result['plan'] if all(result[key] is not None for key in ('plan', 'fact')) else None
        return result
    return {'label': label, 'unit': '₽', 'operation': 'sum', 'children': [],
            'cells': [total([row['cells'][index] for row in rows]) for index in range(len(rows[0]['cells']))],
            'total': total([row['total'] for row in rows])}


def compact_overview(report):
    report['total_first'] = True
    all_money, resource_totals = [], {}
    for entry in report['objects']:
        for section in entry['sections']:
            if section['kind'] == 'works':
                rows = [row for node in section['groups'] for row in node['rows'] if row.get('has_amount')]
                all_money.extend(rows)
                section['has_amount'] = False
                section['groups'] = [{'label': 'Стоимость работ', 'children': [], 'rows': [money_row(rows, 'Итого по объекту')] if rows else []}]
            else:
                kind = section['kind']
                resource_totals.setdefault(kind, []).extend(section.get('subtotals', []))
                for node in section['groups']:
                    node['rows'] = []
                    node['children'] = []
    sections = []
    if all_money:
        sections.append({'kind': 'money', 'label': 'Работы', 'groups': [{'label': 'Стоимость работ', 'children': [], 'rows': [money_row(all_money, 'Итого по всем объектам')]}]})
    for kind, rows in resource_totals.items():
        totals = resource_subtotal_rows(rows)
        sections.append({'kind': kind, 'label': {'labor': 'Люди', 'equipment': 'Техника', 'fuel': 'ГСМ'}[kind], 'groups': [{'label': 'Все объекты', 'children': [], 'rows': totals}]})
    if sections:
        report['objects'].insert(0, {'name': 'Все объекты · итоги', 'overview_total': True, 'object': report['objects'][0]['object'], 'sections': sections, 'warnings': [], 'sources': []})
    for entry in report['objects']:
        for section in entry['sections']:
            if section['kind'] == 'works':
                section['kind'] = 'money'
    return report


def overview_tables(report):
    tables = {}
    for entry in report['objects']:
        for section in entry['sections']:
            kind = section['kind']
            table = tables.setdefault(kind, {'kind': kind, 'label': section['label'], 'rows': []})
            if kind == 'money' or entry.get('overview_total'):
                totals = [row for node in section['groups'] for row in node['rows']]
            else:
                totals = section.get('subtotals', [])
            for index, total in enumerate(totals):
                label = 'Все объекты · итого' if entry.get('overview_total') else str(entry['object'].project) + ' · ' + entry['name']
                if kind == 'fuel':
                    label += ' · ' + total['label'].removeprefix('Итого: ')
                children = []
                if not entry.get('overview_total') and kind != 'money':
                    for node in section['groups']:
                        for subtotal in node.get('subtotals', []):
                            if subtotal['operation'] == total['operation'] and subtotal['unit'] == total['unit']:
                                children.append({**subtotal, 'label': node['label']})
                table['rows'].append({**total, 'label': label, 'is_total': bool(entry.get('overview_total')),
                                      'key': f"overview-{kind}-{entry['object'].pk}-{index}", 'children': children})
    return list(tables.values())
