from datetime import timedelta

from django.core.exceptions import ValidationError,PermissionDenied
from django.db import transaction
from django.utils import timezone

from core.permissions import role_code
from apps.planning.models import WorkTask
from apps.planning.task_services import ensure,event as task_event,state
from .models import CampStay,HousingEvent,MovementReviewEvent,MovementReviewRequest

ROUTE=[
    ('MANAGER','Руководитель проекта'),('SITE_CHIEF','Начальник участка'),
    ('PEREVAKHTA_SPECIALIST','Специалист перевахты'),
    ('DUDINKA_HEAD','Руководитель ОП Дудинка'),
    ('DUDINKA_SECURITY_DEPUTY','Заместитель руководителя ОП по безопасности'),
    ('CHIEF_MECHANIC','Главный механик'),('PARAMEDIC','Фельдшер'),
    ('PERSONNEL_SPECIALIST','Специалист по персоналу'),
    ('LABOR_ECONOMIST','Эксперт по экономике труда и заработной плате'),
]
PARALLEL={code for code,_ in ROUTE[3:]}
ROLE_LABELS=dict(ROUTE)

def initial_route():
    return {code:{'status':'PENDING' if code=='MANAGER' else 'WAITING','comment':'','actor':'','at':''} for code,_ in ROUTE}

def route_for(row):
    route=initial_route()
    if not row.approval_route and row.status not in {'PENDING_APPROVAL','RETURNED'}:
        for code in route:route[code]['status']='APPROVED'
    route.update(row.approval_route or {})
    return route

def active_route_roles(row):
    return {code for code,data in route_for(row).items() if data.get('status')=='PENDING'}

def task_route_roles(task):
    if not task.key.startswith(('personnel-movement-batch:','personnel-movement-approval:')):return set()
    if task.key.startswith('personnel-movement-batch:'):
        rows=MovementReviewRequest.objects.filter(company=task.company,approval_batch=task.key.split(':',1)[1])
    else:rows=MovementReviewRequest.objects.filter(company=task.company,pk=task.key.rsplit(':',1)[1])
    result=set()
    for row in rows:
        if row.status in {'PENDING_APPROVAL','RETURNED'}:result|=active_route_roles(row)
    return result

def route_summary(row):
    route=route_for(row)
    return [{'code':code,'label':label,'status':route[code]['status'],'comment':route[code].get('comment',''),'actor':route[code].get('actor','')} for code,label in ROUTE]

def travel_details_complete(row):
    required=(row.planning_position_name,row.position_name,row.construction_object_id,
              row.from_camp_id if row.kind!='ARRIVAL' else row.to_camp_id,row.movement_date,
              row.base_city,row.phone,row.request_number)
    required+= (row.planned_hub_departure_date,)
    return all(required) and (row.kind!='ARRIVAL' or bool(row.employment_kind))


def log(row,actor,action,comment=''):
    return MovementReviewEvent.objects.create(company=row.company,request=row,actor=actor,action=action,comment=comment.strip())


def approval_task_key(row):
    return f'personnel-movement-batch:{row.approval_batch}' if row.approval_batch else f'personnel-movement-approval:{row.pk}'


def approval_rows(row):
    query=MovementReviewRequest.objects.filter(company=row.company)
    return query.filter(approval_batch=row.approval_batch) if row.approval_batch else query.filter(pk=row.pk)


def sync_approval_task(row,user=None):
    task=WorkTask.objects.filter(company=row.company,key=approval_task_key(row)).first()
    if not task:return None
    pending=approval_rows(row).filter(status__in=['PENDING_APPROVAL','RETURNED']).count()
    if pending:
        if task.status!='OPEN':task.status='OPEN';task.started_at=None;task.save(update_fields=['status','started_at','updated_at'])
    elif task.status=='OPEN':state(task,'DONE',user,'Единый маршрут согласования завершён по всем сотрудникам.')
    if pending:
        roles=set()
        for item in approval_rows(row).filter(status__in=['PENDING_APPROVAL','RETURNED']):roles|=active_route_roles(item)
        target=next(iter(roles)) if len(roles)==1 else 'MOVEMENT_PARALLEL'
        if task.target_role!=target or task.assigned_to_id:
            task.target_role=target;task.assigned_to=None;task.save(update_fields=['target_role','assigned_to','updated_at'])
    return task


@transaction.atomic
def cancel_request(user,row,reason):
    row=MovementReviewRequest.objects.select_for_update().get(pk=row.pk,company=user.company)
    if role_code(user) not in {'ADMIN','HR_HEAD','PERSONNEL_DISPATCHER'}:raise PermissionDenied('Отмена заявки недоступна для вашей роли.')
    if row.status in {'AT_HUB','EN_ROUTE','COMPLETED','CANCELLED'} or row.event_id or row.hub_arrived_on:
        raise ValidationError('Заявку нельзя отменить после начала фактического маршрута или повторно.')
    reason=reason.strip()
    if not reason:raise ValidationError('Укажите причину отмены заявки.')
    row.status='CANCELLED';row.cancellation_reason=reason;row.cancelled_by=user;row.cancelled_at=timezone.now();row.travel_status='Отменено'
    row.save(update_fields=['status','cancellation_reason','cancelled_by','cancelled_at','travel_status','updated_at'])
    log(row,user,'Заявка отменена',reason)
    task=sync_approval_task(row,user)
    if task and not approval_rows(row).exclude(status='CANCELLED').exists() and task.status!='SUPERSEDED':state(task,'SUPERSEDED',user,reason)
    return row


@transaction.atomic
def delete_request(user,row):
    row=MovementReviewRequest.objects.select_for_update().get(pk=row.pk,company=user.company)
    if role_code(user)!='ADMIN':raise PermissionDenied('Безвозвратное удаление доступно только администратору.')
    if row.status!='CANCELLED':raise ValidationError('Сначала отмените заявку и укажите причину.')
    if row.event_id or row.hub_arrived_on or row.hub_departed_on or row.final_arrived_on:
        raise ValidationError('Нельзя удалить заявку, по которой уже зафиксировано фактическое движение.')
    if hasattr(row,'recruitment_arrival'):
        raise ValidationError('Нельзя удалить заявку, связанную с подбором. Оставьте её отменённой для сохранения истории.')
    task=WorkTask.objects.filter(company=row.company,key=approval_task_key(row)).first()
    row.delete()
    if task:
        remaining=MovementReviewRequest.objects.filter(company=task.company,approval_batch=row.approval_batch).exists() if row.approval_batch else False
        if remaining:sync_approval_task(MovementReviewRequest.objects.filter(company=task.company,approval_batch=row.approval_batch).first(),user)
        else:task.delete()


def create_approval(row):
    if row.kind not in {'ARRIVAL','DEPARTURE','TRANSFER'} or row.source!='DISPATCHER':return None
    direction={'ARRIVAL':'заезд','DEPARTURE':'выезд','TRANSFER':'перевод'}[row.kind]
    rows=approval_rows(row).select_related('person','from_camp','to_camp').order_by('pk');count=rows.count()
    names='\n'.join(f'• {item.person.display_name}' for item in rows[:30])
    task=ensure(row.company,approval_task_key(row),kind='APPROVAL',
        title=f'Согласовать {direction}: {count} сотрудник' if count==1 else f'Согласовать {direction}: {count} сотрудников',
        description=(f'{names}\nВЖГ: {(row.to_camp if row.kind in {"ARRIVAL","TRANSFER"} else row.from_camp) or "—"}\n'
                     f'Плановая дата: {row.movement_date:%d.%m.%Y}\n'
                     f'Вид: {"Заезд" if row.kind=="ARRIVAL" else "Перевод" if row.kind=="TRANSFER" else "Выезд с увольнением" if row.departure_destination=="DISMISSAL" else "Выезд на межвахтовый отдых"}'),
        target_role='MANAGER',created_by=row.requested_by,due_date=timezone.localdate()+timedelta(days=1),
        construction_object=row.construction_object)
    for item in rows:
        if not item.approval_route:
            item.approval_route=initial_route();item.save(update_fields=['approval_route','updated_at'])
    title=f'Согласовать {direction}: {count} сотрудник' if count==1 else f'Согласовать {direction}: {count} сотрудников'
    description=(f'{names}\nВЖГ: {(row.to_camp if row.kind in {"ARRIVAL","TRANSFER"} else row.from_camp) or "—"}\nПлановая дата: {row.movement_date:%d.%m.%Y}\n\n'
                 'Маршрут: Руководитель проекта → Начальник участка → Специалист перевахты → '
                 'параллельно: Руководитель ОП Дудинка, безопасность, главный механик, фельдшер, персонал, эксперт по труду и заработной плате.')
    if task.title!=title or task.description!=description:
        task.title=title;task.description=description;task.save(update_fields=['title','description','updated_at'])
    return task


@transaction.atomic
def decide(user,row,approved,comment='',sync_task=True):
    row=MovementReviewRequest.objects.select_for_update().get(pk=row.pk,company=user.company)
    actor_role=role_code(user);route=route_for(row)
    pending=active_route_roles(row)
    if actor_role!='ADMIN' and actor_role not in pending:raise PermissionDenied('Сейчас решение ожидается от другого участника маршрута.')
    if row.status not in {'PENDING_APPROVAL','RETURNED'}:raise ValidationError('Заявка уже обработана или находится на другом этапе.')
    if not approved and not comment.strip():raise ValidationError('При возврате укажите причину.')
    step=actor_role if actor_role!='ADMIN' else next(iter(pending),None)
    if not step:raise ValidationError('В маршруте нет этапа, ожидающего решения.')
    if step=='PEREVAKHTA_SPECIALIST' and approved and not travel_details_complete(row):
        raise ValidationError('Сначала заполните обязательные первичные данные поездки. Только после этого можно запустить параллельные согласования.')
    route[step]={'status':'APPROVED' if approved else 'RETURNED','comment':comment.strip(),'actor':user.full_name,'at':timezone.now().isoformat()}
    if approved:
        if step=='MANAGER':route['SITE_CHIEF']['status']='PENDING'
        elif step=='SITE_CHIEF':route['PEREVAKHTA_SPECIALIST']['status']='PENDING'
        elif step=='PEREVAKHTA_SPECIALIST':
            for code in PARALLEL:
                if route[code]['status']=='WAITING':route[code]['status']='PENDING'
    complete=all(route[code]['status']=='APPROVED' for code in PARALLEL)
    row.status=('OPEN' if row.kind=='TRANSFER' else 'PEREVAKHTA') if complete else 'RETURNED' if not approved else 'PENDING_APPROVAL'
    row.approval_route=route;row.approved_by=user if complete else None;row.approved_at=timezone.now() if complete else None;row.approval_comment=comment.strip()
    row.save(update_fields=['status','approval_route','approved_by','approved_at','approval_comment','updated_at'])
    log(row,user,('Согласовано: ' if approved else 'Возвращено: ')+ROLE_LABELS[step],comment)
    task=WorkTask.objects.filter(company=row.company,key=approval_task_key(row)).first()
    if task and sync_task:
        sync_approval_task(row,user)
    return row


@transaction.atomic
def decide_many(user,task,ids,approved,comment='',all_pending=False):
    if role_code(user)!='ADMIN' and role_code(user) not in task_route_roles(task):raise PermissionDenied('Сейчас решение ожидается от другой роли.')
    if not task.key.startswith('personnel-movement-batch:'):raise ValidationError('Это не пакетное согласование перевахты.')
    batch=task.key.split(':',1)[1]
    query=MovementReviewRequest.objects.select_for_update().filter(company=user.company,approval_batch=batch,status__in=['PENDING_APPROVAL','RETURNED'])
    if not all_pending:
        selected={int(value) for value in ids if str(value).isdigit()}
        if not selected:raise ValidationError('Выберите хотя бы одного сотрудника.')
        query=query.filter(pk__in=selected)
    rows=[row for row in query.order_by('pk') if role_code(user)=='ADMIN' or role_code(user) in active_route_roles(row)]
    if not rows:raise ValidationError('Выбранные заявки уже обработаны или недоступны.')
    if not approved and not comment.strip():raise ValidationError('При возврате укажите причину.')
    for row in rows:decide(user,row,approved,comment,sync_task=False)
    sync_approval_task(rows[0],user)
    task_event(task,user,('Согласовано' if approved else 'Возвращено')+f' сотрудников: {len(rows)}',comment)
    return len(rows)


@transaction.atomic
def resubmit(user,row):
    row=MovementReviewRequest.objects.select_for_update().get(pk=row.pk,company=user.company)
    if row.status!='RETURNED':raise ValidationError('На повторное согласование можно отправить только возвращённую заявку.')
    if role_code(user) not in {'ADMIN','PERSONNEL_DISPATCHER','HR_HEAD'}:raise PermissionDenied
    route=route_for(row)
    for code,data in route.items():
        if data.get('status')=='RETURNED':data['status']='PENDING'
    row.status='PENDING_APPROVAL';row.approval_route=route;row.approved_by=None;row.approved_at=None;row.approval_comment=''
    row.save(update_fields=['status','approval_route','approved_by','approved_at','approval_comment','updated_at'])
    task=WorkTask.objects.filter(company=row.company,key=approval_task_key(row)).first()
    if task:
        task.status='OPEN';task.started_at=None;task.save(update_fields=['status','started_at','updated_at']);task_event(task,user,'Повторно отправлено на согласование')
    else:create_approval(row)
    log(row,user,'Повторно отправлено только вернувшим согласующим')


@transaction.atomic
def arrive_hub(user,row,day):
    row=MovementReviewRequest.objects.select_for_update().select_related('person','from_camp').get(pk=row.pk,company=user.company)
    if row.status!='PEREVAKHTA':raise ValidationError('Прибытие в ОП Дудинка фиксируется после согласования.')
    if not travel_details_complete(row):raise ValidationError('Сначала заполните обязательные данные поездки: город, телефон, номер заявки, дату, время и статус.')
    if day>timezone.localdate():raise ValidationError('Фактическое прибытие в ОП Дудинка нельзя отметить будущей датой. Дождитесь события или укажите уже наступившую дату.')
    if row.kind=='DEPARTURE':
        stay=CampStay.objects.select_for_update().filter(company=row.company,person=row.person,camp=row.from_camp,cancelled_at=None,departed_on=None).order_by('-arrived_on').first()
        if not stay:raise ValidationError('У сотрудника нет открытого размещения в указанном ВЖГ.')
        if day<stay.arrived_on:raise ValidationError('Дата прибытия в Дудинку не может быть раньше заезда в ВЖГ.')
        stay.departed_on=day;stay.save(update_fields=['departed_on','updated_at'])
        row.event=HousingEvent.objects.create(company=row.company,person=row.person,date=day,kind='DEPARTURE',departure_destination=row.departure_destination,
            from_camp=row.from_camp,stay=stay,comment='Прибыл в ОП Дудинка. Фактический выезд из ВЖГ.',recorded_by=user)
    row.hub_arrived_on=day;row.status='AT_HUB';row.travel_status='В ОП Дудинка'
    row.save(update_fields=['event','hub_arrived_on','status','travel_status','updated_at'])
    log(row,user,'Прибыл в ОП Дудинка','Зафиксирован фактический выезд из ВЖГ.' if row.kind=='DEPARTURE' else 'Фактический заезд в ВЖГ ещё не выполнен.')


@transaction.atomic
def update_hub(user,row,*,tickets_ready=False,clearance_document=None):
    row=MovementReviewRequest.objects.select_for_update().get(pk=row.pk,company=user.company)
    if row.status!='AT_HUB':raise ValidationError('Документы и билеты оформляются после прибытия в ОП Дудинка.')
    fields=['updated_at']
    if tickets_ready and not row.tickets_ready:
        row.tickets_ready=True;row.tickets_ready_at=timezone.now();fields+=['tickets_ready','tickets_ready_at'];log(row,user,'Билеты приобретены')
    if clearance_document:
        row.clearance_document=clearance_document;row.clearance_uploaded_at=timezone.now();fields+=['clearance_document','clearance_uploaded_at'];log(row,user,'Прикреплён обходной лист',clearance_document.name)
    row.save(update_fields=fields)


@transaction.atomic
def leave_hub(user,row,day):
    row=MovementReviewRequest.objects.select_for_update().select_related('person__employee').get(pk=row.pk,company=user.company)
    if row.status!='AT_HUB':raise ValidationError('Выезд из Дудинки фиксируется только для сотрудника в ОП Дудинка.')
    if not row.tickets_ready or not row.clearance_document:raise ValidationError('Перед выездом должны быть оформлены билеты и прикреплён обходной лист.')
    if day<row.hub_arrived_on:raise ValidationError('Фактический выезд из ОП Дудинка не может быть раньше прибытия в Дудинку.')
    if day>timezone.localdate():raise ValidationError('Фактический выезд из ОП Дудинка нельзя отметить будущей датой. Плановая дата указана в заявке; факт внесите после выезда.')
    row.hub_departed_on=day;row.status='EN_ROUTE' if row.kind=='ARRIVAL' else 'COMPLETED';row.travel_status='В пути в ВЖГ' if row.kind=='ARRIVAL' else 'Убыл из ОП Дудинка';row.reviewed_by=user;row.reviewed_at=timezone.now()
    row.save(update_fields=['hub_departed_on','status','travel_status','reviewed_by','reviewed_at','updated_at'])
    if row.departure_destination=='DISMISSAL' and row.person.employee_id:
        employee=row.person.employee;employee.is_active=False;employee.save(update_fields=['is_active','updated_at'])
    log(row,user,'Выехал из ОП Дудинка','Сотрудник направлен в ВЖГ.' if row.kind=='ARRIVAL' else 'Заявка завершена. '+('Сотрудник уволен.' if row.departure_destination=='DISMISSAL' else 'Сотрудник переведён на межвахтовый отдых.'))


@transaction.atomic
def arrive_camp(user,row,day):
    row=MovementReviewRequest.objects.select_for_update().select_related('person__employee','to_camp').get(pk=row.pk,company=user.company)
    if row.kind!='ARRIVAL' or row.status!='EN_ROUTE':raise ValidationError('Фактический заезд доступен после отправления сотрудника из ОП Дудинка.')
    if day<row.hub_departed_on:raise ValidationError('Фактическое прибытие в ВЖГ не может быть раньше выезда из ОП Дудинка.')
    if day>timezone.localdate():raise ValidationError('Фактическое прибытие в ВЖГ нельзя отметить будущей датой. Внесите факт после прибытия сотрудника.')
    from . import housing
    housing.move(user,'ARRIVAL',row.to_camp,day,[row.person],comment=f'Заявка на заезд №{row.pk}. Прибытие через ОП Дудинка.')
    event=HousingEvent.objects.filter(company=row.company,person=row.person,kind='ARRIVAL',date=day,to_camp=row.to_camp).order_by('-pk').first()
    row.event=event;row.final_arrived_on=day;row.status='COMPLETED';row.travel_status='Прибыл в ВЖГ';row.reviewed_by=user;row.reviewed_at=timezone.now()
    row.save(update_fields=['event','final_arrived_on','status','travel_status','reviewed_by','reviewed_at','updated_at'])
    prep=getattr(row,'recruitment_arrival',None)
    if prep:
        prep.actual_stay=event.stay;prep.status='ARRIVED';prep.revision+=1;prep.save(update_fields=['actual_stay','status','revision','updated_at'])
        from .recruitment import audit,touch
        touch(prep.candidate.request);audit(user,prep.candidate.request,'Фактический заезд в ВЖГ подтверждён.',prep.candidate)
    log(row,user,'Прибыл в ВЖГ','Фактический заезд зафиксирован. Заявка завершена.')
