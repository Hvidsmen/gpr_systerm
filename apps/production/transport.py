"""Transactional transport journal. A camp is a physical location, not a project."""

from core.fact_history import with_history_actor
from core.fact_edit_window import require_fact_day
from datetime import timedelta
from uuid import UUID
from django.core import signing
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction, connection
from django.db.models import F, Q
from django.utils import timezone
from core.permissions import READ_ROLES, require_roles, role_code, scope_queryset
from apps.accounts.models import Company
from apps.personnel.models import Camp
from apps.projects.models import ConstructionObject
from apps.resources.models import Equipment
from .transport_models import EquipmentCampStay, EquipmentTransportEvent, EquipmentDayRecord, EquipmentTransportControl, CONDITIONS

OPERATORS = {'ADMIN', 'MECHANIZATION_DISPATCHER', 'ALL_FACT_DISPATCHER'}
READERS = READ_ROLES | OPERATORS
SALT = 'transport-equipment-camp-v1'
TTL = timedelta(minutes=5)


def camps_for(user):
    camps = Camp.objects.filter(company=user.company)
    if role_code(user) in {'ALL_FACT_DISPATCHER', 'PLANNER', 'FUEL_WAREHOUSE'}:
        camps = camps.filter(object_links__construction_object__in=user.assigned_objects.filter(company=user.company))
    return camps.distinct().order_by('name', 'pk')


def authorize(user, camp, write=True):
    require_roles(user, OPERATORS if write else READERS)
    if not camps_for(user).filter(pk=camp.pk).exists():
        raise PermissionDenied('ВЖГ не связан с доступными вам объектами.')


def present(company, day):
    return EquipmentCampStay.objects.filter(company=company, cancelled_at=None, arrived_on__lte=day).filter(Q(departed_on=None) | Q(departed_on__gt=day))


def condition_on(machine, day):
    record = machine.transport_events.filter(kind='CONDITION', cancelled_at=None, date__lte=day).order_by('-date', '-pk').first()
    if record:
        return record.condition
    legacy = machine.state_history.filter(company=machine.company, is_cancelled=False, assignment__cancelled_at=None, effective_date__lte=day).order_by('-effective_date', '-shift', '-pk').first()
    return ('REPAIR' if legacy.state == 'REPAIR' else 'READY') if legacy else ''



def conditions_for(company, ids, days):
    """Two history queries for a roster or a whole monthly calendar."""
    ids=list(ids);days=list(days)
    if not ids or not days:return {}
    limit=max(days);new={pk:[] for pk in ids};old={pk:[] for pk in ids}
    for machine,when,state in EquipmentTransportEvent.objects.filter(company=company,equipment_id__in=ids,kind='CONDITION',cancelled_at=None,date__lte=limit).order_by('date','pk').values_list('equipment_id','date','condition'):
        new[machine].append((when,state))
    from .models import EquipmentStateChange
    for machine,when,state in EquipmentStateChange.objects.filter(company=company,equipment_id__in=ids,is_cancelled=False,assignment__cancelled_at=None,effective_date__lte=limit).order_by('effective_date','shift','pk').values_list('equipment_id','effective_date','state'):
        old[machine].append((when,'REPAIR' if state=='REPAIR' else 'READY'))
    result={}
    for pk in ids:
        for day in days:
            recent=[state for when,state in new[pk] if when<=day]
            fallback=[state for when,state in old[pk] if when<=day]
            result[pk,day]=(recent or fallback or [''])[-1]
    return result

def written_off(machine, day=None):
    qs = machine.transport_events.filter(kind='WRITEOFF', cancelled_at=None)
    return qs.filter(date__lte=day).exists() if day else qs.exists()


def snapshot(user, camp, day):
    versions = {str(pk): 0 for pk in Equipment.objects.filter(company=user.company).values_list('pk', flat=True)}
    versions.update({str(pk): revision for pk, revision in EquipmentTransportControl.objects.filter(company=user.company).values_list('equipment_id', 'revision')})
    return signing.dumps({'company': user.company_id, 'user': user.pk, 'camp': camp.pk, 'date': day.isoformat(), 'versions': versions}, salt=SALT, compress=True)


def _payload(user, camp, day, signed):
    try:
        values = signing.loads(signed, salt=SALT, max_age=12 * 3600)
    except (signing.BadSignature, TypeError):
        raise ValidationError('Форма устарела. Обновите страницу; сохранённые данные останутся на месте.')
    if (values.get('company'), values.get('user'), values.get('camp'), values.get('date')) != (user.company_id, user.pk, camp.pk, day.isoformat()):
        raise ValidationError('Форма другого пользователя, ВЖГ или даты. Откройте нужный день заново.')
    return values['versions']


def _session(value):
    try:
        return UUID(str(value)) if value else None
    except (ValueError, TypeError):
        raise ValidationError('Неверный сеанс редактирования. Обновите страницу.')


def _company_mutex(user):
    # SQLite has no row-level SELECT FOR UPDATE. Take its write lock before
    # reading revisions. PostgreSQL uses the object/equipment locks below,
    # allowing independent machines to be saved concurrently.
    if connection.vendor == 'sqlite':
        Company.objects.filter(pk=user.company_id).update(name=F('name'))


def _lock(user, ids):
    ids = sorted(set(ids))
    if not ids or len(ids) > 100:
        raise ValidationError('Выберите от 1 до 100 машин.')
    _company_mutex(user)
    machines = list(Equipment.objects.select_for_update().filter(company=user.company, pk__in=ids).select_related('type__parent').order_by('pk'))
    if len(machines) != len(ids):
        raise ValidationError('Выберите технику своей компании.')
    controls = {}
    for machine in machines:
        control, _ = EquipmentTransportControl.objects.get_or_create(company=user.company, equipment=machine)
        controls[machine.pk] = EquipmentTransportControl.objects.select_for_update().get(pk=control.pk)
    return machines, controls


def _check(user, machine, control, versions, session):
    if versions.get(str(machine.pk)) != control.revision:
        raise ValidationError(f'{machine}: данные уже изменил другой диспетчер. Обновите страницу.')
    if control.expires_at and control.expires_at > timezone.now() and (control.locked_by_id != user.pk or control.session_key != session):
        owner = control.locked_by.get_full_name() or control.locked_by.username if control.locked_by else 'другой диспетчер'
        raise ValidationError(f'{machine}: сейчас редактирует {owner}.')


def _bump(control):
    control.revision += 1
    control.locked_by = None; control.session_key = None; control.expires_at = None
    control.save()


def audit(user, record, action, values):
    from apps.audit.models import AuditLog
    AuditLog.objects.create(user=user, action=action, entity_type=record._meta.label, entity_id=str(record.pk), new_value=values, comment='Учёт техники по ВЖГ')


def _valid_timeline(machine, since):
    """Retroactive changes must not invalidate saved daily facts."""
    for row in machine.transport_days.filter(date__gte=since).select_related('camp'):
        if row.presence_required and not present(machine.company, row.date).filter(equipment=machine, camp=row.camp).exists():
            raise ValidationError(f'{machine}: за {row.date:%d.%m.%Y} уже есть дневная запись в {row.camp}. Сначала исправьте её.')
        if written_off(machine, row.date):
            raise ValidationError(f'{machine}: за {row.date:%d.%m.%Y} уже есть дневная запись. Сначала исправьте её перед списанием.')


@transaction.atomic
@with_history_actor
def move(user, camp, day, ids, kind, *, target=None, condition='READY', comment='', signed, session=None):
    authorize(user, camp); require_fact_day(user,day)
    if kind not in {'ARRIVAL', 'DEPARTURE', 'TRANSFER', 'WRITEOFF'}:
        raise ValidationError('Выберите действие с техникой.')
    if kind == 'TRANSFER':
        if not target or target.pk == camp.pk:
            raise ValidationError('Выберите другой ВЖГ для перевода.')
        authorize(user, target)
    versions = _payload(user, camp, day, signed); session = _session(session)
    machines, controls = _lock(user, ids)
    locked_camps = {c.pk: c for c in Camp.objects.select_for_update().filter(company=user.company, pk__in=[camp.pk] + ([target.pk] if target else [])).order_by('pk')}
    camp = locked_camps[camp.pk]; target = locked_camps[target.pk] if target else None
    if (kind == 'ARRIVAL' and not camp.is_active) or (target and not target.is_active):
        raise ValidationError('Для приезда выберите действующий ВЖГ.')
    for machine in machines:
        require_fact_day(user,day)
        _check(user, machine, controls[machine.pk], versions, session)
        if written_off(machine):
            raise ValidationError(f'{machine}: уже списана. Для исправления отмените ошибочное списание в истории.')
        if machine.transport_events.filter(cancelled_at=None, date__gt=day).exclude(kind='CONDITION').exists():
            raise ValidationError(f'{machine}: есть более поздние перемещения. Сначала исправьте их в истории.')
        current = present(user.company, day).filter(equipment=machine).first()
        previous = resulting = None
        if kind == 'ARRIVAL':
            if not machine.is_active:
                raise ValidationError(f'{machine}: отключена в справочнике.')
            if current:
                raise ValidationError(f'{machine}: уже числится в ВЖГ. Используйте перевод.')
            resulting = EquipmentCampStay(company=user.company, equipment=machine, camp=camp, arrived_on=day)
            resulting.full_clean(); resulting.save()
        else:
            if not current or current.camp_id != camp.pk:
                raise ValidationError(f'{machine}: на эту дату не находится в выбранном ВЖГ.')
            previous = current; current.departed_on = day; current.full_clean(); current.save()
            if kind == 'TRANSFER':
                resulting = EquipmentCampStay(company=user.company, equipment=machine, camp=target, arrived_on=day)
                resulting.full_clean(); resulting.save()
        if kind == 'WRITEOFF':
            from .models import EquipmentActivity,EmployeeActivity,EquipmentAssignment
            if EquipmentActivity.objects.filter(equipment=machine,date__gte=day).exists() or EmployeeActivity.objects.filter(equipment=machine,date__gte=day).exists():
                raise ValidationError(f'{machine}: есть факт техники или экипажа начиная с даты списания. Сначала исправьте его.')
            if EquipmentAssignment.objects.filter(equipment=machine,cancelled_at=None).filter(Q(end_date=None)|Q(end_date__gte=day)).exists():
                raise ValidationError(f'{machine}: сначала завершите действующее назначение техники на объект.')
        event = EquipmentTransportEvent(company=user.company, equipment=machine, date=day, kind=kind, previous_stay=previous, resulting_stay=resulting,
            from_camp=camp if kind != 'ARRIVAL' else None, to_camp=target if kind == 'TRANSFER' else camp if kind == 'ARRIVAL' else None,
            comment=comment.strip(), recorded_by=user, previous_active=machine.is_active if kind == 'WRITEOFF' else None)
        event.full_clean(); event.save()
        if kind == 'WRITEOFF':
            machine.is_active = False; machine.save(update_fields=['is_active', 'updated_at'])
        if kind == 'ARRIVAL' and not condition_on(machine, day):
            if condition not in dict(CONDITIONS):
                raise ValidationError('Укажите состояние приехавшей машины.')
            EquipmentTransportEvent.objects.create(company=user.company, equipment=machine, date=day, kind='CONDITION', condition=condition, to_camp=camp, recorded_by=user, comment='Состояние при первом приезде')
        _valid_timeline(machine,day)
        _bump(controls[machine.pk]); audit(user, event, 'CREATE', {'kind': kind, 'date': str(day), 'equipment': machine.pk})
    require_fact_day(user,day)
    return len(machines)


@transaction.atomic
@with_history_actor
def set_condition(user, camp, day, ids, condition, *, comment='', signed, session=None):
    authorize(user, camp); require_fact_day(user,day)
    if condition not in dict(CONDITIONS):
        raise ValidationError('Выберите техническое состояние.')
    versions = _payload(user, camp, day, signed); session = _session(session)
    machines, controls = _lock(user, ids)
    for machine in machines:
        require_fact_day(user,day)
        _check(user, machine, controls[machine.pk], versions, session)
        if not present(user.company, day).filter(camp=camp, equipment=machine).exists():
            raise ValidationError(f'{machine}: нет в выбранном ВЖГ на эту дату.')
        event = EquipmentTransportEvent.objects.create(company=user.company, equipment=machine, date=day, kind='CONDITION', condition=condition, to_camp=camp, recorded_by=user, comment=comment.strip())
        _valid_timeline(machine,day)
        _bump(controls[machine.pk]); audit(user, event, 'CREATE', {'condition': condition, 'date': str(day)})
    require_fact_day(user,day)
    return len(machines)


def _project(user, record, previous_activity):
    """Reuse existing actual totals for the optional project attribution."""
    from .models import EquipmentActivity, FactSectionDay
    from .dispatch import project_resources
    affected = set()
    if previous_activity:
        affected.add(previous_activity.construction_object_id)
    others = EquipmentActivity.objects.filter(company=user.company, equipment=record.equipment, date=record.date).exclude(pk=previous_activity.pk if previous_activity else None)
    if others.exists():
        raise ValidationError('За этот день техника уже внесена в прежней форме факта. Исправьте прежнюю запись перед вводом по ВЖГ.')
    if not record.construction_object_id:
        if previous_activity:
            record.activity = None; record.save(update_fields=['activity', 'updated_at']); previous_activity.delete()
    else:
        obj = record.construction_object
        affected.add(obj.pk)
        row = previous_activity or EquipmentActivity(company=user.company, equipment=record.equipment, date=record.date, shift=1)
        row.construction_object = obj; row.state = 'WORKING' if record.status == 'WORKED' else 'IDLE'
        row.work = record.work; row.work_item = record.work_item; row.general = not record.work_id
        row.activity = record.comment or ('Работала без привязки к работе · учёт ВЖГ' if record.status == 'WORKED' else 'Простой · учёт ВЖГ')
        row._transport_day_id=record.pk
        row.full_clean(); row.save(); record.activity = row; record.save(update_fields=['activity', 'updated_at'])
    for obj in ConstructionObject.objects.filter(company=user.company, pk__in=affected).order_by('pk'):
        FactSectionDay.objects.get_or_create(company=user.company, construction_object=obj, date=record.date, section='mechanization')
        FactSectionDay.objects.filter(company=user.company, construction_object=obj, date=record.date, section='mechanization').update(revision=F('revision') + 1, confirmed_at=None, confirmed_by=None)
        project_resources(obj, record.date, 'mechanization')


@transaction.atomic
@with_history_actor
def save_day(user, camp, day, ids, status, *, construction_object=None, work=None, work_item=None, comment='', repeat=False, signed, session=None, planning_type=None, allow_external=False):
    authorize(user, camp); require_fact_day(user,day)
    if status not in {'WORKED', 'IDLE', 'CLEAR'}:
        raise ValidationError('Выберите «Работала», «Простой» или очистку дня.')
    versions = _payload(user, camp, day, signed); session = _session(session)
    # Project locks precede equipment locks, matching the old actual-entry writer.
    project_ids = set(EquipmentDayRecord.objects.filter(company=user.company, equipment_id__in=ids, date=day).exclude(construction_object=None).values_list('construction_object_id', flat=True))
    if construction_object: project_ids.add(construction_object.pk)
    if repeat:
        project_ids.update(EquipmentDayRecord.objects.filter(company=user.company, equipment_id__in=ids, date__lt=day).exclude(construction_object=None).values_list('construction_object_id', flat=True))
    _company_mutex(user)
    list(ConstructionObject.objects.select_for_update().filter(company=user.company, pk__in=project_ids).order_by('pk'))
    machines, controls = _lock(user, ids)
    Camp.objects.select_for_update().get(pk=camp.pk, company=user.company)
    for machine in machines:
        require_fact_day(user,day)
        _check(user, machine, controls[machine.pk], versions, session)
        record = EquipmentDayRecord.objects.filter(company=user.company, equipment=machine, date=day).first()
        inside=present(user.company, day).filter(equipment=machine, camp=camp).exists()
        if not inside and not allow_external and not (record and not record.presence_required):
            raise ValidationError(f'{machine}: нет в выбранном ВЖГ на эту дату.')
        if not record and not machine.is_active:raise ValidationError(f'{machine}: отключена в справочнике.')
        if record and record.camp_id != camp.pk:
            raise ValidationError('Сохранённая запись относится к другому ВЖГ.')
        old_activity = record.activity if record else None
        if status == 'CLEAR':
            if record:
                audit(user, record, 'DELETE', {'date': str(day), 'status': record.status, 'comment': record.comment, 'object': record.construction_object_id, 'work': record.work_id, 'item': record.work_item_id})
                # Remove its projection through the same service and invalidate totals.
                record.construction_object = None; _project(user, record, old_activity); record.delete()
            _bump(controls[machine.pk]); continue
        if written_off(machine, day):
            raise ValidationError(f'{machine}: техника списана на выбранную дату.')
        obj, selected_work, selected_item = construction_object, work, work_item
        if repeat:
            previous = machine.transport_days.filter(date__lt=day, status='WORKED').select_related('construction_object', 'work', 'work_item').order_by('-date').first()
            if previous: obj, selected_work, selected_item = previous.construction_object, previous.work, previous.work_item
        if obj:
            retained = record and record.construction_object_id == obj.pk
            if not scope_queryset(ConstructionObject.objects.all(), user).filter(pk=obj.pk).exists():
                raise ValidationError('Объект не доступен этому диспетчеру.')
            if not retained and not camp.object_links.filter(company=user.company, construction_object=obj).exists():
                raise ValidationError('Объект не связан с выбранным ВЖГ.')
        existed = record is not None
        record = record or EquipmentDayRecord(company=user.company, equipment=machine, camp=camp, date=day)
        if not existed:record.presence_required=inside
        if planning_type is not None:record.planning_type=planning_type
        record.status = status; record.construction_object = obj
        record.work = selected_work if status == 'WORKED' else None; record.work_item = selected_item if status == 'WORKED' else None
        record.comment = comment.strip()
        if not existed and not record.recorded_by_id: record.recorded_by = user
        record.full_clean(); record.save(); _project(user, record, old_activity)
        _bump(controls[machine.pk]); audit(user, record, 'UPDATE' if existed else 'CREATE', {'date': str(day), 'status': status, 'camp': camp.pk, 'object': obj.pk if obj else None, 'work': record.work_id, 'item': record.work_item_id})
    require_fact_day(user,day)
    return len(machines)


@transaction.atomic
@with_history_actor
def cancel_event(user, event, reason, *, signed, session=None):
    camp = event.to_camp or event.from_camp
    authorize(user, camp)
    if event.from_camp: authorize(user, event.from_camp)
    if not reason.strip(): raise ValidationError('Укажите причину отмены ошибочной записи.')
    versions = _payload(user, camp, event.date, signed)
    machines, controls = _lock(user, [event.equipment_id]); machine = machines[0]; control = controls[machine.pk]
    _check(user, machine, control, versions, _session(session))
    event = EquipmentTransportEvent.objects.select_for_update().get(pk=event.pk, company=user.company)
    require_fact_day(user,event.date)
    if event.cancelled_at: raise ValidationError('Запись уже отменена.')
    later = machine.transport_events.filter(cancelled_at=None).filter(Q(date__gt=event.date) | Q(date=event.date, pk__gt=event.pk))
    if event.kind != 'CONDITION' and later.exclude(kind='CONDITION').exists():
        raise ValidationError('Есть более поздние перемещения. Сначала исправьте их.')
    if event.resulting_stay_id:
        stay = event.resulting_stay; stay.cancelled_at = timezone.now(); stay.save()
    if event.previous_stay_id:
        stay = event.previous_stay; stay.departed_on = None; stay.full_clean(); stay.save()
    if event.kind == 'WRITEOFF':
        machine.is_active = event.previous_active; machine.save(update_fields=['is_active', 'updated_at'])
    event.cancelled_at = timezone.now(); event.cancelled_by = user; event.cancellation_reason = reason.strip(); event.save()
    _valid_timeline(machine,event.date)
    _bump(control); audit(user, event, 'UPDATE', {'cancelled': True, 'reason': reason.strip()})
    require_fact_day(user,event.date)


@transaction.atomic
def claim(user, camp, day, ids, action, session, *, purpose='movement'):
    authorize(user, camp); session = _session(session)
    if purpose not in {'movement','day'}:raise ValidationError('Неизвестная цель редактирования.')
    if not session: raise ValidationError('Не указан сеанс редактирования.')
    if action not in {'acquire', 'renew', 'release', 'status'}: raise ValidationError('Неизвестное действие.')
    ids = sorted(set(ids))
    if not ids or len(ids) > 100: raise ValidationError('Выберите от 1 до 100 машин.')
    if action in {'acquire','renew'}:require_fact_day(user,day)
    if action == 'status':
        controls = EquipmentTransportControl.objects.filter(company=user.company, equipment_id__in=ids).select_related('locked_by')
    else:
        machines, by_id = _lock(user, ids); controls = list(by_id.values())
        for control in controls:
            if action in {'acquire','renew'}:require_fact_day(user,day)
            active = control.expires_at and control.expires_at > timezone.now()
            mine = control.locked_by_id == user.pk and control.session_key == session
            if action in {'acquire', 'renew'} and active and not mine:
                raise ValidationError(f'{control.equipment}: сейчас редактирует {control.locked_by.get_full_name() or control.locked_by.username}.')
            if action in {'acquire', 'renew'} and (mine or not active):
                # Only present machines, or an unplaced machine being received, can be reserved here.
                stays = present(user.company, day).filter(equipment_id=control.equipment_id)
                if purpose!='day' and stays.exists() and not stays.filter(camp=camp).exists(): raise ValidationError('Техника находится в другом ВЖГ.')
                control.locked_by = user; control.session_key = session; control.expires_at = timezone.now() + TTL; control.save()
            elif action == 'release' and mine:
                control.locked_by = None; control.session_key = None; control.expires_at = None; control.save()
    return {str(c.equipment_id): {'owner': c.locked_by.get_full_name() or c.locked_by.username, 'mine': c.locked_by_id == user.pk and c.session_key == session} for c in controls if c.locked_by_id and c.expires_at and c.expires_at > timezone.now()}
