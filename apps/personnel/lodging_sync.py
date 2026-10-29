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
from .models import CampStay,LodgingStay,LodgingResolution,CampObjectLink,Camp

HR_OPERATORS={'ADMIN','HR_HEAD','PERSONNEL_DISPATCHER'}
TITLES={'PRODUCTION':'Есть производственный факт, нахождение в ВЖГ не оформлено','ARRIVAL':'Заезд требует подтверждения','WAITING':'Ожидает размещения','DEPARTED_OPEN':'Выезд оформлен, проживание не закрыто','AFTER_CHECKOUT':'Проживание завершено — уточнить нахождение','CAMP':'Различаются ВЖГ','DATES':'Различаются даты заезда и заселения'}


def version(row):return [row.pk,row.updated_at.isoformat()] if row else None


def digest(rows):return hashlib.sha256(json.dumps(sorted(row['fingerprint'] for row in rows)).encode()).hexdigest()


def issues(company,camps,day,*,include_resolved=False):
    selected=list(camps);ids={c.pk for c in selected}
    from apps.production.dispatch_models import EmployeeActivity
    from apps.production.employee_pool import activity_direction
    facts={};object_camps={}
    for object_id,camp_id in CampObjectLink.objects.filter(company=company).values_list('construction_object_id','camp_id'):object_camps.setdefault(object_id,set()).add(camp_id)
    for fact in EmployeeActivity.objects.filter(company=company,date=day,assignment_reviewed=True).select_related('employee__position__planning_mapping__brigade__group','reported_by__role','construction_object'):
        if activity_direction(fact)=='works':facts.setdefault(fact.employee_id,[]).append(fact)
    hr_all=list(CampStay.objects.filter(company=company,cancelled_at=None,arrived_on__lte=day,person__employee__isnull=False).select_related('person__employee__position__planning_mapping__brigade__group','camp').order_by('-arrived_on','-pk'))
    living=list(LodgingStay.objects.filter(company=company,cancelled_at=None,arrived_on__lte=day).select_related('employee__position__planning_mapping__brigade__group','camp','bed__building').order_by('-arrived_on','-pk'))
    hr={};last_hr={};rooms={};last_room={};chains={}
    for row in hr_all:
        pk=row.person.employee_id;last_hr.setdefault(pk,row)
        if row.arrived_on!=row.departed_on and (row.departed_on is None or row.departed_on>day):hr.setdefault(pk,row)
    for row in living:
        last_room.setdefault(row.employee_id,row);chains.setdefault(row.employee_id,[]).append(row)
        if row.arrived_on!=row.departed_on and (row.departed_on is None or row.departed_on>day):rooms.setdefault(row.employee_id,row)
    result=[]
    acknowledgments={(r.employee_id,r.fingerprint):r for r in LodgingResolution.objects.filter(company=company).select_related('recorded_by')}
    for pk in sorted(set(hr)|set(rooms)|set(facts)):
        resident=rooms.get(pk);personnel=hr.get(pk);old_hr=last_hr.get(pk);old_room=last_room.get(pk)
        involved={r.camp_id for r in (resident,personnel,old_hr,old_room) if r}
        production=facts.get(pk,[])
        related_production=[fact for fact in production if not object_camps.get(fact.construction_object_id) or object_camps[fact.construction_object_id]&ids]
        if resident or personnel:
            if not involved&ids:continue
        elif not related_production:continue
        # An internal same-camp move keeps the original continuous residence date.
        arrival=resident.arrived_on if resident else None
        if resident:
            for previous in chains.get(pk,[]):
                if previous.pk!=resident.pk and previous.camp_id==resident.camp_id and previous.departed_on==arrival:arrival=previous.arrived_on
        if not resident and not personnel:
            if not related_production or not selected:continue
            code='PRODUCTION'
        elif resident and not personnel:
            code='DEPARTED_OPEN' if old_hr and old_hr.camp_id==resident.camp_id and old_hr.departed_on and old_hr.departed_on>=arrival else 'ARRIVAL'
        elif personnel and not resident:
            code='AFTER_CHECKOUT' if old_room and old_room.camp_id==personnel.camp_id and old_room.departed_on and old_room.departed_on>=personnel.arrived_on else 'WAITING'
        elif resident.camp_id!=personnel.camp_id:code='CAMP'
        elif arrival!=personnel.arrived_on:code='DATES'
        else:continue
        employee=resident.employee if resident else personnel.person.employee if personnel else production[0].employee
        # Includes both source revisions; a later correction invalidates an old explanation.
        basis=[pk,code,version(resident),version(personnel),version(old_room),version(old_hr),str(arrival)]
        if production:basis.append(['production',sorted([f.pk,f.updated_at.isoformat(),f.construction_object_id,f.shift] for f in production)])
        fingerprint=hashlib.sha256(json.dumps(basis,ensure_ascii=False).encode()).hexdigest()
        resolved=acknowledgments.get((pk,fingerprint))
        if resolved and not include_resolved:continue
        camp=resident.camp if resident else personnel.camp if personnel else selected[0]
        suggestion=day if code=='PRODUCTION' else arrival if code in {'ARRIVAL','CAMP'} else old_hr.departed_on if code=='DEPARTED_OPEN' else old_room.departed_on if code=='AFTER_CHECKOUT' else personnel.arrived_on
        result.append({'employee':employee,'code':code,'title':TITLES[code],'camp':camp,'living':resident,'personnel':personnel,'last_hr':old_hr,'last_room':old_room,'arrival':arrival,'suggested_date':suggestion,'fingerprint':fingerprint,'resolved':resolved,'production':production})
    from apps.resources.group_order import employee_order
    return sorted(result,key=lambda row:employee_order(row['employee']))


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


@with_history_actor
@transaction.atomic
def confirm_production_arrival(user,camp,day,signed,target,actual_date,comment=''):
    require_roles(user,HR_OPERATORS);require_fact_day(user,day);require_fact_day(user,actual_date);lodging.mutex(user)
    row=verify_issue(user,[camp],day,signed)
    if row['code']!='PRODUCTION':raise ValidationError('Выбранная запись уже изменилась. Обновите сверку.')
    from apps.resources.models import Employee
    Employee.objects.select_for_update().get(company=user.company,pk=row['employee'].pk)
    row=verify_issue(user,[camp],day,signed)
    if target.company_id!=user.company_id or not target.is_active:raise ValidationError('Выберите действующий ВЖГ своей компании.')
    housing.move(user,'ARRIVAL',target,actual_date,[],employees=[row['employee']],comment=comment or 'Нахождение уточнено по производственному факту',snapshot=housing.movement_snapshot(user))
