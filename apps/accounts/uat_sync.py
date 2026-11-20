"""Idempotent application of a read-only UAT snapshot to portal records."""
from collections import defaultdict
from datetime import date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import re

from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from apps.production.models import EquipmentFact
from apps.resources.models import Employee, Equipment, EquipmentType
from .models import Company, OneCConnection, UatDepartment, UatRepairSheet, UatVehicle, UatWaybill, UatWaybillTask


UNCLASSIFIED = 'Не классифицировано — УАТ'
PLATE_TRANSLATION = str.maketrans('АВЕКМНОРСТУХ', 'ABEKMHOPCTYX')


def text(value, limit=None):
    value = ' '.join(str(value or '').split())
    return value[:limit] if limit else value


def source_id(value):
    return text(value, 64).upper()


def normalized(value, *, plate=False):
    value = text(value).upper()
    if plate:
        value = value.translate(PLATE_TRANSLATION)
    return re.sub(r'[^0-9A-ZА-Я]', '', value)


def decimal(value):
    if value in (None, ''):
        return None
    try:
        return Decimal(str(value).replace(',', '.'))
    except (InvalidOperation, ValueError, TypeError):
        return None


def parsed_date(value):
    try:
        return date.fromisoformat(str(value)[:10])
    except (ValueError, TypeError):
        return None


def parsed_datetime(value):
    if not value:
        return None
    try:
        result = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    except (ValueError, TypeError):
        return None
    if timezone.is_naive(result):
        result = timezone.make_aware(result, timezone.get_default_timezone())
    return result


def employee_index(company):
    result = defaultdict(list)
    for employee in Employee.objects.filter(company=company):
        key = normalized(employee.employee_number)
        if key:
            result[key].append(employee)
    return result


def match_driver(index, number, name):
    key = normalized(number)
    if not key:
        return None, 'NOT_FOUND' if name else 'EMPTY'
    candidates = index.get(key, [])
    return (candidates[0], 'MATCHED') if len(candidates) == 1 else (None, 'AMBIGUOUS' if candidates else 'NOT_FOUND')


def equipment_indexes(company):
    indexes = {key: defaultdict(list) for key in ('vin', 'plate', 'inventory')}
    for machine in Equipment.objects.filter(company=company).select_related('type'):
        for key, value, is_plate in (
            ('vin', machine.vin, False), ('plate', machine.plate_number, True),
            ('inventory', machine.inventory_number, False),
        ):
            token = normalized(value, plate=is_plate)
            if token:
                indexes[key][token].append(machine)
    return indexes


def match_vehicle(row, indexes):
    for key, value, is_plate in (
        ('vin', row.get('vin'), False), ('plate', row.get('plate'), True),
        ('inventory', row.get('garage_number'), False),
    ):
        token = normalized(value, plate=is_plate)
        if not token:
            continue
        candidates = indexes[key].get(token, [])
        if len(candidates) == 1:
            return candidates[0], 'MATCHED', key.upper()
        if len(candidates) > 1:
            return None, 'AMBIGUOUS', key.upper()
    return None, 'UNMATCHED', ''


@transaction.atomic
def auto_match_vehicles_by_vin(company):
    """Persist every unambiguous VIN match without waiting for a manual save."""
    vin_index = equipment_indexes(company)['vin']
    rows = UatVehicle.objects.select_for_update().filter(company=company).exclude(vin='').exclude(match_basis='MANUAL')
    changed = 0
    for row in rows:
        token = normalized(row.vin)
        if not token:
            continue
        candidates = list(vin_index.get(token, []))
        # A machine created for this very UAT row is only a placeholder.  Do
        # not confirm it by matching it to its own copied VIN; if a real portal
        # machine has since appeared, prefer that machine instead.
        generated_key = ('uat:' + row.source_id)[:80]
        candidates = [
            machine for machine in candidates
            if not (
                row.match_status == 'CREATED'
                and machine.pk == row.equipment_id
                and machine.import_key == generated_key
            )
        ]
        if not candidates:
            continue
        candidate_ids = [machine.pk for machine in candidates]
        occupied = set(
            UatVehicle.objects.filter(company=company, equipment_id__in=candidate_ids)
            .exclude(pk=row.pk)
            .values_list('equipment_id', flat=True)
        )
        available = [machine for machine in candidates if machine.pk not in occupied]
        if len(available) != 1:
            if not row.equipment_id and (len(candidates) > 1 or occupied):
                fields = []
                if row.match_status != 'AMBIGUOUS':
                    row.match_status = 'AMBIGUOUS';fields.append('match_status')
                if row.match_basis != 'VIN':
                    row.match_basis = 'VIN';fields.append('match_basis')
                if fields:
                    row.save(update_fields=fields + ['updated_at'])
            continue
        machine = available[0]
        if row.equipment_id == machine.pk and row.match_status == 'MATCHED' and row.match_basis == 'VIN':
            continue
        row.equipment = machine
        row.match_status = 'MATCHED'
        row.match_basis = 'VIN'
        row.save(update_fields=['equipment', 'match_status', 'match_basis', 'updated_at'])
        changed += 1
    return changed


def unclassified_type(company):
    row = EquipmentType.objects.filter(company=company, name=UNCLASSIFIED, parent=None).first()
    if row:
        return row
    return EquipmentType.objects.create(company=company, name=UNCLASSIFIED, brand_model=UNCLASSIFIED, is_active=True)


def create_vehicle(company, row, kind):
    machine = Equipment(
        company=company, type=kind, name=text(row.get('name') or row.get('plate') or 'Техника УАТ', 150),
        brand_model=text(row.get('name'), 150), plate_number=text(row.get('plate'), 50),
        vin=text(row.get('vin'), 100), inventory_number=text(row.get('garage_number'), 50),
        import_key=('uat:' + source_id(row.get('source_id')))[:80], is_active=True,
    )
    machine.full_clean();machine.save()
    return machine


def upsert_departments(company, rows, now):
    seen = set();created = updated = 0
    for raw in rows:
        key = source_id(raw.get('source_id'))
        if not key:
            continue
        seen.add(key)
        row, was_created = UatDepartment.objects.get_or_create(company=company, source_id=key, defaults={'name': text(raw.get('name'), 300) or key})
        values = {'code': text(raw.get('code'), 80), 'name': text(raw.get('name'), 300) or row.name, 'is_active': True, 'last_seen_at': now}
        changed = [name for name, value in values.items() if getattr(row, name) != value]
        for name, value in values.items():setattr(row, name, value)
        if changed:row.save(update_fields=changed + ['updated_at'])
        created += was_created;updated += bool(changed and not was_created)
    if seen:
        UatDepartment.objects.filter(company=company).exclude(source_id__in=seen).update(is_active=False)
    return {'created': created, 'updated': updated, 'total': len(seen)}


def ensure_department(company, raw, cache, now):
    key = source_id(raw.get('department_ref'))
    if key and key in cache:
        return cache[key]
    name = text(raw.get('department'), 300)
    if key:
        row, _ = UatDepartment.objects.get_or_create(company=company, source_id=key, defaults={'name': name or key, 'last_seen_at': now})
        if name and row.name != name:
            row.name = name;row.last_seen_at = now;row.is_active = True;row.save(update_fields=['name', 'last_seen_at', 'is_active', 'updated_at'])
        cache[key] = row
        return row
    matches = list(UatDepartment.objects.filter(company=company, name=name, is_active=True)[:2]) if name else []
    return matches[0] if len(matches) == 1 else None


def upsert_vehicles(company, rows, now, *, complete=True):
    seen = set();created = matched = ambiguous = updated = 0
    indexes = equipment_indexes(company);kind = None
    for raw in rows:
        key = source_id(raw.get('source_id') or raw.get('vehicle_ref'))
        if not key:
            continue
        seen.add(key)
        row, was_created = UatVehicle.objects.get_or_create(company=company, source_id=key, defaults={'name': text(raw.get('name') or raw.get('vehicle'), 300) or key})
        source_values = {
            'name': text(raw.get('name') or raw.get('vehicle'), 300) or row.name,
            'plate': text(raw.get('plate'), 80), 'vin': text(raw.get('vin'), 120),
            'garage_number': text(raw.get('garage_number'), 80), 'is_active': True, 'last_seen_at': now,
        }
        if row.equipment_id:
            machine = row.equipment
            status = row.match_status if row.match_status in {'MATCHED', 'CREATED'} else 'MATCHED';basis = row.match_basis or 'SOURCE_ID'
        else:
            machine, status, basis = match_vehicle({**raw, **source_values}, indexes)
            if machine is None and status == 'UNMATCHED':
                kind = kind or unclassified_type(company)
                machine = create_vehicle(company, {**raw, **source_values, 'source_id': key}, kind)
                status, basis = 'CREATED', 'CREATED'
                for idx, value, is_plate in (('vin', machine.vin, False), ('plate', machine.plate_number, True), ('inventory', machine.inventory_number, False)):
                    token = normalized(value, plate=is_plate)
                    if token:indexes[idx][token].append(machine)
        values = {**source_values, 'equipment': machine, 'match_status': status, 'match_basis': basis}
        changed = [name for name, value in values.items() if getattr(row, name) != value]
        for name, value in values.items():setattr(row, name, value)
        if changed:row.save(update_fields=changed + ['updated_at'])
        if machine and status == 'CREATED':
            fill = {'name': source_values['name'][:150], 'brand_model': source_values['name'][:150], 'plate_number': source_values['plate'][:50], 'vin': source_values['vin'][:100], 'inventory_number': source_values['garage_number'][:50], 'is_active': True}
            changed_machine = [name for name, value in fill.items() if getattr(machine, name) != value]
            for name, value in fill.items():setattr(machine, name, value)
            if changed_machine:machine.full_clean();machine.save(update_fields=changed_machine + ['updated_at'])
        created += was_created;matched += status in {'MATCHED', 'CREATED'};ambiguous += status == 'AMBIGUOUS';updated += bool(changed and not was_created)
    if complete and seen:
        UatVehicle.objects.filter(company=company).exclude(source_id__in=seen).update(is_active=False)
    auto_match_vehicles_by_vin(company)
    return {'created': created, 'updated': updated, 'matched': matched, 'ambiguous': ambiguous, 'total': len(seen)}


def vehicle_for(company, raw, cache, now):
    key = source_id(raw.get('vehicle_ref'))
    if key in cache:return cache[key]
    if not key:return None
    upsert_vehicles(company, [{**raw, 'source_id': key}], now, complete=False)
    row = UatVehicle.objects.get(company=company, source_id=key);cache[key] = row
    return row


def clear_old_facts(waybills):
    tasks = UatWaybillTask.objects.filter(waybill__in=waybills)
    fact_ids = set(tasks.exclude(fact=None).values_list('fact_id', flat=True))
    tasks.update(fact=None)
    for fact in EquipmentFact.objects.filter(pk__in=fact_ids):
        if not fact.uat_tasks.exists() and fact.comment.startswith('[УАТ]'):
            fact.delete()


def fact_number(machine, source):
    return text(machine.plate_number or machine.inventory_number or machine.vin or ('UAT-' + source.source_id), 50)


def create_facts(company, waybills):
    groups = defaultdict(list);bill_states = defaultdict(list)
    for bill in waybills.select_related('vehicle__equipment', 'department__construction_object', 'department__camp').prefetch_related('tasks__department__construction_object', 'tasks__department__camp'):
        tasks = list(bill.tasks.all())
        if not bill.is_active or not bill.posted:
            bill.fact_status = 'NOT_POSTED';bill.fact_message = 'Документ не проведён в УАТ.';bill.save(update_fields=['fact_status', 'fact_message', 'updated_at']);continue
        if not tasks:
            bill.fact_status = 'EMPTY';bill.fact_message = 'В путевом листе нет заданий.';bill.save(update_fields=['fact_status', 'fact_message', 'updated_at']);continue
        machine = bill.vehicle.equipment if bill.vehicle_id and bill.vehicle.equipment_id else None
        for task in tasks:
            department = task.department or bill.department
            obj = department.construction_object if department and department.construction_object_id else None
            camp = department.camp if department and department.camp_id else None
            if not machine or not obj or not camp:
                reasons = []
                if not machine:reasons.append('не сопоставлена техника')
                if not obj:reasons.append('не сопоставлено подразделение УАТ с объектом')
                if not camp:reasons.append('не сопоставлено подразделение УАТ с местонахождением')
                bill_states[bill.pk].extend(('PENDING', reason) for reason in reasons);continue
            groups[(bill.document_date, machine.pk, obj.pk)].append((bill, task, machine, obj, department))
    for (_day, _machine, _obj), rows in groups.items():
        bill, _task, machine, obj, _department = rows[0]
        equipment_type = machine.type.parent if machine.type.parent_id else machine.type
        number = fact_number(machine, bill)
        lookup = dict(company=company, construction_object=obj, date=bill.document_date, equipment_type=equipment_type, equipment_number=number)
        existing = EquipmentFact.objects.filter(**lookup).first()
        if existing:
            for source, *_rest in rows:bill_states[source.pk].append(('CONFLICT', 'за эту дату уже есть ручной факт'))
            continue
        hours = sum((task.hours or Decimal('0')) for _, task, *_ in rows).quantize(
            Decimal('0.01'), rounding=ROUND_HALF_UP,
        )
        bills = sorted({source.number or source.source_id for source, *_ in rows})
        drivers = sorted({name for source, *_ in rows for name in (source.driver_1_name, source.driver_2_name) if name})
        departments = sorted({department.name for *_, department in rows if department})
        camps = sorted({department.camp.name for *_, department in rows if department and department.camp_id})
        comment = '[УАТ] Путевые листы: ' + ', '.join(bills)
        if drivers:comment += '. Водители: ' + ', '.join(drivers)
        if departments:comment += '. Подразделения УАТ: ' + ', '.join(departments)
        if camps:comment += '. Местонахождения: ' + ', '.join(camps)
        fact = EquipmentFact(**lookup, planned_count=0, actual_count=1, machine_hours=hours or None, comment=comment)
        fact.full_clean();fact.save()
        for source, task, *_rest in rows:
            task.fact = fact;task.save(update_fields=['fact', 'updated_at']);bill_states[source.pk].append(('CREATED', ''))
    for bill in waybills:
        states = bill_states.get(bill.pk, [])
        if not states:continue
        status = 'CONFLICT' if any(item[0] == 'CONFLICT' for item in states) else 'PENDING' if any(item[0] == 'PENDING' for item in states) else 'CREATED'
        messages = sorted({item[1] for item in states if item[1]})
        bill.fact_status = status;bill.fact_message = '; '.join(messages)[:500]
        bill.save(update_fields=['fact_status', 'fact_message', 'updated_at'])


@transaction.atomic
def rebuild_facts(company, from_day=None):
    Company.objects.select_for_update().get(pk=company.pk)
    waybills = UatWaybill.objects.filter(company=company)
    if from_day:waybills = waybills.filter(document_date__gte=from_day)
    clear_old_facts(waybills)
    create_facts(company, waybills)


@transaction.atomic
def apply_uat_report(run, report):
    """Persist a snapshot and rebuild only UAT-owned facts for its date range."""
    company = run.connection.company;now = timezone.now()
    Company.objects.select_for_update().get(pk=company.pk)
    department_result = upsert_departments(company, report.get('departments', []), now)
    department_cache = {row.source_id: row for row in UatDepartment.objects.filter(company=company)}
    vehicle_rows = list(report.get('vehicles', []))
    known_vehicle_ids = {source_id(row.get('source_id')) for row in vehicle_rows}
    for raw in [*report.get('waybills', []), *report.get('repair_sheets', [])]:
        key = source_id(raw.get('vehicle_ref'))
        if key and key not in known_vehicle_ids:
            vehicle_rows.append({**raw, 'source_id': key, 'name': raw.get('vehicle')});known_vehicle_ids.add(key)
    vehicle_result = upsert_vehicles(company, vehicle_rows, now)
    vehicle_cache = {row.source_id: row for row in UatVehicle.objects.filter(company=company)}
    drivers = employee_index(company)
    from_day = parsed_date(report.get('from_date')) or timezone.localdate()
    seen_bills = set();bill_objects = []
    for raw in report.get('waybills', []):
        key = source_id(raw.get('source_id'));day = parsed_date(raw.get('document_date'))
        if not key or not day:continue
        seen_bills.add(key);vehicle = vehicle_for(company, raw, vehicle_cache, now);department = ensure_department(company, raw, department_cache, now)
        driver_1, status_1 = match_driver(drivers, raw.get('driver_1_number'), raw.get('driver_1'))
        driver_2, status_2 = match_driver(drivers, raw.get('driver_2_number'), raw.get('driver_2'))
        defaults = {
            'number': text(raw.get('number'), 80), 'document_date': day, 'posted': bool(raw.get('posted')), 'is_active': True,
            'vehicle': vehicle, 'vehicle_name': text(raw.get('vehicle'), 300), 'plate': text(raw.get('plate'), 80),
            'driver_1_ref': source_id(raw.get('driver_1_ref')), 'driver_1_number': text(raw.get('driver_1_number'), 80), 'driver_1_name': text(raw.get('driver_1'), 300), 'driver_1': driver_1, 'driver_1_status': status_1,
            'driver_2_ref': source_id(raw.get('driver_2_ref')), 'driver_2_number': text(raw.get('driver_2_number'), 80), 'driver_2_name': text(raw.get('driver_2'), 300), 'driver_2': driver_2, 'driver_2_status': status_2,
            'departure': parsed_datetime(raw.get('departure')), 'returned': parsed_datetime(raw.get('returned')), 'department': department, 'department_name': text(raw.get('department'), 300),
            'project_name': text(raw.get('object_grandparent'), 300), 'site_name': text(raw.get('object_parent'), 300), 'object_name': text(raw.get('construction_object'), 300), 'object_code': text(raw.get('construction_object_code'), 80),
            'organisation': text(raw.get('organisation'), 300), 'odometer_departure': decimal(raw.get('odometer_departure')), 'odometer_return': decimal(raw.get('odometer_return')), 'comment': text(raw.get('comment')), 'payload': raw, 'last_seen_at': now,
        }
        bill, _ = UatWaybill.objects.update_or_create(company=company, source_id=key, defaults=defaults);bill_objects.append(bill)
    if not report.get('limited'):
        missing = UatWaybill.objects.filter(company=company, document_date__gte=from_day).exclude(source_id__in=seen_bills)
        missing.update(is_active=False, posted=False, fact_status='NOT_POSTED', fact_message='Документ удалён или больше не входит в снимок УАТ.')
    period_bills = UatWaybill.objects.filter(company=company, document_date__gte=from_day)
    clear_old_facts(period_bills)
    tasks_by_bill = defaultdict(list)
    for raw in report.get('waybill_tasks', []):tasks_by_bill[source_id(raw.get('parent_id'))].append(raw)
    for bill in bill_objects:
        lines = set()
        for raw in tasks_by_bill.get(bill.source_id, []):
            try:line = int(raw.get('line_number'))
            except (TypeError, ValueError):continue
            lines.add(line);department = ensure_department(company, raw, department_cache, now)
            UatWaybillTask.objects.update_or_create(company=company, waybill=bill, line_number=line, defaults={
                'department': department, 'department_name': text(raw.get('department'), 300), 'project_name': text(raw.get('object_grandparent'), 300),
                'site_name': text(raw.get('object_parent'), 300), 'object_name': text(raw.get('construction_object'), 300), 'object_code': text(raw.get('construction_object_code'), 80),
                'departure_address': text(raw.get('departure_address'), 500), 'arrival_address': text(raw.get('arrival_address'), 500), 'completed': bool(raw.get('completed')),
                'trips': decimal(raw.get('trips')), 'loaded_km': decimal(raw.get('loaded_km')), 'empty_km': decimal(raw.get('empty_km')), 'tonnes': decimal(raw.get('tonnes')), 'hours': decimal(raw.get('hours')),
            })
        bill.tasks.exclude(line_number__in=lines).delete()
    seen_repairs = set();works = defaultdict(list);materials = defaultdict(list);executors = defaultdict(list)
    for target, source in ((works, 'repair_works'), (materials, 'repair_materials'), (executors, 'repair_executors')):
        for row in report.get(source, []):target[source_id(row.get('parent_id'))].append(row)
    for raw in report.get('repair_sheets', []):
        key = source_id(raw.get('source_id'));day = parsed_date(raw.get('document_date'))
        if not key or not day:continue
        seen_repairs.add(key);vehicle = vehicle_for(company, raw, vehicle_cache, now);department = ensure_department(company, raw, department_cache, now)
        UatRepairSheet.objects.update_or_create(company=company, source_id=key, defaults={
            'number': text(raw.get('number'), 80), 'document_date': day, 'posted': bool(raw.get('posted')), 'completed': bool(raw.get('completed')), 'is_active': True,
            'vehicle': vehicle, 'vehicle_name': text(raw.get('vehicle'), 300), 'plate': text(raw.get('plate'), 80), 'service_kind': text(raw.get('service_kind'), 300),
            'started': parsed_datetime(raw.get('started')), 'finished': parsed_datetime(raw.get('finished')), 'master': text(raw.get('master'), 300), 'organisation': text(raw.get('organisation'), 300),
            'department': department, 'department_name': text(raw.get('department'), 300), 'repair_place': text(raw.get('repair_place'), 300), 'reason': text(raw.get('reason')), 'comment': text(raw.get('comment')),
            'works': works[key], 'materials': materials[key], 'executors': executors[key], 'payload': raw, 'last_seen_at': now,
        })
    if not report.get('limited'):
        UatRepairSheet.objects.filter(company=company, document_date__gte=from_day).exclude(source_id__in=seen_repairs).update(is_active=False, posted=False)
    create_facts(company, period_bills)
    result = {
        'departments': department_result, 'vehicles': vehicle_result,
        'waybills': len(seen_bills), 'repair_sheets': len(seen_repairs),
        'facts_created': UatWaybillTask.objects.filter(waybill__company=company, waybill__document_date__gte=from_day, fact__isnull=False).values('fact_id').distinct().count(),
        'pending': UatWaybill.objects.filter(company=company, document_date__gte=from_day, fact_status__in=['PENDING', 'CONFLICT']).count(),
    }
    return result


def source_ready(connection):
    from .uat_sources import status
    database = settings.UAT_DATABASE
    run = connection.runs.filter(database=database, status='DONE', report__uat_discovery=True).order_by('-created_at', '-pk').first()
    return bool(run and status(run.report)['ready'])


def scheduled_slot(connection, now=None):
    now = now or timezone.now();local = timezone.localtime(now, timezone.get_default_timezone())
    slot = timezone.make_aware(datetime.combine(local.date(), connection.uat_sync_time), timezone.get_default_timezone())
    if slot > now:slot -= timedelta(days=1)
    return slot


def next_scheduled_slot(connection, now=None):
    now = now or timezone.now();slot = scheduled_slot(connection, now)
    if slot <= now:slot += timedelta(days=1)
    return slot


def enqueue_due_uat(now=None):
    from .one_c import enqueue
    now = now or timezone.now()
    for connection in OneCConnection.objects.filter(uat_sync_enabled=True, active_until__isnull=True):
        if not source_ready(connection):continue
        slot = scheduled_slot(connection, now)
        previous = connection.runs.filter(database=settings.UAT_DATABASE, report__uat_import=True, created_at__gte=slot).first()
        if previous:continue
        enqueue(
            connection, database=settings.UAT_DATABASE,
            uat_import_from=timezone.localdate(now)-timedelta(days=connection.uat_sync_lookback_days),
            trust_certificate=True,
        )
