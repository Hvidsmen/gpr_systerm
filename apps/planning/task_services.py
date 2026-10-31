"""Company-scoped task inbox. Workflow decisions remain in their original services."""
from datetime import timedelta
from uuid import uuid4
from django.core.exceptions import ValidationError,PermissionDenied
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from core.permissions import ROLES,role_code,require_roles,DISPATCHER_ROLES
from core.plan_approval import DEPARTMENTS,ROLE_SECTIONS
from apps.accounts.models import User
from .models import WorkTask,WorkTaskEvent,DailyTaskCursor,EstimateReviewTask,GlobalPlanVersion,CarryoverReviewTask

ACTIVE={'OPEN','IN_REVIEW'}


def filter_scope(query, user, scope):
    if scope == 'outgoing':
        return query.filter(created_by=user)
    if scope == 'all' and role_code(user) in {'ADMIN', 'OBSERVER'}:
        return query
    return query.filter(Q(assigned_to=user) | Q(assigned_to__isnull=True, target_role=role_code(user)))


def filter_role(query, code):
    if not code:return query
    return query.filter(Q(assigned_to__role__code=code) | Q(assigned_to__isnull=True,target_role=code))


def filter_status(query, status):
    if status == 'not_taken':
        return query.filter(status='OPEN', started_at__isnull=True)
    if status == 'in_progress':
        return query.filter(Q(status='OPEN', started_at__isnull=False) | Q(status='IN_REVIEW'))
    if status == 'history':
        return query.exclude(status__in=ACTIVE)
    if status == 'active':
        return query.filter(status__in=ACTIVE)
    if status == 'overdue':
        return query.filter(status__in=ACTIVE, due_date__lt=timezone.localdate())
    if status in dict(WorkTask._meta.get_field('status').choices):
        return query.filter(status=status)
    return query if status == 'all' else query.none()


@transaction.atomic
def take(user, task):
    require_roles(user, ROLES - {'OBSERVER'})
    from django.shortcuts import get_object_or_404
    task = get_object_or_404(visible(user).select_for_update(), pk=task.pk)
    if not is_executor(user, task):
        raise PermissionDenied
    if task.status != 'OPEN':
        raise ValidationError('Задание уже завершено или передано на проверку.')
    if task.kind == 'ESTIMATE' and not task.estimate_id:
        raise ValidationError('Используйте действие «Взять проверку сметчика».')
    if task.started_at:
        return task
    if not task.assigned_to_id:
        task.assigned_to = user
    task.started_at = timezone.now()
    task.save(update_fields=['started_at', 'assigned_to', 'updated_at'])
    event(task, user, 'Принято в работу')
    return task


def event(task,actor,action,text=''):
    WorkTaskEvent.objects.create(company_id=task.company_id,task=task,actor=actor,actor_name=actor.full_name if actor else 'Система',action=action,text=text[:8000])


def state(task,status,actor=None,text=''):
    if task.status=='CLOSED_ADMIN' or task.status==status:return
    with transaction.atomic():
        current=WorkTask.objects.select_for_update().filter(pk=task.pk,company_id=task.company_id).only('status').first()
        if current is None:return
        task.status=current.status
        if task.status=='CLOSED_ADMIN' or task.status==status:return
        # Returning a result for correction keeps it in work; a new workflow
        # round after a terminal state needs a fresh acceptance.
        if status == 'OPEN' and current.status not in ACTIVE:
            task.started_at = None
            task.save(update_fields=['started_at'])
        task.status=status;task.save(update_fields=['status','updated_at']);event(task,actor,task.get_status_display(),text)


def ensure(company,key,**defaults):
    task,created=WorkTask.objects.get_or_create(company=company,key=key,defaults=defaults)
    if created:event(task,None,'Создано',f'{task.description}\nИсполнитель: {task.assigned_to or task.target_role}')
    return task


def visible(user):
    require_roles(user,ROLES)
    from core.permissions import scope_queryset
    q=scope_queryset(WorkTask.objects.all(),user)
    from core.permissions import CATALOG_MANAGER_ROLES
    if role_code(user) in CATALOG_MANAGER_ROLES:
        return q.filter(kind='CATALOGUE',target_role=role_code(user)).filter(Q(assigned_to=user)|Q(assigned_to__isnull=True))
    if role_code(user)=='PLANNER':
        q=q.filter(Q(construction_object__in=user.assigned_objects.filter(company=user.company))|Q(construction_object__isnull=True))
    if role_code(user) not in {'ADMIN','OBSERVER'}:
        q=q.filter(Q(assigned_to=user)|Q(created_by=user)|Q(assigned_to__isnull=True,target_role=role_code(user)))
    return q


def is_executor(user,task):
    return task.assigned_to_id==user.pk or (not task.assigned_to_id and task.target_role==role_code(user)) or role_code(user)=='ADMIN'


def estimate_task(estimate,*,restart_closed=False):
    obj=estimate.version if estimate.version_id else estimate.schedule
    object_id=estimate.version.construction_object_id if estimate.version_id else estimate.schedule.construction_object_id
    active=estimate.work_tasks.filter(kind='ESTIMATE',status__in=ACTIVE).order_by('-pk').first()
    previous=estimate.work_tasks.filter(kind='ESTIMATE').order_by('-pk').first()
    key=active.key if active else previous.key if previous and estimate.status!='OPEN' else f'estimate:{estimate.pk}:phase:{obj.approval_round if estimate.version_id else obj.revision}:{obj.status}'
    defaults=dict(kind='ESTIMATE',title='Проверить цены работ',assigned_to=estimate.assigned_to,created_by=estimate.created_by,version_id=estimate.version_id,schedule_id=estimate.schedule_id,estimate=estimate,construction_object_id=object_id,due_date=timezone.localdate()+timedelta(days=1))
    task=ensure(estimate.company,key,**defaults)
    if task.status in ACTIVE and task.title!=defaults['title']:
        task.title=defaults['title'];task.save(update_fields=['title'])
    if task.status=='CLOSED_ADMIN':
        if not restart_closed:return task
        task=ensure(estimate.company,f'{key}:retry:{uuid4()}',**defaults)
    if task.assigned_to_id!=estimate.assigned_to_id:
        task.assigned_to_id=estimate.assigned_to_id;task.save(update_fields=['assigned_to']);event(task,None,'Переназначено')
    desired='OPEN' if estimate.status=='OPEN' else 'DONE'
    if obj.status in {'APPROVED','COMPLETED','APPLIED','ARCHIVED'} and estimate.status=='OPEN':desired='SUPERSEDED'
    if desired=='OPEN' and task.status=='DONE':state(task,'OPEN',text='Повторная проверка')
    elif desired!='OPEN':state(task,desired,text=estimate.comment)
    return task


def sync_version(version):
    from .smg_rebase import sync as sync_rebase
    for record in CarryoverReviewTask.objects.filter(company_id=version.company_id,correction_version=version).select_related('version','correction_version','assigned_to'):sync_rebase(record)
    from .approval_workflow import ready
    review=version.review_rounds.defer('snapshot').filter(number=version.approval_round).first()
    for task in WorkTask.objects.filter(version=version,review__isnull=False,status__in=ACTIVE).exclude(kind='REMARK'):
        if task.review_id!=(review.pk if review else None) or version.status not in {'SUBMITTED','APPROVED','COMPLETED'}:state(task,'SUPERSEDED',text='Раунд согласования заменен или возвращен на доработку.')
    if not review or version.status not in {'SUBMITTED','APPROVED','COMPLETED'}:return
    decisions={d.section:d for d in review.decisions.filter(action='APPROVE')}
    if version.status in {'APPROVED','COMPLETED'}:
        mapping={**ROLE_SECTIONS,'CEO':'CEO','ESTIMATOR':'ESTIMATE'}
        for task in WorkTask.objects.filter(review=review,status__in=ACTIVE).exclude(kind='REMARK'):
            decision=decisions.get(mapping.get(task.target_role))
            state(task,'DONE' if decision else 'SUPERSEDED',decision.actor if decision else None,'План уже утвержден.')
        return
    if 'ESTIMATE' not in decisions:
        for task in WorkTask.objects.filter(review=review,kind='APPROVAL',status__in=ACTIVE):
            state(task,'SUPERSEDED',text='Сначала требуется проверка сметчика.')
    else:
        for section,label,role,_ in DEPARTMENTS:
            people=list(User.objects.filter(company_id=version.company_id,role__code=role,is_active=True)[:2])
            task=ensure(version.company,f'approval:{review.pk}:{section}',kind='APPROVAL',title=f'Согласовать план: {label}',target_role=role,assigned_to=people[0] if len(people)==1 else None,created_by=review.submitted_by,version=version,review=review,construction_object_id=version.construction_object_id,due_date=timezone.localdate()+timedelta(days=2),description=f'Раунд {review.number}. Раздел: {label}.')
            if task.status=='OPEN' and task.assigned_to_id and (not task.assigned_to.is_active or role_code(task.assigned_to)!=role):
                task.assigned_to=people[0] if len(people)==1 else None;task.save(update_fields=['assigned_to']);event(task,None,'Переназначено','Изменился ответственный по роли.')
            if task.status=='SUPERSEDED' and section not in decisions:state(task,'OPEN',text='Проверка сметчика завершена.')
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
    if not ready(review):
        for old in WorkTask.objects.filter(review=review,target_role='CEO',kind='APPROVAL',status__in=ACTIVE):state(old,'SUPERSEDED',text='Требуются согласования всех служб, включая Службу СБО.')
    if ready(review):
        people=list(User.objects.filter(company_id=version.company_id,role__code='CEO',is_active=True)[:2])
        task=ensure(version.company,f'approval:{review.pk}:CEO',kind='APPROVAL',title='Утвердить план',target_role='CEO',assigned_to=people[0] if len(people)==1 else None,created_by=review.submitted_by,version=version,review=review,construction_object_id=version.construction_object_id,due_date=timezone.localdate()+timedelta(days=2))
        if task.status=='OPEN' and task.assigned_to_id and (not task.assigned_to.is_active or role_code(task.assigned_to)!='CEO'):
            task.assigned_to=people[0] if len(people)==1 else None;task.save(update_fields=['assigned_to']);event(task,None,'Переназначено','Изменился ответственный по роли.')
        if task.status=='SUPERSEDED' and 'CEO' not in decisions:state(task,'OPEN',text='Все службы согласовали.')
        if 'CEO' in decisions:state(task,'DONE',decisions['CEO'].actor,decisions['CEO'].comment)


def create_repair(version,actor,comment):
    planner=version.created_by
    if not planner or not planner.is_active:planner=User.objects.filter(company=version.company,role__code='PLANNER',is_active=True).first()
    if not planner:return
    ensure(version.company,f'repair:{version.pk}:{version.approval_round}',kind='MANUAL',title='Доработать план и повторно отправить на согласование',description=comment,assigned_to=planner,created_by=actor,version=version,construction_object_id=version.construction_object_id,due_date=timezone.localdate()+timedelta(days=1))


def sync_company(company):
    from apps.resources.catalogue_tasks import sync_company as sync_catalogue_tasks
    sync_catalogue_tasks(company)
    for estimate in EstimateReviewTask.objects.filter(company=company).select_related('version','schedule__source').defer('version__snapshot','schedule__source__snapshot').iterator():estimate_task(estimate)
    from .daily_schedule_approval import sync as sync_daily
    from .models import DailySchedule
    for schedule in DailySchedule.objects.filter(company=company).filter(Q(status__in=['SUBMITTED','APPROVED'])|Q(status='DRAFT',estimate_reviews__status='APPROVED')).distinct().select_related('source').defer('source__snapshot').iterator():sync_daily(schedule)
    for version in GlobalPlanVersion.objects.filter(company=company,status__in=['SUBMITTED','APPROVED','COMPLETED']).defer('snapshot').iterator():sync_version(version)
    for record in CarryoverReviewTask.objects.filter(company=company,due_date__lte=timezone.localdate()).select_related('version','correction_version').defer('version__snapshot','correction_version__snapshot'):
        task=ensure(company,f'carryover:{record.pk}',kind='CARRYOVER',title='Уточнить прогнозные остатки по закрытому факту',assigned_to=record.assigned_to,version=record.version,carryover=record,construction_object_id=record.version.construction_object_id,due_date=record.due_date)
        if record.correction_version_id and record.correction_version.status in {'APPROVED','COMPLETED'}:
            state(task,'DONE')
            from .smg_rebase import sync as sync_rebase
            sync_rebase(record)


@transaction.atomic
def generate_daily(user,*,today=None):
    if not user.is_active or not user.company_id or role_code(user) not in DISPATCHER_ROLES:return 0
    if role_code(user)=='FUEL_WAREHOUSE':
        for task in WorkTask.objects.filter(company=user.company,assigned_to=user,kind='FACT',status__in=ACTIVE):state(task,'SUPERSEDED',text='ГСМ ведётся по ВЖГ. Учёт по объектам отключён.')
        return 0
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
            from apps.production.dispatch import required_fact_sections,fact_sections_confirmed
            description='Внесите работы, персонал, технику и ГСМ и подтвердите все направления на странице «Факт за день».' if len(required_fact_sections(user))>1 else 'Внесите факт своего направления и подтвердите его полноту на странице «Факт за день». Другие направления заполняют соответствующие диспетчеры.'
            task=ensure(user.company,key,kind='FACT',title=f'Внести факт · {user.role.name} за {day:%d.%m.%Y} · {obj.name}'[:255],assigned_to=user,construction_object=obj,fact_date=day,due_date=day+timedelta(days=1),description=description)
            if fact_sections_confirmed(user,user.company_id,obj.pk,day):state(task,'DONE',text='Направление факта уже подтверждено.')
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
    require_roles(user,{'ESTIMATOR','ADMIN'})
    task=WorkTask.objects.select_for_update().get(pk=task.pk,company=user.company)
    if task.kind!='ESTIMATE' or task.status!='OPEN' or task.estimate_id or not is_executor(user,task):raise PermissionDenied
    version=GlobalPlanVersion.objects.select_for_update().defer('snapshot').get(pk=task.version_id,company=user.company)
    if version.status!='SUBMITTED' or not task.review_id or task.review.number!=version.approval_round:raise ValidationError('Раунд согласования уже изменился.')
    estimate=EstimateReviewTask.objects.create(company=user.company,assigned_to=user,created_by=task.created_by or version.created_by,version=version)
    task.estimate=estimate;task.assigned_to=user;task.save(update_fields=['estimate','assigned_to'])
    # Keep one inbox row when the normal estimator service synchronizes it later.
    task.key=f'estimate:{estimate.pk}';task.started_at=timezone.now();task.save(update_fields=['key','started_at','updated_at']);event(task,user,'Принято в работу')
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
        if not task.started_at:
            task.started_at=timezone.now();task.save(update_fields=['started_at','updated_at'])
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


def invalidate_fact(company_id,object_id,day,section=None):
    from apps.production.models import FactSectionDay
    from apps.production.dispatch import required_fact_sections
    from django.db.models import F
    records=FactSectionDay.objects.filter(company_id=company_id,construction_object_id=object_id,date=day)
    if section:records=records.filter(section=section)
    records.update(confirmed_at=None,confirmed_by=None,revision=F('revision')+1)
    for task in WorkTask.objects.filter(company_id=company_id,kind='FACT',construction_object_id=object_id,fact_date=day,status='DONE').select_related('assigned_to__role'):
        if not section or task.assigned_to and section in (required_fact_sections(task.assigned_to) or set()):state(task,'OPEN',text='Факт направления изменен после подтверждения. Повторно проверьте полноту данных.')


def validate_fact(task):
    from .report_services import select_sources
    from apps.production.models import DailyFact,LaborFact,EquipmentFact,FuelFact
    user=task.assigned_to;obj=task.construction_object;day=task.fact_date
    from apps.production.dispatch import required_fact_sections,fact_sections_confirmed
    if required_fact_sections(user):
        if not user.assigned_objects.filter(pk=obj.pk,company_id=task.company_id).exists():raise PermissionDenied('Объект не закреплен за исполнителем.')
        if not fact_sections_confirmed(user,task.company_id,obj.pk,day):raise ValidationError('Подтвердите все свои направления на странице «Факт за день».')
        return
    if not user or not obj or obj.company_id!=task.company_id or not user.assigned_objects.filter(pk=obj.pk,company_id=task.company_id).exists():raise PermissionDenied('Объект не закреплен за исполнителем.')
    sources=select_sources(user,{'start':day,'end':day,'mode':'latest'},[obj]).get(obj.pk,[])
    snapshot=sources[0].snapshot if sources else {}
    actual=set(DailyFact.objects.filter(company_id=task.company_id,date=day,project_work__section__construction_object=obj).values_list('project_work_id','work_item_id'))
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
    from apps.production.dispatch import required_fact_sections
    expected=required_fact_sections(task.assigned_to) or {'works','labor','equipment','fuel'}
    if set(confirmed)!=expected:raise ValidationError('Подтвердите проверку своего направления факта.')
    validate_fact(task);state(task,'DONE',user,'Проверены работы, должности, техника и ГСМ.');return task
