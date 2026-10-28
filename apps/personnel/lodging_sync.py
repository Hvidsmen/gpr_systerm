"""One live reconciliation for both roles; source records remain independent."""
import hashlib,json
from django.core import signing
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from core.fact_history import with_history_actor
from core.fact_edit_window import require_fact_day
from core.permissions import require_roles
from . import housing,lodging
from .models import CampStay,LodgingStay,LodgingResolution

HR_OPERATORS={'ADMIN','HR_HEAD','PERSONNEL_DISPATCHER'}
TITLES={'ARRIVAL':'Заезд требует подтверждения','WAITING':'Ожидает размещения','DEPARTED_OPEN':'Выезд оформлен, проживание не закрыто','AFTER_CHECKOUT':'Проживание завершено — уточнить нахождение','CAMP':'Различаются ВЖГ','DATES':'Различаются даты заезда и заселения'}


def version(row):return [row.pk,row.updated_at.isoformat()] if row else None


def digest(rows):return hashlib.sha256(json.dumps(sorted(row['fingerprint'] for row in rows)).encode()).hexdigest()


def issues(company,camps,day,*,include_resolved=False):
    ids={c.pk for c in camps}
    hr_all=list(CampStay.objects.filter(company=company,cancelled_at=None,arrived_on__lte=day,person__employee__isnull=False).select_related('person__employee','camp').order_by('-arrived_on','-pk'))
    living=list(LodgingStay.objects.filter(company=company,cancelled_at=None,arrived_on__lte=day).select_related('employee','camp','bed__building').order_by('-arrived_on','-pk'))
    hr={};last_hr={};rooms={};last_room={};chains={}
    for row in hr_all:
        pk=row.person.employee_id;last_hr.setdefault(pk,row)
        if row.arrived_on!=row.departed_on and (row.departed_on is None or row.departed_on>day):hr.setdefault(pk,row)
    for row in living:
        last_room.setdefault(row.employee_id,row);chains.setdefault(row.employee_id,[]).append(row)
        if row.arrived_on!=row.departed_on and (row.departed_on is None or row.departed_on>day):rooms.setdefault(row.employee_id,row)
    result=[]
    acknowledgments={(r.employee_id,r.fingerprint):r for r in LodgingResolution.objects.filter(company=company).select_related('recorded_by')}
    for pk in sorted(set(hr)|set(rooms)):
        resident=rooms.get(pk);personnel=hr.get(pk);old_hr=last_hr.get(pk);old_room=last_room.get(pk)
        involved={r.camp_id for r in (resident,personnel,old_hr,old_room) if r}
        if not involved&ids:continue
        # An internal same-camp move keeps the original continuous residence date.
        arrival=resident.arrived_on if resident else None
        if resident:
            for previous in chains.get(pk,[]):
                if previous.pk!=resident.pk and previous.camp_id==resident.camp_id and previous.departed_on==arrival:arrival=previous.arrived_on
        if resident and not personnel:
            code='DEPARTED_OPEN' if old_hr and old_hr.camp_id==resident.camp_id and old_hr.departed_on and old_hr.departed_on>=arrival else 'ARRIVAL'
        elif personnel and not resident:
            code='AFTER_CHECKOUT' if old_room and old_room.camp_id==personnel.camp_id and old_room.departed_on and old_room.departed_on>=personnel.arrived_on else 'WAITING'
        elif resident.camp_id!=personnel.camp_id:code='CAMP'
        elif arrival!=personnel.arrived_on:code='DATES'
        else:continue
        employee=resident.employee if resident else personnel.person.employee
        # Includes both source revisions; a later correction invalidates an old explanation.
        fingerprint=hashlib.sha256(json.dumps([pk,code,version(resident),version(personnel),version(old_room),version(old_hr),str(arrival)],ensure_ascii=False).encode()).hexdigest()
        resolved=acknowledgments.get((pk,fingerprint))
        if resolved and not include_resolved:continue
        camp=resident.camp if resident else personnel.camp
        suggestion=arrival if code in {'ARRIVAL','CAMP'} else old_hr.departed_on if code=='DEPARTED_OPEN' else old_room.departed_on if code=='AFTER_CHECKOUT' else personnel.arrived_on
        result.append({'employee':employee,'code':code,'title':TITLES[code],'camp':camp,'living':resident,'personnel':personnel,'last_hr':old_hr,'last_room':old_room,'arrival':arrival,'suggested_date':suggestion,'fingerprint':fingerprint,'resolved':resolved})
    return result


def signed_issue(user,row):return signing.dumps({'user':user.pk,'company':user.company_id,'employee':row['employee'].pk,'fingerprint':row['fingerprint']},salt='lodging-sync',compress=True)


def verify_issue(user,camps,day,signed):
    try:data=signing.loads(signed,salt='lodging-sync',max_age=3600)
    except (signing.BadSignature,TypeError):raise ValidationError('Сверка устарела. Обновите страницу.')
    if data.get('user')!=user.pk or data.get('company')!=user.company_id:raise ValidationError('Сверка другого пользователя.')
    row=next((r for r in issues(user.company,camps,day) if r['employee'].pk==data.get('employee') and r['fingerprint']==data.get('fingerprint')),None)
    if row is None:raise ValidationError('Сведения уже изменились или расхождение устранено. Обновите сверку.')
    return row


@with_history_actor
@transaction.atomic
def explain(user,camp,day,signed,reason):
    require_roles(user,lodging.OPERATORS|HR_OPERATORS);require_fact_day(user,day);lodging.mutex(user)
    row=verify_issue(user,[camp],day,signed)
    reason=reason.strip()
    if not reason or len(reason)>1000:raise ValidationError('Укажите объяснение расхождения до 1000 символов.')
    return LodgingResolution.objects.create(company=user.company,employee=row['employee'],camp=camp,date=day,fingerprint=row['fingerprint'],reason=reason,recorded_by=user)


@with_history_actor
@transaction.atomic
def confirm_arrival(user,camp,day,signed,actual_date,comment=''):
    require_roles(user,HR_OPERATORS);require_fact_day(user,day);require_fact_day(user,actual_date);lodging.mutex(user)
    row=verify_issue(user,[camp],day,signed)
    if row['code']!='ARRIVAL':raise ValidationError('Для этого расхождения нужно уточнить перемещение или даты, а не подтверждать новый заезд.')
    housing.move(user,'ARRIVAL',row['living'].camp,actual_date,[],employees=[row['employee']],comment=comment or 'Заезд подтверждён по сведениям коменданта',snapshot=housing.movement_snapshot(user))
