from calendar import monthrange
from datetime import date, timedelta
from django.utils import timezone


def days(month):
    return [date(month.year, month.month, d) for d in range(1, monthrange(month.year, month.month)[1]+1)]


def calendar_for(slot):
    return {**(slot.pattern.calendar if slot.pattern_id else {}), **slot.calendar}


def report(board, month, query='', status='', camp=''):
    today = timezone.localdate()
    dates = days(month)
    result = []; total = dict(attendance=0, staffing=0, employed=0, vacancies=0, on=0, off=0, unknown=0, shortage=0)
    daily = [0] * len(dates)
    people = set()
    for group in board.groups.prefetch_related('slots__person', 'slots__pattern'):
        slots = list(group.slots.all())
        people.update(s.person_id for s in slots if s.person_id)
        stats = dict(attendance=group.attendance_plan, staffing=group.staffing_plan, employed=sum(s.person_id is not None for s in slots), vacancies=sum(s.person_id is None for s in slots), on=sum(s.person_id is not None and s.status=='МВ' for s in slots), off=sum(s.person_id is not None and s.status=='МО' for s in slots), unknown=sum(s.person_id is not None and s.status not in {'МВ','МО'} for s in slots))
        stats['shortage'] = max(group.staffing_plan - stats['employed'], 0)
        rows = []
        for slot in slots:
            cal = calendar_for(slot)
            start = slot.arrival_date if slot.status == 'МВ' else slot.departure_date if slot.status == 'МО' else None
            elapsed = (today-start).days if start and start <= today else None
            working = sorted(d for d, value in cal.items() if d >= today.isoformat() and is_work(value))
            leave = departure_days(cal, today)
            row = dict(slot=slot, codes=[cal.get(day.isoformat(), '') for day in dates], elapsed=elapsed, next_work=working[0] if working else '', next_leave=leave[0] if leave else '')
            for i, code in enumerate(row['codes']):
                if slot.person_id and is_work(code): daily[i] += 1
            name = slot.person.full_name if slot.person_id else 'Вакансия'
            if query and query.casefold() not in (' '.join([name, group.position, slot.person.employee_number if slot.person_id else ''])).casefold(): continue
            if status and (('VACANCY' if not slot.person_id else slot.status or 'UNKNOWN') != status): continue
            if camp and slot.camp != camp: continue
            rows.append(row)
        for key in total: total[key] += stats[key]
        result.append(dict(group=group, stats=stats, rows=rows))
    return dict(groups=result, total=total, dates=dates, daily=daily, today=today, people_count=len(people))


def is_work(value):
    try: return float(value) > 0
    except (ValueError, TypeError): return value == 'МВ'


def departure_days(calendar, today):
    result = []
    for key, value in sorted(calendar.items()):
        if key < today.isoformat() or value != 'ДП': continue
        day = date.fromisoformat(key) + timedelta(days=1)
        while calendar.get(day.isoformat()) == 'ДП': day += timedelta(days=1)
        # An outgoing trip is followed by rest; incoming trips precede work.
        if calendar.get(day.isoformat()) == 'МО':
            previous = (date.fromisoformat(key)-timedelta(days=1)).isoformat()
            if calendar.get(previous) != 'ДП' or key == today.isoformat(): result.append(key)
    return result
