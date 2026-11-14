"""Read-only comparison against one applied monthly SMG, without GPR fallback."""
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

from django.http import Http404
from apps.planning.daily_schedule import bounds
from apps.planning.models import DailySchedule
from apps.resources.models import Brigade, EquipmentType, PositionPlanningBrigade
from apps.resources.brigade_merge import brigade_aliases
from apps.resources.equipment_merge import equipment_aliases
from apps.works.models import ProjectWork
from apps.personnel.models import Camp
from .models import DailyFact, EmployeeActivity, EquipmentActivity, LaborFact, EquipmentFact
from .transport_models import EquipmentDayRecord
from .camp_fuel_models import CampFuelDay
from .report_grouping import resource_groups, work_groups, daily_rows, selected_daily_totals, totals
from apps.resources.group_order import NONE, labor_group_order, equipment_group_order, position_group


def number(value):
    if value is None or value == '':
        return None
    try:
        result = Decimal(str(value))
        return result if result.is_finite() else None
    except (InvalidOperation, ValueError, TypeError):
        return None


def identity(value):
    try:
        return int(value) if value is not None and value != '' else None
    except (ValueError, TypeError):
        return None


def canonical(value, aliases):
    value = identity(value)
    seen = set()
    while value in aliases and value not in seen:
        seen.add(value)
        value = aliases[value]
    return value


def bucket(rows, key, label, unit, context=''):
    return rows.setdefault(key, dict(label=str(label), unit=str(unit), context=str(context),
        plan=None, actual=None, days=set(), daily={}, comments=[], operational_facts=[]))


def add(row, field, value, day=None):
    value = number(value)
    if value is not None:
        row[field] = (row[field] or Decimal(0)) + value
        if day:
            daily = row['daily'].setdefault(day.isoformat(), {'plan':None,'actual':None,'unit':row['unit']})
            daily[field] = (daily[field] or Decimal(0)) + value
        if field == 'actual' and day:
            row['days'].add(day)


def finish(rows):
    result = []
    for row in rows.values():
        plan, actual = row['plan'], row['actual']
        row['deviation'] = actual - plan if actual is not None and plan is not None else None
        row['percent'] = actual / plan * 100 if actual is not None and plan is not None and plan > 0 else None
        row['recorded_days'] = len(row.pop('days'))
        result.append(row)
    return sorted(result, key=lambda r: (r['context'].casefold(), r['label'].casefold(), r['unit']))


def sum_present(rows, field):
    values = [row[field] for row in rows if row.get(field) is not None]
    return sum(values, Decimal(0)) if values else None


def build(obj, day, schedule_id=None, *, day_only=False, equipment_only=False, include_fuel=True):
    start, end = bounds(day.replace(day=1))
    schedules = DailySchedule.objects.filter(company=obj.company, construction_object=obj, month=day.replace(day=1))
    if day_only:
        start = end = day
    labor_unit = 'чел.'
    equipment_unit = 'ед.'
    if schedule_id:
        schedule = schedules.filter(pk=identity(schedule_id), status__in=['APPLIED', 'ARCHIVED']).first()
        if schedule is None:
            raise Http404('Эта версия СМГ недоступна для выбранного объекта и месяца.')
    else:
        schedule = schedules.filter(status='APPLIED').order_by('-revision').first()

    # All work IDs are local to this object, including SMGs inherited by subobjects.
    works = {} if equipment_only else {w.pk: w for w in ProjectWork.objects.filter(company=obj.company,
        section__construction_object=obj).select_related('section', 'complex_object').prefetch_related('items')}
    work_rows, labor_rows, equipment_rows, fuel_rows = {}, {}, {}, {}
    operational_matches=[]
    all_labor = defaultdict(set)
    labor_alias = {} if equipment_only else brigade_aliases(obj.company)
    equipment_alias = equipment_aliases(obj.company)
    brigade_models = {} if equipment_only else {b.pk: b for b in Brigade.objects.filter(company=obj.company).select_related('group')}
    brigades = {pk:b.name for pk,b in brigade_models.items()}
    types = {t.pk: t for t in EquipmentType.objects.filter(company=obj.company).select_related('category')}

    def equipment_key(pk):
        pk = canonical(pk, equipment_alias)
        item = types.get(pk)
        return canonical(item.parent_id, equipment_alias) if item and item.parent_id else pk

    def resource(rows, pk, label, unit):
        target = bucket(rows, pk, label, unit)
        item = brigade_models.get(pk) if rows is labor_rows else types.get(pk) if rows is equipment_rows else None
        group = (item.group if item and item.group_id else None) if rows is labor_rows else (item.category if item and item.category_id else None) if rows is equipment_rows else None
        if group and group.company_id != obj.company_id:group = None
        rank = labor_group_order(group) if rows is labor_rows else equipment_group_order(group)
        target.update(group=group.name if group else 'Без группы',group_id=group.pk if group else None,group_order=rank[0])
        return target

    def work_row(work_id, item_id, metadata=None):
        work = works.get(work_id)
        if not work:
            return None
        # A composite's header is not an additional completed work quantity.
        if work.kind == 'COMPOSITE' and item_id is None:
            return None
        item = next((i for i in work.items.all() if i.pk == item_id and i.company_id == obj.company_id), None) if item_id else None
        if item_id and item is None:
            return None
        meta = metadata or {}
        frozen = next((i for i in meta.get('items', []) if identity(i.get('id')) == item_id), {}) if item_id else meta
        label = frozen.get('name') or (item.name if item else work.name)
        unit = frozen.get('unit') or (item.unit if item else work.unit)
        context = ' → '.join(p for p in [work.section.name,
            work.complex_object.name if work.complex_object_id else '', work.name if item else ''] if p)
        target = bucket(work_rows, (work_id, item_id, unit), label, unit, context)
        target.update(section_id=work.section_id,section=work.section.name,kind=work.kind,
            complex_id=work.complex_object_id,complex=work.complex_object.name if work.complex_object_id else '',
            package=work.name if item else '')
        return target

    def operational_row(fact):
        match=getattr(fact,'operational_match',None)
        if match:
            return work_row(match.target_work_id,match.target_item_id)
        work = works.get(fact.project_work_id)
        if not work or not fact.operational_key:
            return None
        # Older/manual rows may have been saved before both fields became
        # mandatory.  They still carry a real daily quantity and must not
        # disappear from either report.
        label = fact.operational_name.strip() or 'Работа без названия'
        unit = fact.operational_unit.strip() or work.unit
        context = ' → '.join(p for p in [work.section.name,
            work.complex_object.name if work.complex_object_id else '', work.name] if p)
        target = bucket(work_rows, ('operational', fact.operational_key, unit), label, unit, context)
        target.update(section_id=work.section_id, section=work.section.name, kind='OPERATIONAL',
            complex_id=work.complex_object_id, complex=work.complex_object.name if work.complex_object_id else '',
            package=work.name)
        return target

    entries = list((schedule.entries.filter(kind='equipment') if equipment_only else schedule.entries.all()).order_by('pk')) if schedule else []
    for entry in entries:
        if entry.kind=='fuel' and not include_fuel:continue
        metadata = entry.metadata or {}
        work_id = identity(metadata.get('id'))
        if entry.kind == 'work' and work_id is None and entry.key.startswith('work:'):
            work_id = identity(entry.key.split(':')[1])
        for value in entry.rows:
            try:
                row_day = date.fromisoformat(value.get('date', ''))
            except (ValueError, TypeError):
                continue
            if not start <= row_day <= end:
                continue
            if entry.kind == 'work':
                target = work_row(work_id, identity(value.get('item_id')), metadata)
                if target is not None:
                    add(target, 'plan', value.get('quantity'), row_day)
            elif entry.kind == 'labor':
                pk = canonical(value.get('brigade_id'), labor_alias)
                if pk in brigades:
                    target = resource(labor_rows, pk, brigades[pk], labor_unit)
                    add(target, 'plan', value.get('planned_workers', value.get('planned_count')), row_day)
            elif entry.kind == 'equipment':
                pk = equipment_key(value.get('equipment_type_id'))
                if pk in types:
                    target = resource(equipment_rows, pk, types[pk].name, equipment_unit)
                    add(target, 'plan', value.get('planned_count'), row_day)
            elif entry.kind == 'fuel':
                # Planning still stores/labels liters. Warehouse numbers are m³;
                # scopes and units intentionally remain separate, never silently converted.
                pk = value.get('fuel_type') or 'DIESEL'
                from .models import FuelFact
                label = dict(FuelFact.FUEL_TYPE_CHOICES).get(pk, entry.label or str(pk))
                add(resource(fuel_rows, pk, label, 'л'), 'plan', value.get('planned_liters'), row_day)

    scope = dict(company=obj.company, construction_object=obj, date__range=(start, end))
    if not equipment_only:
        for fact in DailyFact.objects.filter(company=obj.company, project_work__section__construction_object=obj,
                date__range=(start, end)).select_related('operational_match__target_work','operational_match__target_item').order_by('pk'):
            # Actual quantities use today's unit; a changed frozen unit forms a separate row.
            target = operational_row(fact) if fact.operational_key else work_row(fact.project_work_id, fact.work_item_id)
            if target is not None:
                add(target, 'actual', fact.actual_quantity, fact.date)
                text=(fact.comment or '').strip()
                if text:
                    entry={'date':fact.date,'text':text}
                    if entry not in target['comments']:target['comments'].append(entry)
                if fact.operational_key:
                    match=getattr(fact,'operational_match',None)
                    matching={'id':fact.pk,'date':fact.date,'name':fact.operational_name or 'Работа без названия',
                        'unit':fact.operational_unit,'matched':bool(match),'target':match.target_label if match else ''}
                    target['operational_facts'].append(matching);operational_matches.append(matching)

        mapping = dict(PositionPlanningBrigade.objects.filter(company=obj.company).values_list('position_id', 'brigade_id'))
        named_labor = defaultdict(set)
        activities = EmployeeActivity.objects.filter(**scope).select_related(
            'employee', 'position__planning_mapping__brigade__group',
            'planning_position__planning_mapping__brigade__group')
        for activity in activities:
            if activity.planning_position_id:
                position = activity.planning_position
                pk = canonical(mapping.get(position.pk), labor_alias)
                key = pk if pk in brigades else f'planning-position:{position.pk}'
                label = brigades.get(pk) or position.name
                resource(labor_rows, key, label, labor_unit)
            else:
                position = activity.position or activity.employee.position
                key = f'staff-position:{position.pk if position else "unknown"}'
                label = position.name if position else 'Должность не указана'
                target = bucket(labor_rows, key, label, labor_unit)
                target.update(group='Люди без должностей СМГ', group_id='non-smg',
                    group_order=NONE, subgroup=position_group(position))
            named_labor[(activity.date, key)].add(activity.employee_id)
            all_labor[activity.date].add(activity.employee_id)
        for (actual_day, key), employees in named_labor.items():
            add(labor_rows[key], 'actual', len(employees), actual_day)
        for fact in LaborFact.objects.filter(**scope):
            pk = canonical(fact.brigade_id, labor_alias)
            if (fact.date, pk) not in named_labor and pk in brigades:
                add(resource(labor_rows, pk, brigades[pk], labor_unit), 'actual', fact.actual_workers, fact.date)

    named_equipment = defaultdict(set)
    projected_equipment_days = set()
    transport_days = set()
    for record in EquipmentDayRecord.objects.filter(**scope).select_related('equipment__type'):
        pk = equipment_key(record.planning_type_id or record.equipment.type_id)
        if pk not in types:
            continue
        transport_days.add((record.date, record.equipment_id))
        # The legacy transport projection may still carry the machine's model/root
        # instead of its explicit planning override. Suppress both projections.
        projected_equipment_days.add((record.date, equipment_key(record.equipment.type_id)))
        resource(equipment_rows, pk, types[pk].name, equipment_unit)
        named_equipment[(record.date, pk)]  # An explicitly idle day is an actual zero.
        if record.status == 'WORKED':
            named_equipment[(record.date, pk)].add(record.equipment_id)
    for activity in EquipmentActivity.objects.filter(**scope).select_related('equipment__type'):
        if (activity.date, activity.equipment_id) in transport_days:
            continue
        pk = equipment_key(activity.equipment.type_id)
        if pk not in types:
            continue
        resource(equipment_rows, pk, types[pk].name, equipment_unit)
        named_equipment[(activity.date, pk)]
        if activity.state == 'WORKING':
            named_equipment[(activity.date, pk)].add(activity.equipment_id)
    for (actual_day, pk), machines in named_equipment.items():
        add(equipment_rows[pk], 'actual', len(machines), actual_day)
    # Detailed records replace their projected/legacy aggregate for the same type/day.
    for fact in EquipmentFact.objects.filter(**scope):
        pk = equipment_key(fact.equipment_type_id)
        if (fact.date, pk) not in named_equipment and (fact.date, pk) not in projected_equipment_days and pk in types:
            add(resource(equipment_rows, pk, types[pk].name, equipment_unit), 'actual', fact.actual_count, fact.date)

    camps = [] if equipment_only or not include_fuel else list(Camp.objects.filter(company=obj.company, object_links__company=obj.company,
        object_links__construction_object=obj).distinct().order_by('name', 'pk'))
    fuel_days = defaultdict(list)
    for record in (CampFuelDay.objects.none() if equipment_only or not include_fuel else CampFuelDay.objects.filter(company=obj.company, camp__in=camps,
            fuel_type='TOTAL', date__range=(start, end)).order_by('date', 'pk')):
        fuel_days[record.camp_id].append(record)
    warehouse = []
    for camp in camps:
        records = fuel_days[camp.pk]
        warehouse.append(dict(label=camp.name, unit='м³', recorded_days=len(records),
            received=sum((r.received_liters for r in records), Decimal(0)) if records else None,
            actual=sum((r.actual_liters for r in records), Decimal(0)) if records else None))

    works_finished, labor_finished, equipment_finished = finish(work_rows), finish(labor_rows), finish(equipment_rows)
    fuel_finished = finish(fuel_rows)
    for row in equipment_finished:
        if row['plan'] is None and row['actual'] is not None:
            row['subgroup']=row.get('group') or 'Без группы'
            row.update(group='Техника вне СМГ',group_id='non-smg',group_order=NONE)
    dates=[start+timedelta(days=i) for i in range((end-start).days+1)]
    for row in works_finished+labor_finished+equipment_finished:daily_rows(row,dates)
    for row in labor_finished+equipment_finished:selected_daily_totals(row,day)
    labor_total=totals(labor_finished,dates)
    for actual_day,employees in all_labor.items():
        values=labor_total['daily'].setdefault(actual_day.isoformat(),{'plan':None,'actual':None,'unit':labor_unit})
        values['actual']=Decimal(len(employees))
    daily_rows(labor_total,dates);selected_daily_totals(labor_total,day)
    equipment_total=totals(equipment_finished,dates);selected_daily_totals(equipment_total,day)
    labor_grouped=resource_groups(labor_finished,dates)
    equipment_grouped=resource_groups(equipment_finished,dates)
    for group in labor_grouped+equipment_grouped:
        selected_daily_totals(group['total'],day)
        for subgroup in group.get('subgroups',[]):selected_daily_totals(subgroup['total'],day)
    return dict(work_groups=work_groups(works_finished,dates),labor_groups=labor_grouped,equipment_groups=equipment_grouped,
        object=obj, start=start, end=end, day=day, schedule=schedule,equipment_only=equipment_only,day_only=day_only,include_fuel=include_fuel,
        period_days=(end-start).days+1, works=works_finished, labor=labor_finished,
        operational_matches=sorted(operational_matches,key=lambda fact:(fact['date'],fact['name'],fact['id'])),
        labor_total=labor_total,equipment=equipment_finished,equipment_total=equipment_total,
        fuel=fuel_finished, fuel_total={'plan':sum_present(fuel_finished,'plan')}, warehouse=warehouse,
        warehouse_total={'received':sum_present(warehouse,'received'),'actual':sum_present(warehouse,'actual')})
