from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal, ROUND_CEILING
from django.core.exceptions import ValidationError
from django.db import transaction
from apps.resources.models import Brigade
from .models import RotationRole, RotationPerson


def get_demand(plan):
    from apps.planning.global_services import build_snapshot
    snapshot = (plan.source.snapshot if plan.source.status in {'SUBMITTED', 'APPROVED', 'COMPLETED'} else None) or build_snapshot(plan.source)
    result = defaultdict(int)
    for row in snapshot.get('resources', {}).get('labor', []):
        day = date.fromisoformat(row['date'])
        if plan.start <= day <= plan.end and row.get('brigade_id'):
            value = Decimal(str(row.get('planned_workers') or 0))
            if not value.is_finite() or value < 0:
                raise ValidationError('В исходном плане некорректная численность людей.')
            result[(row['brigade_id'], day)] += int(value.to_integral_value(rounding=ROUND_CEILING))
    valid = set(Brigade.objects.filter(company=plan.company, pk__in=[key[0] for key in result]).values_list('pk', flat=True))
    if any(key[0] not in valid for key in result):
        raise ValidationError('В исходном плане есть должности другой компании.')
    return [{'brigade_id': brigade, 'date': day.isoformat(), 'count': count} for (brigade, day), count in sorted(result.items())]


@transaction.atomic
def refresh_demand(plan):
    plan.demand = get_demand(plan)
    plan.save(update_fields=['demand', 'updated_at'])
    for brigade in {row['brigade_id'] for row in plan.demand}:
        RotationRole.objects.get_or_create(company=plan.company, plan=plan, brigade_id=brigade, defaults={'anchor': plan.start})


def on_shift(person, day):
    elapsed = (day-person.anchor).days
    return elapsed >= 0 and elapsed % (person.on_days+person.off_days) < person.on_days


@transaction.atomic
def generate_people(position):
    position = RotationRole.objects.select_for_update().get(pk=position.pk)
    peak = max((row['count'] for row in position.plan.demand if row['brigade_id'] == position.brigade_id), default=0)
    cycle = position.on_days+position.off_days
    count = (peak*cycle+position.on_days-1)//position.on_days
    if count > 1000:
        raise ValidationError('Для этой должности требуется более 1000 мест. Разделите потребность на несколько планов.')
    existing = position.people.count()
    people = []
    used_names = set(position.people.values_list('name', flat=True))
    next_number = 1
    for index in range(existing, count):
        # Start each cycle before the planning period so all phases are represented from day one.
        anchor = position.anchor - timedelta(days=index*cycle//count)
        if anchor > position.plan.start:
            anchor -= timedelta(days=((anchor-position.plan.start).days+cycle-1)//cycle*cycle)
        while f'{position.brigade.name} №{next_number}' in used_names:
            next_number += 1
        name = f'{position.brigade.name} №{next_number}'
        used_names.add(name)
        people.append(RotationPerson(company=position.company, position=position,
            name=name, on_days=position.on_days, off_days=position.off_days, anchor=anchor))
    RotationPerson.objects.bulk_create(people)
    return len(people)


def matrix(plan, start, end):
    days = [start+timedelta(days=i) for i in range((end-start).days+1)]
    demand = {(row['brigade_id'], row['date']): row['count'] for row in plan.demand}
    result = []
    for position in plan.positions.select_related('brigade').prefetch_related('people'):
        people = [{'person': person, 'cells': [on_shift(person, day) for day in days]} for person in position.people.all()]
        needed = [demand.get((position.brigade_id, day.isoformat()), None) for day in days]
        present = [sum(row['cells'][i] for row in people) for i in range(len(days))]
        result.append({'position': position, 'people': people, 'needed': needed, 'present': present,
            'shortage': [max(need-have, 0) if need is not None else None for need, have in zip(needed, present)],
            'excess': [max(have-need, 0) if need is not None else None for need, have in zip(needed, present)]})
    return days, result
