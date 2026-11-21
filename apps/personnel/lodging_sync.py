"""One live reconciliation for both roles; source records remain independent."""
import hashlib,json,logging
from datetime import date,timedelta
from time import perf_counter
from django.core import signing
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import F
from django.utils import timezone
from core.fact_history import with_history_actor
from core.fact_edit_window import require_fact_day
from core.permissions import require_roles
from . import housing,lodging
from .models import CampStay,LodgingStay,LodgingResolution,CampObjectLink,Camp,LodgingSyncState

HR_OPERATORS={'ADMIN','HR_HEAD','PERSONNEL_DISPATCHER'}
TITLES={'PRODUCTION':'Есть производственный факт, нахождение в местонахождении не оформлено','ARRIVAL':'Заезд требует подтверждения','WAITING':'Ожидает размещения','DEPARTED_OPEN':'Выезд оформлен, проживание не закрыто','AFTER_CHECKOUT':'Проживание завершено — уточнить нахождение','CAMP':'Различаются местонахождения','DATES':'Различаются даты заезда и заселения'}
SYNC_STAGES=[('queued','Задание принято',5),('personnel','Чтение перевахты',20),('lodging','Чтение проживания',40),('production','Чтение производственного факта',60),('compare','Сопоставление сотрудников',80),('saving','Сохранение результата',95),('done','Сверка готова',100)]


def state_for(company,camp,day):
    state,_=LodgingSyncState.objects.get_or_create(company=company,camp=camp,day=day)
    return state


def mark_dirty(company_id,camp_ids):
    ids={int(pk) for pk in camp_ids if pk}
    if ids:LodgingSyncState.objects.filter(company_id=company_id,camp_id__in=ids).update(source_version=F('source_version')+1)


def state_payload(state):
    busy=state.status in {'QUEUED','RUNNING'}
    return {'id':state.pk,'status':state.status,'busy':busy,'stage':state.stage or state.get_status_display(),
        'progress':state.progress,'source_version':state.source_version,'calculated_version':state.calculated_version,
        'current':state.current,'dirty':state.calculated_version!=state.source_version,'count':state.issue_count,
        'calculated_at':state.calculated_at.isoformat() if state.calculated_at else None,'error':state.error}


def enqueue(user,camp,day):
    with transaction.atomic():
        state=state_for(user.company,camp,day)
        state=LodgingSyncState.objects.select_for_update().get(pk=state.pk)
        if state.status in {'QUEUED','RUNNING'} and state.updated_at>=timezone.now()-timedelta(minutes=10):return state,False
        if state.current:return state,False
        state.status='QUEUED';state.stage='Задание принято';state.progress=5;state.error='';state.requested_by=user
        state.started_at=None;state.finished_at=None;state.save(update_fields=['status','stage','progress','error','requested_by','started_at','finished_at','updated_at'])
        return state,True


def _date(value):return value.isoformat() if value else None


def serialize_issue(row):
    return {'employee':row['employee'].pk,'code':row['code'],'camp':row['camp'].pk,
        'living':row['living'].pk if row['living'] else None,'personnel':row['personnel'].pk if row['personnel'] else None,
        'last_hr':row['last_hr'].pk if row['last_hr'] else None,'last_room':row['last_room'].pk if row['last_room'] else None,
        'arrival':_date(row['arrival']),'suggested_date':_date(row['suggested_date']),'fingerprint':row['fingerprint'],
        'resolved':row['resolved'].pk if row['resolved'] else None,'production':[item.pk for item in row['production']]}


def hydrate(company,payload):
    from apps.resources.models import Employee
    from apps.production.dispatch_models import EmployeeActivity
    employees=Employee.objects.filter(company=company,pk__in=[row['employee'] for row in payload]).select_related('position')
    rooms=LodgingStay.objects.filter(company=company,pk__in={pk for row in payload for pk in (row.get('living'),row.get('last_room')) if pk}).select_related('employee','camp','bed__building','recorded_by')
    personnel=CampStay.objects.filter(company=company,pk__in={pk for row in payload for pk in (row.get('personnel'),row.get('last_hr')) if pk}).select_related('person__employee','camp','recorded_by')
    productions=EmployeeActivity.objects.filter(company=company,pk__in={pk for row in payload for pk in row.get('production',[])}).select_related('construction_object','reported_by')
    resolutions=LodgingResolution.objects.filter(company=company,pk__in={row['resolved'] for row in payload if row.get('resolved')}).select_related('recorded_by')
    camps=Camp.objects.filter(company=company,pk__in={row['camp'] for row in payload})
    maps=[{item.pk:item for item in query} for query in (employees,rooms,personnel,productions,resolutions,camps)]
    employee_map,room_map,personnel_map,production_map,resolution_map,camp_map=maps
    rows=[]
    for item in payload:
        if item['employee'] not in employee_map or item['camp'] not in camp_map:continue
        rows.append({'employee':employee_map[item['employee']],'code':item['code'],'title':TITLES[item['code']],
            'camp':camp_map[item['camp']],'living':room_map.get(item.get('living')),'personnel':personnel_map.get(item.get('personnel')),
            'last_hr':personnel_map.get(item.get('last_hr')),'last_room':room_map.get(item.get('last_room')),
            'arrival':date.fromisoformat(item['arrival']) if item.get('arrival') else None,
            'suggested_date':date.fromisoformat(item['suggested_date']) if item.get('suggested_date') else None,
            'fingerprint':item['fingerprint'],'resolved':resolution_map.get(item.get('resolved')),
            'production':[production_map[pk] for pk in item.get('production',[]) if pk in production_map]})
    return rows


def snapshot_rows(state,include_resolved=False):
    payload=state.result if include_resolved else [row for row in state.result if not row.get('resolved')]
    return hydrate(state.company,payload)


def calculate_state(pk):
    started=perf_counter()
    with transaction.atomic():
        state=LodgingSyncState.objects.select_for_update().select_related('company','camp').get(pk=pk)
        if state.status not in {'QUEUED','RUNNING'}:return state
        target=state.source_version;state.status='RUNNING';state.stage='Чтение перевахты';state.progress=20;state.started_at=timezone.now();state.error=''
        state.save(update_fields=['status','stage','progress','started_at','error','updated_at'])
    def progress(code,label,percent):LodgingSyncState.objects.filter(pk=pk).update(stage=label,progress=percent)
    try:
        rows=issues(state.company,[state.camp],state.day,include_resolved=True,progress=progress)
        progress('saving','Сохранение результата',95);result=[serialize_issue(row) for row in rows]
        with transaction.atomic():
            state=LodgingSyncState.objects.select_for_update().get(pk=pk)
            state.result=result;state.issue_count=sum(1 for row in result if not row.get('resolved'));state.calculated_version=target
            state.status='DONE';state.stage='Сверка готова';state.progress=100;state.calculated_at=timezone.now();state.finished_at=state.calculated_at;state.error=''
            state.save(update_fields=['result','issue_count','calculated_version','status','stage','progress','calculated_at','finished_at','error','updated_at'])
    except Exception as exc:
        LodgingSyncState.objects.filter(pk=pk).update(status='ERROR',stage='Ошибка расчёта',error=str(exc)[:4000],finished_at=timezone.now())
        raise
    elapsed=(perf_counter()-started)*1000
    if elapsed>=1000:logging.getLogger('gpr.performance').warning('lodging reconciliation state=%s %.0f ms',pk,elapsed)
    return state


def version(row):return [row.pk,row.updated_at.isoformat()] if row else None


def digest(rows):return hashlib.sha256(json.dumps(sorted(row['fingerprint'] for row in rows)).encode()).hexdigest()


def issues(company,camps,day,*,include_resolved=False,progress=None):
    progress=progress or (lambda *args:None)
    selected=list(camps);ids={c.pk for c in selected}
    from apps.production.dispatch_models import EmployeeActivity
    from apps.production.employee_pool import activity_direction
    facts={};object_camps={};progress('personnel','Чтение перевахты',20)
    for object_id,camp_id in CampObjectLink.objects.filter(company=company).values_list('construction_object_id','camp_id'):object_camps.setdefault(object_id,set()).add(camp_id)
    progress('production','Чтение производственного факта',35)
    for fact in EmployeeActivity.objects.filter(company=company,date=day,assignment_reviewed=True).select_related('employee__position__planning_mapping__brigade__group','reported_by__role','construction_object'):
        if activity_direction(fact)=='works':facts.setdefault(fact.employee_id,[]).append(fact)
    progress('personnel','Чтение перевахты',45)
    hr_all=list(CampStay.objects.filter(company=company,cancelled_at=None,arrived_on__lte=day,person__employee__isnull=False).select_related('person__employee__position__planning_mapping__brigade__group','camp','recorded_by').order_by('-arrived_on','-pk'))
    # Accommodation is an operational register: open rows are current even
    # when their documentary date is in the past or future.
    progress('lodging','Чтение проживания',60)
    living=list(LodgingStay.objects.filter(company=company,cancelled_at=None,employee__isnull=False).select_related('employee__position__planning_mapping__brigade__group','camp','bed__building','recorded_by').order_by('-pk'))
    hr={};last_hr={};rooms={};last_room={};chains={}
    for row in hr_all:
        pk=row.person.employee_id;last_hr.setdefault(pk,row)
        if row.arrived_on!=row.departed_on and (row.departed_on is None or row.departed_on>day):hr.setdefault(pk,row)
    for row in living:
        last_room.setdefault(row.employee_id,row);chains.setdefault(row.employee_id,[]).append(row)
        if row.departed_on is None:rooms.setdefault(row.employee_id,row)
    progress('compare','Сопоставление сотрудников',75);result=[]
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
    if target.company_id!=user.company_id or not target.is_active:raise ValidationError('Выберите действующее местонахождение своей компании.')
    housing.move(user,'ARRIVAL',target,actual_date,[],employees=[row['employee']],comment=comment or 'Нахождение уточнено по производственному факту',snapshot=housing.movement_snapshot(user))
