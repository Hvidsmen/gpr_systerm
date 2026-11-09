from datetime import timedelta

from django.core.exceptions import ValidationError,PermissionDenied
from django.db import transaction
from django.utils import timezone

from core.permissions import role_code
from apps.planning.models import WorkTask
from apps.planning.task_services import ensure,event as task_event,state
from .models import CampStay,HousingEvent,MovementReviewEvent,MovementReviewRequest


def log(row,actor,action,comment=''):
    return MovementReviewEvent.objects.create(company=row.company,request=row,actor=actor,action=action,comment=comment.strip())


def create_approval(row):
    if row.kind not in {'ARRIVAL','DEPARTURE'} or row.source!='DISPATCHER':return None
    direction='заезд' if row.kind=='ARRIVAL' else 'выезд'
    task=ensure(row.company,f'personnel-movement-approval:{row.pk}',kind='APPROVAL',
        title=f'Согласовать {direction}: {row.person.display_name}',
        description=(f'{row.person.display_name}\nВЖГ: {(row.to_camp if row.kind=="ARRIVAL" else row.from_camp) or "—"}\n'
                     f'Плановая дата: {row.movement_date:%d.%m.%Y}\n'
                     f'Вид: {"Заезд" if row.kind=="ARRIVAL" else "Выезд с увольнением" if row.departure_destination=="DISMISSAL" else "Выезд на межвахтовый отдых"}'),
        target_role='MANAGER',created_by=row.requested_by,due_date=timezone.localdate()+timedelta(days=1),
        construction_object=row.construction_object)
    return task


@transaction.atomic
def decide(user,row,approved,comment=''):
    row=MovementReviewRequest.objects.select_for_update().get(pk=row.pk,company=user.company)
    if role_code(user) not in {'ADMIN','MANAGER'}:raise PermissionDenied('Согласование доступно руководителю проекта.')
    if row.status!='PENDING_APPROVAL':raise ValidationError('Заявка уже обработана или находится на другом этапе.')
    if not approved and not comment.strip():raise ValidationError('При возврате укажите причину.')
    row.status='PEREVAKHTA' if approved else 'RETURNED';row.approved_by=user;row.approved_at=timezone.now();row.approval_comment=comment.strip()
    row.save(update_fields=['status','approved_by','approved_at','approval_comment','updated_at'])
    log(row,user,'Согласовано руководителем' if approved else 'Возвращено на доработку',comment)
    task=WorkTask.objects.filter(company=row.company,key=f'personnel-movement-approval:{row.pk}').first()
    if task:state(task,'DONE' if approved else 'SUPERSEDED',user,comment)
    return row


@transaction.atomic
def resubmit(user,row):
    row=MovementReviewRequest.objects.select_for_update().get(pk=row.pk,company=user.company)
    if row.status!='RETURNED':raise ValidationError('На повторное согласование можно отправить только возвращённую заявку.')
    if role_code(user) not in {'ADMIN','PERSONNEL_DISPATCHER','HR_HEAD'}:raise PermissionDenied
    row.status='PENDING_APPROVAL';row.approved_by=None;row.approved_at=None;row.approval_comment=''
    row.save(update_fields=['status','approved_by','approved_at','approval_comment','updated_at'])
    task=WorkTask.objects.filter(company=row.company,key=f'personnel-movement-approval:{row.pk}').first()
    if task:
        task.status='OPEN';task.started_at=None;task.save(update_fields=['status','started_at','updated_at']);task_event(task,user,'Повторно отправлено на согласование')
    else:create_approval(row)
    log(row,user,'Повторно отправлено на согласование')


@transaction.atomic
def arrive_hub(user,row,day):
    row=MovementReviewRequest.objects.select_for_update().select_related('person','from_camp').get(pk=row.pk,company=user.company)
    if row.status!='PEREVAKHTA':raise ValidationError('Прибытие в ОП Дудинка фиксируется после согласования.')
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
