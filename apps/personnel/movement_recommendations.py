"""Editable 14-day arrival and departure recommendations."""
from datetime import timedelta
from django.core import signing
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from .models import Camp,MovementRecommendationOverride,MovementReviewRequest,Person
from . import housing

TOKEN_SALT='personnel-movement-recommendation-v1'


def _key(kind,person_id,camp_id,planned_on):
    return kind,int(person_id),int(camp_id),str(planned_on)


def _row(kind,person,camp,planned_on,manual=False):
    key=_key(kind,person.pk,camp.pk,planned_on)
    return {'kind':kind,'person':person,'employee':person.employee,'camp':camp,'planned_on':planned_on,'manual':manual,
        'token':signing.dumps(key,salt=TOKEN_SALT,compress=True)}


def build(company,day,*,camps=None,rows=None):
    from .rotation import rotation_rows
    camps=list(camps) if camps is not None else list(Camp.objects.filter(company=company,is_active=True).order_by('name'))
    end=day+timedelta(days=14);auto={}
    if rows is None and camps:
        rows=rotation_rows(company,camps,day,include_unassigned=True)[0]
    for row in rows or []:
        person=row.get('person');employee=person.employee if person and person.employee_id else None
        if not employee:continue
        if row.get('state')=='MO' and row.get('planned_return') and day<=row['planned_return']<=end:
            camp=row['slot'].housing_camp if row.get('slot') and row['slot'].housing_camp_id else row.get('camp')
            if camp:auto[_key('ARRIVAL',person.pk,camp.pk,row['planned_return'])]=_row('ARRIVAL',person,camp,row['planned_return'])
        # An overdue departure remains actionable until the employee
        # actually leaves.  The upper bound is still the agreed 14 days.
        if row.get('state')=='MV' and row.get('planned_departure_display') and row['planned_departure_display']<=end and row.get('camp'):
            planned=row['planned_departure_display'];auto[_key('DEPARTURE',person.pk,row['camp'].pk,planned)]=_row('DEPARTURE',person,row['camp'],planned)
    present={(person_id,camp_id) for person_id,camp_id in housing.present_stays(company,day).values_list('person_id','camp_id')}
    recommendation_window=Q(planned_on__range=(day,end))|Q(kind='DEPARTURE',planned_on__lt=day)
    camp_ids={camp.pk for camp in camps}
    for override in MovementRecommendationOverride.objects.filter(recommendation_window,company=company,camp_id__in=camp_ids).select_related('person__employee','camp'):
        key=_key(override.kind,override.person_id,override.camp_id,override.planned_on)
        if override.action=='EXCLUDE':auto.pop(key,None);continue
        eligible=(override.person_id,override.camp_id) not in present if override.kind=='ARRIVAL' else (override.person_id,override.camp_id) in present
        if eligible:auto[key]=_row(override.kind,override.person,override.camp,override.planned_on,manual=True)
    request_window=Q(movement_date__range=(day,end))|Q(kind='DEPARTURE',movement_date__lt=day)
    for request in MovementReviewRequest.objects.filter(request_window,company=company).filter(Q(to_camp_id__in=camp_ids)|Q(from_camp_id__in=camp_ids)).exclude(status='CANCELLED'):
        camp_id=request.to_camp_id if request.kind=='ARRIVAL' else request.from_camp_id
        if camp_id:auto.pop(_key(request.kind,request.person_id,camp_id,request.movement_date),None)
    values=sorted(auto.values(),key=lambda row:(row['planned_on'],row['person'].display_name.casefold(),row['camp'].name.casefold()))
    return {'ARRIVAL':[row for row in values if row['kind']=='ARRIVAL'],'DEPARTURE':[row for row in values if row['kind']=='DEPARTURE']}


def decode(token,company):
    try:kind,person_id,camp_id,planned=signing.loads(token,salt=TOKEN_SALT,max_age=24*3600)
    except (signing.BadSignature,ValueError,TypeError):raise ValidationError('Список рекомендаций устарел. Обновите страницу.')
    if kind not in {'ARRIVAL','DEPARTURE'}:raise ValidationError('Неизвестная рекомендация.')
    from django import forms
    try:planned_on=forms.DateField().clean(planned)
    except ValidationError:raise ValidationError('Некорректная дата рекомендации.')
    person=Person.objects.filter(company=company,pk=person_id).first();camp=Camp.objects.filter(company=company,pk=camp_id).first()
    if not person or not camp:raise ValidationError('Рекомендация больше недоступна.')
    return kind,person,camp,planned_on


@transaction.atomic
def exclude(user,tokens):
    count=0
    for token in set(tokens):
        kind,person,camp,planned_on=decode(token,user.company)
        MovementRecommendationOverride.objects.update_or_create(company=user.company,person=person,camp=camp,kind=kind,planned_on=planned_on,
            defaults={'action':'EXCLUDE','recorded_by':user})
        count+=1
    return count


@transaction.atomic
def add(user,kind,employees,camp,planned_on):
    if kind not in {'ARRIVAL','DEPARTURE'}:raise ValidationError('Неизвестное направление рекомендации.')
    present=housing.present_stays(user.company,planned_on)
    if kind=='DEPARTURE':
        allowed=set(present.filter(camp=camp).values_list('person__employee_id',flat=True))
        if any(employee.pk not in allowed for employee in employees):raise ValidationError('Для рекомендации к выезду выберите сотрудников, находящихся в этом ВЖГ на указанную дату.')
    count=0
    for employee in employees:
        person=housing.person_for(employee)
        if kind=='ARRIVAL' and present.filter(person=person).exists():raise ValidationError(f'{employee.display_name} уже находится в ВЖГ на указанную дату.')
        MovementRecommendationOverride.objects.update_or_create(company=user.company,person=person,camp=camp,kind=kind,planned_on=planned_on,
            defaults={'action':'ADD','recorded_by':user})
        count+=1
    return count


@transaction.atomic
def create_requests(user,tokens):
    count=0
    for token in dict.fromkeys(tokens):
        kind,person,camp,planned_on=decode(token,user.company)
        lookup={'company':user.company,'person':person,'kind':kind,'movement_date':planned_on,
            'to_camp':camp if kind=='ARRIVAL' else None,'from_camp':camp if kind=='DEPARTURE' else None}
        if MovementReviewRequest.objects.filter(**lookup).exists():continue
        employee=person.employee
        construction_object,site_object=housing.request_object_pair(person,planned_on,camp)
        MovementReviewRequest.objects.create(**lookup,requested_by=user,
            employment_kind='STAFF',planning_position_name=housing.planning_position_name(person,planned_on),position_name=employee.display_position if employee else '',
            construction_object=construction_object,site_object=site_object,site=site_object.name if site_object else camp.name,
            phone=(employee.phone if employee else '') or person.phone,travel_status='Прибыл' if kind=='ARRIVAL' else 'Убыл')
        MovementRecommendationOverride.objects.update_or_create(company=user.company,person=person,camp=camp,kind=kind,planned_on=planned_on,
            defaults={'action':'EXCLUDE','recorded_by':user})
        count+=1
    if not tokens:raise ValidationError('Выберите хотя бы одного сотрудника.')
    return count
