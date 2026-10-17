"""Company-scoped task inbox. Workflow decisions remain in their original services."""
from datetime import timedelta
from uuid import uuid4
from django.core.exceptions import ValidationError,PermissionDenied
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from core.permissions import ROLES,role_code,require_roles
from apps.accounts.models import User
from .models import WorkTask,WorkTaskEvent,DailyTaskCursor,EstimateReviewTask,GlobalPlanVersion,CarryoverReviewTask

ACTIVE={'OPEN','IN_REVIEW'}


def event(task,actor,action,text=''):
    WorkTaskEvent.objects.create(company_id=task.company_id,task=task,actor=actor,actor_name=actor.full_name if actor else 'Система',action=action,text=text[:8000])


def state(task,status,actor=None,text=''):
    if task.status!=status:
        task.status=status;task.save(update_fields=['status','updated_at']);event(task,actor,task.get_status_display(),text)


def ensure(company,key,**defaults):
    task,created=WorkTask.objects.get_or_create(company=company,key=key,defaults=defaults)
    if created:event(task,None,'Создано',f'{task.description}\nИсполнитель: {task.assigned_to or task.target_role}')
    return task


def visible(user):
    require_roles(user,ROLES)
    q=WorkTask.objects.filter(company_id=user.company_id)
    if role_code(user) not in {'ADMIN','OBSERVER'}:
        q=q.filter(Q(assigned_to=user)|Q(created_by=user)|Q(assigned_to__isnull=True,target_role=role_code(user)))
    return q


def is_executor(user,task):
    return task.assigned_to_id==user.pk or (not task.assigned_to_id and task.target_role==role_code(user)) or role_code(user)=='ADMIN'


def estimate_task(estimate):
    obj=estimate.version if estimate.version_id else estimate.schedule
    object_id=estimate.version.construction_object_id if estimate.version_id else estimate.schedule.source.construction_object_id
    active=estimate.work_tasks.filter(kind='ESTIMATE',status__in=ACTIVE).order_by('-pk').first()
    previous=estimate.work_tasks.filter(kind='ESTIMATE').order_by('-pk').first()
    key=active.key if active else previous.key if previous and estimate.status!='OPEN' else f'estimate:{estimate.pk}:phase:{obj.approval_round if estimate.version_id else obj.revision}:{obj.status}'
    task=ensure(estimate.company,key,kind='ESTIMATE',title='Проверить цены, названия и состав работ',assigned_to=estimate.assigned_to,created_by=estimate.created_by,version_id=estimate.version_id,schedule_id=estimate.schedule_id,estimate=estimate,construction_object_id=object_id,due_date=timezone.localdate()+timedelta(days=1))
    if task.assigned_to_id!=estimate.assigned_to_id:
        task.assigned_to_id=estimate.assigned_to_id;task.save(update_fields=['assigned_to']);event(task,None,'Переназначено')
    desired='OPEN' if estimate.status=='OPEN' else 'DONE'
    if obj.status in {'APPROVED','COMPLETED','APPLIED','ARCHIVED'} and estimate.status=='OPEN':desired='SUPERSEDED'
    if desired=='OPEN' and task.status=='DONE':state(task,'OPEN',text='Повторная проверка')
    elif desired!='OPEN':state(task,desired,text=estimate.comment)
    return task


def sync_version(version):
    from .approval_workflow import ready
    review=version.review_rounds.defer('snapshot').filter(number=version.approval_round).first()
    for task in WorkTask.objects.filter(version=version,review__isnull=False,status__in=ACTIVE).exclude(kind='REMARK'):
        if task.review_id!=(review.pk if review else None) or version.status not in {'SUBMITTED','APPROVED','COMPLETED'}:state(task,'SUPERSEDED',text='Раунд согласования заменен или возвращен на доработку.')
    if not review or version.status not in {'SUBMITTED','APPROVED','COMPLETED'}:return
    decisions={d.section:d for d in review.decisions.filter(action='APPROVE')}
    if version.status in {'APPROVED','COMPLETED'}:
        mapping={'PRODUCTION_HEAD':'PRODUCTION','HR_HEAD':'HR','TECH_HEAD':'TECH','CEO':'CEO','ESTIMATOR':'ESTIMATE'}
        for task in WorkTask.objects.filter(review=review,status__in=ACTIVE).exclude(kind='REMARK'):
            decision=decisions.get(mapping.get(task.target_role))
            state(task,'DONE' if decision else 'SUPERSEDED',decision.actor if decision else None,'План уже утвержден.')
        return
    for section,label,role in [('PRODUCTION','Работы','PRODUCTION_HEAD'),('HR','Люди','HR_HEAD'),('TECH','Техника и ГСМ','TECH_HEAD')]:
        people=list(User.objects.filter(company_id=version.company_id,role__code=role,is_active=True)[:2])
        task=ensure(version.company,f'approval:{review.pk}:{section}',kind='APPROVAL',title=f'Согласовать план: {label}',target_role=role,assigned_to=people[0] if len(people)==1 else None,created_by=review.submitted_by,version=version,review=review,construction_object_id=version.construction_object_id,due_date=timezone.localdate()+timedelta(days=2),description=f'Раунд {review.number}. Раздел: {label}.')
        if task.status=='OPEN' and task.assigned_to_id and (not task.assigned_to.is_active or role_code(task.assigned_to)!=role):
            task.assigned_to=people[0] if len(people)==1 else None;task.save(update_fields=['assigned_to']);event(task,None,'Переназначено','Изменился ответственный по роли.')
        if section in decisions:state(task,'DONE',decisions[section].actor,decisions[section].comment)
    latest=version.estimate_reviews.order_by('-pk').first()
    if latest:
        task=estimate_task(latest)
        if not task.review_id and task.status=='OPEN':task.review=review;task.save(update_fields=['review'])
    if 'ESTIMATE' not in decisions and (not latest or latest.status!='OPEN'):
        ensure(version.company,f'approval:{review.pk}:ESTIMATE',kind='ESTIMATE',title='Проверить и согласовать план сметчиком',target_role='ESTIMATOR',created_by=review.submitted_by,version=version,review=review,construction_object_id=version.construction_object_id,due_date=timezone.localdate()+timedelta(days=2))
    for task in WorkTask.objects.filter(review=review,kind='ESTIMATE',estimate__isnull=True):
        if 'ESTIMATE' in decisions:state(task,'DONE',decisions['ESTIMATE'].actor)
        elif latest and latest.status=='OPEN':state(task,'SUPERSEDED',text='Проверка назначена конкретному сметчику.')
    if ready(review):
        people=list(User.objects.filter(company_id=version.company_id,role__code='CEO',is_active=True)[:2])
        task=ensure(version.company,f'approval:{review.pk}:CEO',kind='APPROVAL',title='Утвердить план',target_role='CEO',assigned_to=people[0] if len(people)==1 else None,created_by=review.submitted_by,version=version,review=review,construction_object_id=version.construction_object_id,due_date=timezone.localdate()+timedelta(days=2))
        if task.status=='OPEN' and task.assigned_to_id and (not task.assigned_to.is_active or role_code(task.assigned_to)!='CEO'):
            task.assigned_to=people[0] if len(people)==1 else None;task.save(update_fields=['assigned_to']);event(task,None,'Переназначено','Изменился ответственный по роли.')
        if 'CEO' in decisions:state(task,'DONE',decisions['CEO'].actor,decisions['CEO'].comment)


def create_repair(version,actor,comment):
    planner=version.created_by
    if not planner or not planner.is_active:planner=User.objects.filter(company=version.company,role__code='PLANNER',is_active=True).first()
    if not planner:return
    ensure(version.company,f'repair:{version.pk}:{version.approval_round}',kind='MANUAL',title='Доработать план и повторно отправить на согласование',description=comment,assigned_to=planner,created_by=actor,version=version,construction_object_id=version.construction_object_id,due_date=timezone.localdate()+timedelta(days=1))


def sync_company(company):
    for estimate in EstimateReviewTask.objects.filter(company=company).select_related('version','schedule__source').defer('version__snapshot','schedule__source__snapshot').iterator():estimate_task(estimate)
    for version in GlobalPlanVersion.objects.filter(company=company,status__in=['SUBMITTED','APPROVED','COMPLETED']).defer('snapshot').iterator():sync_version(version)
    for record in CarryoverReviewTask.objects.filter(company=company,due_date__lte=timezone.localdate()).select_related('version','correction_version').defer('version__snapshot','correction_version__snapshot'):
        task=ensure(company,f'carryover:{record.pk}',kind='CARRYOVER',title='Уточнить прогнозные остатки по закрытому факту',assigned_to=record.assigned_to,version=record.version,carryover=record,construction_object_id=record.version.construction_object_id,due_date=record.due_date)
        if record.correction_version_id and record.correction_version.status in {'APPROVED','COMPLETED'}:state(task,'DONE')


@transaction.atomic
def generate_daily(user,*,today=None):
    if not user.is_active or not user.company_id or role_code(user)!='DISPATCHER':return 0
    today=today or timezone.localdate();yesterday=today-timedelta(days=1);count=0
    User.objects.select_for_update().only('pk').get(pk=user.pk)
    objects=list(user.assigned_objects.filter(company_id=user.company_id))
    ids=[o.pk for o in objects]
    DailyTaskCursor.objects.filter(user=user).exclude(construction_object_id__in=ids).delete()
    for task in WorkTask.objects.filter(company=user.company,assigned_to=user,kind='FACT',status__in=ACTIVE).exclude(construction_object_id__in=ids):state(task,'SUPERSEDED',text='Объект больше не закреплен за диспетчером.')
    for obj in objects:
        cursor,_=DailyTaskCursor.objects.get_or_create(user=user,construction_object=obj,defaults={'last_date':yesterday-timedelta(days=1)})
        day=cursor.last_date+timedelta(days=1)
        while day<=yesterday:
            key=f'fact:{user.pk}:{obj.pk}:{day.isoformat()}'
            task=ensure(user.company,key,kind='FACT',title=f'Внести факт за {day:%d.%m.%Y} · {obj.name}'[:255],assigned_to=user,construction_object=obj,fact_date=day,due_date=day+timedelta(days=1),description='Проверьте работы, людей, технику и ГСМ. Сохраните данные и отдельно подтвердите полноту факта.')
            count+=1;day+=timedelta(days=1)
        if cursor.last_date<yesterday:cursor.last_date=yesterday;cursor.save(update_fields=['last_date'])
    return count


def require_no_remarks(estimate):
    scope=Q(estimate=estimate) | (Q(version_id=estimate.version_id) if estimate.version_id else Q(schedule_id=estimate.schedule_id))
    if WorkTask.objects.filter(scope,company=estimate.company,kind='REMARK',status__in=ACTIVE).exists():raise ValidationError('Сначала проверьте и закройте все замечания к плану.')


@transaction.atomic
def create_remark(user,estimate,planner,title,text,*,work_id=None,due=None):
    from . import estimate_review
    estimate=estimate_review.lock_task(user,estimate)
    if planner.company_id!=user.company_id or not planner.is_active or role_code(planner) not in {'PLANNER','ADMIN'}:raise ValidationError('Выберите планировщика своей компании.')
    if not title.strip() or not text.strip():raise ValidationError('Укажите заголовок и что нужно исправить.')
    parent=estimate_task(estimate)
    if work_id:estimate_review.review_work(user,estimate,work_id,False,text)
    else:estimate_review.return_to_draft(estimate,user,text)
    task=ensure(user.company,f'remark:{uuid4()}',kind='REMARK',title=title,description=text,assigned_to=planner,created_by=user,estimate=estimate,version_id=estimate.version_id,schedule_id=estimate.schedule_id,work_id=work_id,parent=parent,construction_object_id=parent.construction_object_id,due_date=due or timezone.localdate()+timedelta(days=1))
    event(parent,user,'Замечание планировщику',f'Задание №{task.pk}: {title}')
    fresh=EstimateReviewTask.objects.select_related('version','schedule__source').defer('version__snapshot','schedule__source__snapshot').get(pk=estimate.pk)
    estimate_task(fresh)
    return task


@transaction.atomic
def claim_estimate(user,task):
    require_roles(user,{'ESTIMATOR'})
    task=WorkTask.objects.select_for_update().get(pk=task.pk,company=user.company)
    if task.kind!='ESTIMATE' or task.status!='OPEN' or task.estimate_id or not is_executor(user,task):raise PermissionDenied
    version=GlobalPlanVersion.objects.select_for_update().defer('snapshot').get(pk=task.version_id,company=user.company)
    if version.status!='SUBMITTED' or not task.review_id or task.review.number!=version.approval_round:raise ValidationError('Раунд согласования уже изменился.')
    estimate=EstimateReviewTask.objects.create(company=user.company,assigned_to=user,created_by=task.created_by or version.created_by,version=version)
    task.estimate=estimate;task.assigned_to=user;task.save(update_fields=['estimate','assigned_to'])
    # Keep one inbox row when the normal estimator service synchronizes it later.
    task.key=f'estimate:{estimate.pk}';task.save(update_fields=['key']);event(task,user,'Принято в работу')
    return estimate


@transaction.atomic
def discussion_action(user,task,action,text=''):
    if role_code(user)=='OBSERVER':raise PermissionDenied
    task=visible(user).select_for_update().get(pk=task.pk)
    if action=='comment':
        if not text.strip():raise ValidationError('Введите комментарий.')
        if role_code(user)=='OBSERVER':raise PermissionDenied
        event(task,user,'Комментарий',text);return task
    if task.kind not in {'REMARK','MANUAL'} or task.status not in ACTIVE:raise ValidationError('Это действие недоступно для задания.')
    if action=='resolve':
        if not is_executor(user,task):raise PermissionDenied
        if not text.strip():raise ValidationError('Опишите, что исправлено.')
        state(task,'IN_REVIEW',user,text)
    elif action=='cancel':
        if user.pk!=task.created_by_id:raise PermissionDenied('Задание отменяет его автор.')
        if not text.strip():raise ValidationError('Укажите причину отмены.')
        state(task,'SUPERSEDED',user,text)
    elif action in {'accept','reopen'}:
        if user.pk!=task.created_by_id:raise PermissionDenied('Задание закрывает его автор.')
        if task.status!='IN_REVIEW':raise ValidationError('Исполнитель еще не передал результат на проверку.')
        if action=='reopen' and not text.strip():raise ValidationError('Укажите, что еще нужно исправить.')
        state(task,'DONE' if action=='accept' else 'OPEN',user,text)
        if task.parent_id:event(task.parent,user,'Проверка замечания',f'№{task.pk}: {task.get_status_display()}. {text}')
    else:raise ValidationError('Неизвестное действие.')
    return task


def invalidate_fact(company_id,object_id,day):
    for task in WorkTask.objects.filter(company_id=company_id,kind='FACT',construction_object_id=object_id,fact_date=day,status='DONE'):state(task,'OPEN',text='Факт изменен после подтверждения. Повторно проверьте полноту данных.')


def validate_fact(task):
    from .report_services import select_sources
    from apps.production.models import DailyFact,LaborFact,EquipmentFact,FuelFact,DayActuation
    user=task.assigned_to;obj=task.construction_object;day=task.fact_date
    if not user or not obj or obj.company_id!=task.company_id or not user.assigned_objects.filter(pk=obj.pk,company_id=task.company_id).exists():raise PermissionDenied('Объект не закреплен за исполнителем.')
    sources=select_sources(user,{'start':day,'end':day,'mode':'latest'},[obj]).get(obj.pk,[])
    snapshot=sources[0].snapshot if sources else {}
    actual=set(DailyFact.objects.filter(company_id=task.company_id,date=day,project_work__section__construction_object=obj).values_list('project_work_id','work_item_id'))
    actuated=DayActuation.objects.filter(company_id=task.company_id,construction_object=obj,date=day).first()
    if actuated:
        actuated.full_clean()
        if DailyFact.objects.filter(company_id=task.company_id,date=day,project_work__section__construction_object=obj).exclude(actual_quantity=0).exists():raise ValidationError('При актировке факт работ должен быть нулевым.')
    else:
        for spec in snapshot.get('works',[]):
            for row in spec.get('plans',[]):
                if row['date']==day.isoformat() and (spec['id'],row.get('item_id')) not in actual:raise ValidationError(f'Не заполнен факт работы «{spec["name"]}». Ноль необходимо внести явно.')
    configs=[('labor',LaborFact,('brigade_id',)),('equipment',EquipmentFact,('equipment_type_id','equipment_number')),('fuel',FuelFact,('fuel_type',))]
    for kind,model,fields in configs:
        actual=set(model.objects.filter(company_id=task.company_id,construction_object=obj,date=day).values_list(*fields))
        for row in snapshot.get('resources',{}).get(kind,[]):
            if row['date']==day.isoformat() and tuple(row.get(f,'' if f=='equipment_number' else None) for f in fields) not in actual:raise ValidationError('Не заполнен факт по запланированным людям, технике или ГСМ. Нулевые значения вносятся явно.')


@transaction.atomic
def complete_fact(user,task,confirmed):
    from core.permissions import FACT_ROLES
    require_roles(user,FACT_ROLES)
    task=visible(user).select_for_update().select_related('assigned_to','construction_object').get(pk=task.pk)
    if task.kind!='FACT' or task.status!='OPEN' or not is_executor(user,task):raise PermissionDenied
    if set(confirmed)!={'works','labor','equipment','fuel'}:raise ValidationError('Подтвердите проверку всех четырех разделов факта.')
    validate_fact(task);state(task,'DONE',user,'Проверены работы, люди, техника и ГСМ.');return task
