"""Versioned accommodation writes and expiring locks on people and places."""
from datetime import timedelta
from uuid import UUID
from django.core import signing
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q,F
from django.utils import timezone
from core.permissions import require_roles,READ_ROLES
from core.fact_edit_window import require_fact_day
from core.fact_history import with_history_actor
from apps.accounts.models import Company
from apps.resources.models import Employee
from .models import Camp,LodgingBuilding,LodgingBed,LodgingStay,LodgingEvent,LodgingControl

OPERATORS={'ADMIN','COMMANDANT','ALL_FACT_DISPATCHER'}
READERS=OPERATORS|READ_ROLES|{'HR_HEAD','PERSONNEL_DISPATCHER'}
SALT='lodging-edit-v1'
TTL=timedelta(minutes=5)


def present(company,day):
    return LodgingStay.objects.filter(company=company,cancelled_at=None,arrived_on__lte=day).filter(Q(departed_on=None)|Q(departed_on__gt=day)).exclude(departed_on=F('arrived_on'))


def available_beds(company,camp,day,*,user=None,session=None):
    occupied=present(company,day).filter(camp=camp).values('bed_id')
    leases=LodgingControl.objects.filter(company=company,resource='bed',expires_at__gt=timezone.now())
    if user and session:leases=leases.exclude(locked_by=user,session=session)
    return LodgingBed.objects.filter(company=company,building__camp=camp,building__camp__is_active=True,building__is_active=True,state='AVAILABLE').exclude(pk__in=occupied).exclude(pk__in=leases.values('resource_id')).select_related('building')


def session_id(value):
    try:return UUID(str(value))
    except (ValueError,TypeError,AttributeError):raise ValidationError('Обновите страницу: неверный сеанс оформления.')


def mutex(user):
    # All accommodation and personnel movement services take this same lock first.
    Company.objects.filter(pk=user.company_id).update(name=F('name'))


def resources(user,employee,bed,day):
    if employee.company_id!=user.company_id or bed and bed.company_id!=user.company_id:raise ValidationError('Выберите сотрудника и место своей компании.')
    keys={('employee',employee.pk)}
    if bed:keys.add(('bed',bed.pk))
    current=present(user.company,day).filter(employee=employee).first()
    if current:keys.add(('bed',current.bed_id))
    return sorted(keys)


def controls(user,keys):
    result=[]
    for resource,pk in keys:
        row,_=LodgingControl.objects.get_or_create(company=user.company,resource=resource,resource_id=pk)
        result.append(LodgingControl.objects.select_for_update().select_related('locked_by').get(pk=row.pk))
    return result


def version(rows):return {f'{row.resource}:{row.resource_id}':row.revision for row in rows}


def locked_elsewhere(row,user,session):
    if row.expires_at and row.expires_at>timezone.now() and (row.locked_by_id!=user.pk or row.session!=session):
        owner=row.locked_by.get_full_name() or row.locked_by.username if row.locked_by else 'другой комендант'
        raise ValidationError(f'{"Сотрудника" if row.resource=="employee" else "Место"} сейчас оформляет {owner}.')


def bed_versions(user,keys):return {str(b.pk):b.updated_at.isoformat() for b in LodgingBed.objects.filter(company=user.company,pk__in=[pk for kind,pk in keys if kind=='bed'])}


@transaction.atomic
def claim(user,camp,day,employee,bed,session,action='acquire'):
    require_roles(user,OPERATORS);require_fact_day(user,day);session=session_id(session)
    if camp.company_id!=user.company_id or bed and bed.building.camp_id!=camp.pk:raise ValidationError('Место другого ВЖГ.')
    mutex(user);keys=resources(user,employee,bed,day);rows=controls(user,keys)
    if action in {'acquire','renew'}:
        for row in rows:locked_elsewhere(row,user,session)
        if action=='renew' and any(row.locked_by_id!=user.pk or row.session!=session or not row.expires_at or row.expires_at<=timezone.now() for row in rows):raise ValidationError('Срок оформления истёк. Проверьте свежие данные и начните оформление заново.')
        if bed:
            if bed.state!='AVAILABLE' or not bed.building.is_active:raise ValidationError('Место недоступно для заселения.')
            occupant=present(user.company,day).filter(bed=bed).exclude(employee=employee).first()
            if occupant:raise ValidationError('Место уже занято другим сотрудником.')
        # Selection changes release only this browser tab's previous leases.
        for old in LodgingControl.objects.select_for_update().filter(company=user.company,locked_by=user,session=session):
            if (old.resource,old.resource_id) not in keys:old.locked_by=None;old.session=None;old.expires_at=None;old.save()
        for row in rows:row.locked_by=user;row.session=session;row.expires_at=timezone.now()+TTL;row.save()
    elif action=='release':
        for row in LodgingControl.objects.select_for_update().filter(company=user.company,locked_by=user,session=session):
            row.locked_by=None;row.session=None;row.expires_at=None;row.save()
    else:raise ValidationError('Неизвестное действие блокировки.')
    return signing.dumps({'user':user.pk,'company':user.company_id,'camp':camp.pk,'day':str(day),'session':str(session),'versions':version(rows),'beds':bed_versions(user,keys)},salt=SALT,compress=True)


def check(user,camp,day,employee,bed,session,signed):
    try:payload=signing.loads(signed,salt=SALT,max_age=3600)
    except (signing.BadSignature,TypeError):raise ValidationError('Возьмите сотрудника и место в оформление заново.')
    if any(payload.get(k)!=v for k,v in {'user':user.pk,'company':user.company_id,'camp':camp.pk,'day':str(day),'session':str(session)}.items()):raise ValidationError('Форма относится к другому пользователю, ВЖГ или дате.')
    keys=resources(user,employee,bed,day);rows=controls(user,keys)
    if payload.get('versions')!=version(rows) or payload.get('beds')!=bed_versions(user,keys):raise ValidationError('Проживание или место уже изменены. Обновите данные и начните оформление заново.')
    for row in rows:
        locked_elsewhere(row,user,session)
        if row.locked_by_id!=user.pk or row.session!=session or not row.expires_at or row.expires_at<=timezone.now():raise ValidationError('Срок оформления истёк. Возьмите сотрудника и место заново.')
    return rows


def bump(rows):
    for row in rows:
        row.revision+=1;row.locked_by=None;row.session=None;row.expires_at=None;row.save()


@with_history_actor
@transaction.atomic
def move(user,kind,camp,day,employee,*,bed=None,session=None,signed=None,planned_departure=None,keys_issued=False,key_number='',pass_number='',comment=''):
    require_roles(user,OPERATORS);require_fact_day(user,day)
    if kind not in {'CHECKIN','MOVE','CHECKOUT','UPDATE'}:raise ValidationError('Выберите действие с проживанием.')
    if camp.company_id!=user.company_id:raise ValidationError('ВЖГ другой компании.')
    mutex(user);employee=Employee.objects.select_for_update().get(company=user.company,pk=employee.pk)
    if bed:bed=LodgingBed.objects.select_for_update().select_related('building').get(company=user.company,pk=bed.pk)
    rows=check(user,camp,day,employee,bed,session_id(session),signed)
    current=present(user.company,day).select_for_update().filter(employee=employee).first()
    if kind in {'CHECKIN','MOVE'}:
        if not employee.is_active:raise ValidationError('Для нового заселения выберите активного сотрудника.')
        if not bed or bed.building.camp_id!=camp.pk:raise ValidationError('Выберите место в этом ВЖГ.')
        if not camp.is_active or not bed.building.is_active or bed.state!='AVAILABLE':raise ValidationError('Место недоступно. Снимите резерв или измените доступность в справочнике.')
        if kind=='CHECKIN' and current:raise ValidationError('Сотрудник уже заселён. Оформите переселение.')
    if kind!='CHECKIN' and (not current or current.camp_id!=camp.pk):raise ValidationError('На эту дату сотрудник не проживает в выбранном ВЖГ.')
    if kind=='MOVE' and current.bed_id==bed.pk:raise ValidationError('Выберите другое место.')
    old_bed=current.bed if current else None
    if kind in {'MOVE','CHECKOUT'}:
        current.departed_on=day;current.save(update_fields=['departed_on','updated_at'])
    if kind in {'CHECKIN','MOVE'}:
        stay=LodgingStay(company=user.company,camp=camp,employee=employee,bed=bed,arrived_on=day,planned_departure=planned_departure,keys_issued=keys_issued,key_number=key_number,pass_number=pass_number,comment=comment,recorded_by=user)
        stay.organization_snapshot=employee.zup_organisation or str(employee.company)
        stay.position_snapshot=employee.display_position
        stay.department_snapshot=employee.display_department
        stay.save()
    elif kind=='UPDATE':
        current.planned_departure=planned_departure;current.keys_issued=keys_issued;current.key_number=key_number;current.pass_number=pass_number;current.comment=comment;current.save();stay=current
    else:stay=current
    LodgingEvent.objects.create(company=user.company,camp=camp,employee=employee,date=day,kind=kind,from_bed=old_bed,to_bed=bed if kind in {'CHECKIN','MOVE'} else None,stay=stay,comment=comment,recorded_by=user)
    require_fact_day(user,day);bump(rows)
    return stay


@with_history_actor
@transaction.atomic
def cancel(user,stay,reason,updated):
    require_roles(user,OPERATORS);mutex(user)
    stay=LodgingStay.objects.select_for_update().get(pk=stay.pk,company=user.company)
    require_fact_day(user,stay.arrived_on)
    if stay.departed_on:require_fact_day(user,stay.departed_on)
    if not reason.strip():raise ValidationError('Укажите причину отмены.')
    if stay.updated_at.isoformat()!=updated or stay.cancelled_at:raise ValidationError('Запись уже изменена. Откройте историю заново.')
    rows=controls(user,[('bed',stay.bed_id),('employee',stay.employee_id)])
    for row in rows:locked_elsewhere(row,user,None)
    stay.cancelled_at=timezone.now();stay.cancelled_by=user;stay.cancellation_reason=reason.strip();stay.save()
    LodgingEvent.objects.create(company=user.company,camp=stay.camp,employee=stay.employee,date=stay.arrived_on,kind='CANCEL',from_bed=stay.bed,stay=stay,comment=reason,recorded_by=user)
    bump(rows)


@transaction.atomic
def correct(user,stay,values,updated,reason):
    from core.fact_history import history_actor
    require_roles(user,OPERATORS);mutex(user)
    with history_actor(user,reason):
        stay=LodgingStay.objects.select_for_update().get(pk=stay.pk,company=user.company)
        if not reason.strip():raise ValidationError('Укажите причину исправления.')
        if stay.updated_at.isoformat()!=updated or stay.cancelled_at:raise ValidationError('Запись уже изменена. Откройте её заново.')
        for day in {stay.arrived_on,stay.departed_on,values['arrived_on'],values.get('departed_on')} - {None}:require_fact_day(user,day)
        rows=controls(user,[('bed',stay.bed_id),('employee',stay.employee_id)])
        for row in rows:locked_elsewhere(row,user,None)
        for name in ('arrived_on','departed_on','planned_departure','keys_issued','pass_number','comment'):setattr(stay,name,values.get(name))
        if 'key_number' in values:stay.key_number=values['key_number']
        stay.save()
        LodgingEvent.objects.create(company=user.company,camp=stay.camp,employee=stay.employee,date=values.get('departed_on') or values['arrived_on'],kind='UPDATE',from_bed=stay.bed,to_bed=stay.bed,stay=stay,comment=reason,recorded_by=user)
        bump(rows)
        return stay
