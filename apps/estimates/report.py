"""Report hierarchy and totals; source subtotal rows are never calculation inputs."""
from decimal import Decimal, ROUND_HALF_UP
from .excel import number

ZERO = Decimal('0')


def rounded(value, digits='0.01'):
    return value.quantize(Decimal(digits), rounding=ROUND_HALF_UP)


def build_report(rows):
    works = []; work = None; sub = None
    for row in rows:
        v = row['values']; name = str(v[2] or '').strip()
        if row['kind'] == 'section':
            work = {'name': str(v[0]), 'source_row': row['source_row'], 'subworks': []}
            works.append(work); sub = None
        elif row['kind'] == 'item':
            if work is None:
                work = {'name': 'Без раздела', 'source_row': None, 'subworks': []}; works.append(work)
            sub = {'name': name, 'source_row': row['source_row'], 'code': v[1], 'unit': v[3], 'quantity': v[6], 'values': v, 'resources': [], 'components': {}, 'charges': [], 'warnings': []}
            work['subworks'].append(sub)
        elif sub and row.get('parent_row') == sub['source_row']:
            if row['kind'] == 'resource':
                sub['resources'].append(row)
            elif name in {'ОТ', 'ЭМ', 'М', 'О', 'ОБ'}:
                sub['components'][name] = v
            elif name.startswith(('НР ', 'СП ')):
                sub['charges'].append(v)
            elif name.replace(' ', '').lower() in {'вт.ч.отм', 'вт.ч.отм.'}:
                sub['driver'] = v
    for work in works:
        for sub in work['subworks']:
            comps = sub['components']; v = sub['values']; warnings = sub['warnings']
            if comps:
                base = ZERO; current = ZERO; valid = True
                for key, c in comps.items():
                    amount = number(c[9]); index = number(c[10])
                    if amount is None:
                        warnings.append(f'{key}: отсутствует базисная стоимость.'); valid = False; continue
                    base += amount
                    if index is not None:
                        current += rounded(amount * index, '1')
                    elif number(c[11]) is not None:
                        current += number(c[11])
                    else:
                        warnings.append(f'{key}: отсутствуют индекс и текущая стоимость.'); valid = False
                ot = comps.get('ОТ'); driver = sub.get('driver')
                wage_base = (number(ot[9]) or ZERO) if ot else ZERO
                wage_current = rounded(wage_base*(number(ot[10]) or ZERO),'1') if ot else ZERO
                if driver:
                    wage_base += number(driver[9]) or ZERO
                    if number(driver[10]) is not None:
                        wage_current += rounded((number(driver[9]) or ZERO)*number(driver[10]),'1')
                    elif number(driver[11]) is not None:
                        wage_current += number(driver[11])
                    else:
                        valid = False;warnings.append('Не определена оплата труда машинистов в текущих ценах.')
                for charge in sub['charges']:
                    percent = number(charge[6])
                    if percent is None:
                        valid = False;warnings.append('Не определен процент НР/СП.');continue
                    base += rounded(wage_base * percent / 100)
                    current += rounded(wage_current * percent / 100,'1')
                sub['base'] = rounded(base) if valid else None
                sub['current'] = rounded(current) if valid else None
                sub['method'] = 'ОТ + ЭМ + М + начисления НР/СП от ФОТ; ОТм отдельно не прибавляется'
            else:
                # A separately numbered material/equipment line is itself a price input.
                sub['base'] = number(v[9]);sub['current'] = number(v[11])
                if sub['current'] is None and sub['base'] is not None and number(v[10]) is not None:
                    sub['current'] = rounded(sub['base'] * number(v[10]),'1')
                sub['method'] = 'Стоимость отдельного материала / оборудования из строки подработы'
                if sub['base'] is None or sub['current'] is None:
                    warnings.append('Недостаточно данных для расчета стоимости. Итог из файла не подставлялся.')
            # Source totals are diagnostics only, never operands.
        work['base'] = sum((s['base'] for s in work['subworks']),ZERO) if all(s['base'] is not None for s in work['subworks']) else None
        work['current'] = sum((s['current'] for s in work['subworks']),ZERO) if all(s['current'] is not None for s in work['subworks']) else None
    diagnostics = []
    source = next((number(r['values'][11]) for r in reversed(rows) if str(r['values'][2] or '').strip().lower() == 'всего по смете'), None)
    computed = sum((w['current'] for w in works), ZERO) if all(w['current'] is not None for w in works) else None
    if source is not None and computed is not None and source != computed:
        diagnostics.append(f'Расчет отличается от контрольного итога файла на {computed-source:,.2f} ₽. Итог файла не подставлялся.')
    if computed is None:
        diagnostics.append('Итог не определен: есть подработы с недостаточными данными. Проверьте предупреждения внутри работ.')
    return {'diagnostics':diagnostics, 'works':works, 'base':sum((w['base'] for w in works),ZERO) if all(w['base'] is not None for w in works) else None, 'current':sum((w['current'] for w in works),ZERO) if all(w['current'] is not None for w in works) else None}
