"""Actual camp residence, independent of project assignment and planned calendars."""
from datetime import date
from django.core import signing
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q, F, Count, Max
from django.utils import timezone
from apps.resources.models import Employee
from .models import Camp, CampObjectLink, CampStay, HousingEvent, Person, StaffingSlot, Board, StaffingGroup

SALT='personnel-camp-movement-v1'


def present_stays(company, day):
    return CampStay.objects.filter(company=company,cancelled_at=None,arrived_on__lte=day).filter(Q(departed_on=None)|Q(departed_on__gt=day))


def available_employee_ids(obj,day):
    return present_stays(obj.company,day).filter(camp__company=obj.company,camp__object_links__company=obj.company,
        camp__object_links__construction_object=obj,person__company=obj.company,person__employee__company=obj.company,
        person__employee__is_active=True).exclude(person__employee=None).values('person__employee_id')


def require_available(employee,obj,day,lock=False):
    if lock:
        camp_ids=present_stays(obj.company,day).filter(person__employee=employee).values('camp_id')
        list(Camp.objects.select_for_update().filter(company=obj.company,pk__in=camp_ids).order_by('pk'))
    if not available_employee_ids(obj,day).filter(person__employee=employee).exists():
        raise ValidationError(f'{employee.display_name}: нет фактического размещения в ВЖГ, связанном с этим объектом, на {day:%d.%m.%Y}. Заезд и выезд оформляет диспетчер по персоналу.')


def matching_employee(person):
    """Only unambiguous company identities; matching names never merge two people."""
    qs=Employee.objects.filter(company=person.company)
    name=' '.join(person.full_name.split()).casefold()
    if person.employee_number:
        candidates=list(qs.filter(employee_number=person.employee_number)[:2])
        if len(candidates)!=1:return None
        candidate=candidates[0]
        if ' '.join(candidate.display_name.split()).casefold()!=name:return None
    else:
        same_names=[value for value in Person.objects.filter(company=person.company).values_list('full_name',flat=True) if ' '.join(value.split()).casefold()==name]
        if len(same_names)>1:return None
        candidates=[e for e in qs if ' '.join(e.display_name.split()).casefold()==name]
        if len(candidates)!=1:return None
        candidate=candidates[0]
    if Person.objects.filter(employee=candidate).exclude(pk=person.pk).exists():return None
    return candidate


def link_people(company):
    count=0
    with transaction.atomic():
        # Import and manual linking serialize within company, without touching source employee fields.
        from apps.accounts.models import Company
        Company.objects.select_for_update().get(pk=company.pk)
        for person in Person.objects.select_for_update().filter(company=company,employee=None):
            employee=matching_employee(person)
            if employee:
                person.employee=employee;person.save(update_fields=['employee','updated_at']);count+=1
    return count


def person_for(employee):
    person=Person.objects.filter(company=employee.company,employee=employee).first()
    if person:return person
    candidates=Person.objects.filter(company=employee.company,employee=None)
    if employee.employee_number:candidates=candidates.filter(employee_number=employee.employee_number)
    else:candidates=candidates.filter(full_name=employee.display_name)
    matches=list(candidates[:2])
    if not matches and employee.employee_number:
        matches=[p for p in Person.objects.filter(company=employee.company,employee=None,employee_number='') if ' '.join(p.full_name.split()).casefold()==' '.join(employee.display_name.split()).casefold()][:2]
    if matches and (len(matches)!=1 or matching_employee(matches[0])!=employee):raise ValidationError(f'{employee.display_name}: неоднозначная кадровая карточка. Уточните связь в реестре сотрудников.')
    if len(matches)==1 and matching_employee(matches[0])==employee:
        person=matches[0];person.employee=employee;person.save(update_fields=['employee','updated_at']);return person
    if employee.employee_number and Person.objects.filter(company=employee.company,employee_number=employee.employee_number).exists():
        raise ValidationError(f'{employee.display_name}: табельный номер занят кадровой карточкой. Уточните связь в реестре сотрудников.')
    return Person.objects.create(company=employee.company,employee=employee,full_name=employee.display_name,employee_number=employee.employee_number)


def versions(company,people):
    result={str(p.pk):[0,None] for p in people}
    for row in CampStay.objects.filter(company=company,person_id__in=result).values('person_id').annotate(count=Count('pk'),latest=Max('updated_at')):
        result[str(row['person_id'])]=[row['count'],row['latest'].isoformat()]
    return result


def movement_snapshot(user):
    people=Person.objects.filter(company=user.company)
    return signing.dumps({'user':user.pk,'company':user.company_id,'versions':versions(user.company,people)},salt=SALT,compress=True)


def check_snapshot(user,signed,people):
    try:payload=signing.loads(signed,salt=SALT,max_age=12*3600)
    except (signing.BadSignature,TypeError):raise ValidationError('Форма перемещения устарела. Обновите список; сохранённые данные останутся на месте.')
    if payload.get('user')!=user.pk or payload.get('company')!=user.company_id:raise ValidationError('Форма другого пользователя или компании.')
    actual=versions(user.company,people)
    for person in people:
        before=payload.get('versions',{}).get(str(person.pk),[0,None])
        if before!=actual[str(person.pk)]:raise ValidationError(f'{person}: размещение уже изменил другой диспетчер. Обновите список.')


@transaction.atomic
def move(user,kind,camp,day,people,employees=(),target=None,comment='',snapshot=None):
    if camp.company_id!=user.company_id or target and target.company_id!=user.company_id:raise ValidationError('ВЖГ другой компании.')
    if kind not in {'ARRIVAL','DEPARTURE','TRANSFER'}:raise ValidationError('Неизвестное действие.')
    if kind=='TRANSFER' and (not target or target.pk==camp.pk):raise ValidationError('Выберите другой ВЖГ для перевода.')
    if day>timezone.localdate():raise ValidationError('Фактическое событие нельзя вносить будущей датой. Плановые даты задаются графиком.')
    if kind=='ARRIVAL' and not camp.is_active or target and not target.is_active:raise ValidationError('Для заезда выберите действующий ВЖГ.')
    from apps.accounts.models import Company
    Company.objects.filter(pk=user.company_id).update(name=F('name'))
    selected={p.pk:p for p in people}
    # Canonical employee lock also serializes production validation and cross-camp moves.
    employee_ids={p.employee_id for p in people if p.employee_id}|{e.pk for e in employees}
    list(Employee.objects.select_for_update().filter(company=user.company,pk__in=employee_ids).order_by('pk'))
    for employee in employees:
        if employee.company_id!=user.company_id or not employee.is_active:raise ValidationError('Выберите действующего сотрудника своей компании.')
        person=person_for(employee);selected[person.pk]=person
    ids=sorted(selected)
    if not ids or len(ids)>300:raise ValidationError('Выберите от 1 до 300 сотрудников.')
    locked=list(Person.objects.select_for_update().filter(company=user.company,pk__in=ids).order_by('pk'))
    if len(locked)!=len(ids):raise ValidationError('Сотрудник другой компании.')
    if snapshot is not None:check_snapshot(user,snapshot,locked)
    locked_camps={row.pk:row for row in Camp.objects.select_for_update().filter(company=user.company,pk__in=[camp.pk]+([target.pk] if target else [])).order_by('pk')}
    camp=locked_camps[camp.pk];target=locked_camps[target.pk] if target else None
    if kind=='ARRIVAL' and not camp.is_active or target and not target.is_active:raise ValidationError('Для заезда выберите действующий ВЖГ.')
    for person in locked:
        current=present_stays(user.company,day).filter(person=person).first()
        if kind=='ARRIVAL':
            if current:raise ValidationError(f'{person}: уже находится в {current.camp}. Используйте перевод.')
            stay=CampStay.objects.create(company=user.company,person=person,camp=camp,arrived_on=day,comment=comment,recorded_by=user)
        else:
            if not current or current.camp_id!=camp.pk:raise ValidationError(f'{person}: на эту дату не находится в выбранном ВЖГ.')
            current.departed_on=day;current.save(update_fields=['departed_on','updated_at'])
            stay=current
            if kind=='TRANSFER':stay=CampStay.objects.create(company=user.company,person=person,camp=target,arrived_on=day,comment=comment,recorded_by=user)
        HousingEvent.objects.create(company=user.company,person=person,date=day,kind=kind,from_camp=camp if kind!='ARRIVAL' else None,to_camp=target if kind=='TRANSFER' else camp if kind=='ARRIVAL' else None,stay=stay,comment=comment,recorded_by=user)
    return len(locked)


@transaction.atomic
def cancel_stay(user,stay,reason,version):
    if stay.company_id!=user.company_id:raise ValidationError('Размещение другой компании.')
    if not reason.strip():raise ValidationError('Укажите причину отмены.')
    from apps.accounts.models import Company
    Company.objects.filter(pk=user.company_id).update(name=F('name'))
    if stay.person.employee_id:Employee.objects.select_for_update().get(pk=stay.person.employee_id,company=user.company)
    Person.objects.select_for_update().get(pk=stay.person_id,company=user.company)
    current=CampStay.objects.select_for_update().get(pk=stay.pk,company=user.company)
    if current.updated_at.isoformat()!=version or current.cancelled_at:raise ValidationError('Запись уже изменена. Обновите историю.')
    current.cancelled_at=timezone.now();current.cancelled_by=user;current.cancellation_reason=reason.strip();current.save()
    HousingEvent.objects.create(company=user.company,person=current.person,date=timezone.localdate(),kind='CANCEL',from_camp=current.camp,stay=current,comment=reason,recorded_by=user)


def schedule_slot(person):
    if person.schedule_slot_id and person.schedule_slot.person_id==person.pk and person.schedule_slot.company_id==person.company_id:return person.schedule_slot
    slots=list(person.assignments.select_related('pattern','group__board').order_by('-pk'))
    if len(slots)==1:return slots[0]
    if slots and len({(s.pattern_id,str(sorted(s.calendar.items()))) for s in slots})==1:return slots[0]
    return None


def bootstrap_legacy(company,slots=None):
    """Preserve source values; infer residence only from consistent explicit actual dates."""
    slots=list((slots if slots is not None else StaffingSlot.objects.filter(company=company)).select_related('person','housing_camp'))
    camps={' '.join(c.name.split()).casefold():c for c in Camp.objects.filter(company=company)}
    by_person={}
    for slot in slots:
        name=' '.join(slot.camp.split())
        if name and not slot.housing_camp_id:
            camp=camps.get(name.casefold())
            if camp is None:
                camp=Camp.objects.create(company=company,name=name);camps[name.casefold()]=camp
            slot.housing_camp=camp;slot.save(update_fields=['housing_camp','updated_at'])
        if slot.person_id:by_person.setdefault(slot.person_id,[]).append(slot)
    for person_id,rows in by_person.items():
        person=rows[0].person
        if not person.employee_id:
            employee=matching_employee(person)
            if employee:person.employee=employee;person.save(update_fields=['employee','updated_at'])
        if not person.schedule_slot_id:
            selected=schedule_slot(person)
            if selected:person.schedule_slot=selected;person.save(update_fields=['schedule_slot','updated_at'])
        if CampStay.objects.filter(company=company,person=person).exists():continue
        eligible=[s for s in rows if s.housing_camp_id and s.arrival_date and s.arrival_date<=timezone.localdate() and s.status=='МВ']
        if eligible and len({(s.housing_camp_id,s.arrival_date) for s in eligible})==1:
            row=eligible[0]
            stay=CampStay.objects.create(company=company,person=person,camp=row.housing_camp,arrived_on=row.arrival_date,comment='Перенесено из кадровой таблицы')
            HousingEvent.objects.create(company=company,person=person,date=row.arrival_date,kind='ARRIVAL',to_camp=row.housing_camp,stay=stay,comment=stay.comment)
        elif not eligible:
            closed=[s for s in rows if s.status=='МО' and s.housing_camp_id and s.arrival_date and s.departure_date and s.arrival_date<s.departure_date<=timezone.localdate()]
            if closed and len({(s.housing_camp_id,s.arrival_date,s.departure_date) for s in closed})==1:
                row=closed[0];CampStay.objects.create(company=company,person=person,camp=row.housing_camp,arrived_on=row.arrival_date,departed_on=row.departure_date,comment='Перенесено из кадровой таблицы')


def schedule_snapshot(person):
    return signing.dumps({'person':person.pk,'company':person.company_id,'updated':person.updated_at.isoformat(),
        'slots':{str(pk):updated.isoformat() for pk,updated in person.assignments.values_list('pk','updated_at')}},salt=SALT+'-schedule',compress=True)


def check_schedule(person,signed,slot):
    try:data=signing.loads(signed,salt=SALT+'-schedule',max_age=12*3600)
    except (signing.BadSignature,TypeError):raise ValidationError('Форма графика устарела. Обновите её перед сохранением.')
    if data.get('person')!=person.pk or data.get('company')!=person.company_id or data.get('updated')!=person.updated_at.isoformat():raise ValidationError('Карточка или основной график сотрудника уже изменены. Обновите форму.')
    if slot and data.get('slots',{}).get(str(slot.pk))!=slot.updated_at.isoformat():raise ValidationError('Эту строку графика уже изменил другой диспетчер. Обновите форму; его изменения сохранены.')
