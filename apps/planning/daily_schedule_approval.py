"""Estimator -> planner submission -> parallel departments -> CEO -> activation."""
from datetime import timedelta
from django.core.exceptions import ValidationError,PermissionDenied
from django.db import transaction
from django.utils import timezone
from core.permissions import require_roles,PLAN_ROLES,role_code
from apps.accounts.models import User
from .models import DailySchedule,DailyScheduleReview,DailyScheduleDecision,WorkTask
from . import estimate_review
from core.plan_approval import DEPARTMENTS,REQUIRED_SECTIONS,DEPARTMENT_ROLES,ROLE_SECTIONS,SECTION_ACTIONS,ACTION_ROLES

SECTIONS=[(section,label,role) for section,label,role,_ in DEPARTMENTS]


def requires_approval(schedule):
    from .preparation import record
    if record(schedule):return True
    from .daily_schedule import schedule_differences
    if not schedule.source_id:return True
    from .subobject_smg import parent_reference
    if parent_reference(schedule):return True
    return bool(schedule_differences(schedule))


def next_action(schedule):
    if schedule.status=='APPROVED':return 'Применить согласованный дневной СМГ'
    return 'Отправить дневной СМГ на согласование' if requires_approval(schedule) else 'Сохранить СМГ как оперативный план'


def planner(schedule,estimate):
    from .preparation import record
    preparation=record(schedule)
    if preparation:return preparation.manager
    candidates=[schedule.created_by,estimate.created_by]
    for role in ['PLANNER','ADMIN']:
        for candidate in candidates:
            if candidate and candidate.is_active and candidate.company_id==schedule.company_id and role_code(candidate)==role:return candidate
    return User.objects.filter(company_id=schedule.company_id,is_active=True,role__code='PLANNER').first()


def handoff(estimate):
    from .task_services import ensure,state,ACTIVE
    if not estimate.schedule_id:return
    schedule=estimate.schedule
    key=f'smg-submit:{schedule.pk}:{estimate.pk}'
    existing=WorkTask.objects.filter(company_id=estimate.company_id,key=key).first()
    valid=schedule.status=='DRAFT' and schedule.estimate_reviews.order_by('-pk').values_list('pk',flat=True).first()==estimate.pk and estimate_review.check_valid(schedule,estimate)
    if valid and schedule.status=='DRAFT':
        assigned=planner(schedule,estimate)
        actor_id=estimate.events.filter(action='APPROVED').order_by('-pk').values_list('actor_id',flat=True).first() or estimate.assigned_to_id
        task=ensure(estimate.company,key,kind='SUBMISSION',title=next_action(schedule),description='Проверка работ сметчика завершена. Руководитель определяет ресурсы и запускает согласование. Если месячные итоги совпадают с ГПР, сохраните СМГ как оперативный план. При отклонениях или отсутствии ГПР требуется согласование всех профильных служб, включая Службу СБО, и генерального директора.',assigned_to=assigned,target_role='' if assigned else 'MANAGER',created_by_id=actor_id,schedule=schedule,estimate=estimate,construction_object_id=schedule.construction_object_id,due_date=timezone.localdate()+timedelta(days=1))
        if task.title!=next_action(schedule):task.title=next_action(schedule);task.save(update_fields=['title'])
        if task.status=='SUPERSEDED':state(task,'OPEN')
    elif existing and existing.status in ACTIVE:
        state(existing,'DONE' if schedule.status in {'SUBMITTED','APPROVED','APPLIED'} else 'SUPERSEDED',text='СМГ отправлен на согласование.' if schedule.status!='DRAFT' else 'Требуется актуальная проверка сметчика.')


def current(schedule):return schedule.approval_reviews.defer('snapshot').filter(number=schedule.approval_round).first()


def sync(schedule):
    from .task_services import ensure,state,ACTIVE
    latest=schedule.estimate_reviews.select_related('created_by','assigned_to','schedule__source').defer('schedule__source__snapshot').order_by('-pk').first()
    for task in WorkTask.objects.filter(schedule=schedule,kind='SUBMISSION',key__startswith='smg-submit:',status__in=ACTIVE).exclude(estimate_id=latest.pk if latest else None):state(task,'SUPERSEDED',text='Проверка сметчика заменена.')
    if latest:handoff(latest)
    review=current(schedule)
    for task in WorkTask.objects.filter(schedule=schedule,schedule_review__isnull=False,status__in=ACTIVE):
        if not review or task.schedule_review_id!=review.pk or schedule.status not in {'SUBMITTED','APPROVED','APPLIED'}:state(task,'SUPERSEDED',text='Раунд согласования СМГ завершен или заменен.')
    if not review:return
    decisions={d.section:d for d in review.decisions.filter(action='APPROVE')}
    if schedule.status in {'APPROVED','APPLIED'}:
        for task in WorkTask.objects.filter(schedule_review=review,kind='APPROVAL',status__in=ACTIVE):state(task,'DONE')
        key=f'smg-apply:{schedule.pk}:{review.number}'
        if schedule.status=='APPROVED':
            assigned=planner(schedule,latest) if latest else schedule.created_by
            ensure(schedule.company,key,kind='SUBMISSION',title='Применить согласованный дневной СМГ',assigned_to=assigned,created_by=review.submitted_by,schedule=schedule,schedule_review=review,construction_object_id=schedule.construction_object_id,due_date=timezone.localdate()+timedelta(days=1))
        else:
            for task in WorkTask.objects.filter(company=schedule.company,key=key,status__in=ACTIVE):state(task,'DONE')
        return
    if schedule.status!='SUBMITTED':return
    if not REQUIRED_SECTIONS<=decisions.keys():
        for old in WorkTask.objects.filter(schedule_review=review,target_role='CEO',kind='APPROVAL',status__in=ACTIVE):state(old,'SUPERSEDED',text='Требуется согласование всех служб, включая Службу СБО.')
    roles=list(SECTIONS)
    if REQUIRED_SECTIONS<=decisions.keys():roles.append(('CEO','Утверждение СМГ','CEO'))
    for section,label,role in roles:
        people=list(User.objects.filter(company=schedule.company,is_active=True,role__code=role)[:2])
        task=ensure(schedule.company,f'smg-approval:{review.pk}:{section}',kind='APPROVAL',title=f'Согласовать дневной СМГ: {label}',assigned_to=people[0] if len(people)==1 else None,target_role=role,created_by=review.submitted_by,schedule=schedule,schedule_review=review,construction_object_id=schedule.construction_object_id,due_date=timezone.localdate()+timedelta(days=2))
        if task.status=='SUPERSEDED' and section not in decisions:state(task,'OPEN')
        if task.status=='OPEN' and task.assigned_to_id and (not task.assigned_to.is_active or role_code(task.assigned_to)!=role):
            task.assigned_to=people[0] if len(people)==1 else None;task.save(update_fields=['assigned_to'])
        if section in decisions:state(task,'DONE',decisions[section].actor)


@transaction.atomic
def transition(user,schedule,action,comment=''):
    from apps.projects.models import ConstructionObject
    scope=DailySchedule.objects.filter(pk=schedule.pk,company=user.company).values_list('construction_object_id',flat=True).get()
    ConstructionObject.objects.select_for_update().get(pk=scope,company=user.company)
    schedule=DailySchedule.objects.select_for_update().select_related('source').defer('source__snapshot').get(pk=schedule.pk,company=user.company)
    from core.permissions import require_planning_object
    require_planning_object(user,schedule.construction_object_id)
    role=role_code(user);review=current(schedule)
    if action=='submit':
        from .preparation import require_submission
        require_submission(user,schedule)
        if schedule.status!='DRAFT':raise ValidationError('На согласование отправляется только черновик СМГ.')
        if not requires_approval(schedule):raise ValidationError('Месячные итоги совпадают с ГПР: сохраните СМГ как оперативный план без нового согласования.')
        estimate_review.require_approved(schedule)
        schedule.approval_round+=1
        review=DailyScheduleReview.objects.create(company=user.company,schedule=schedule,number=schedule.approval_round,fingerprint=estimate_review.signature(schedule),snapshot=list(schedule.entries.values('id','key','kind','label','metadata','original','rows','revision')),submitted_by=user)
        schedule.status='SUBMITTED'
    else:
        section={**ROLE_SECTIONS,'CEO':'CEO','ESTIMATOR':'ESTIMATE'}.get(role)
        if role=='ADMIN':section={**{a:s for s,a in SECTION_ACTIONS.items()},'approve':'CEO'}.get(action,'ADMIN')
        allowed={**{a:roles|{'ADMIN'} for a,roles in ACTION_ROLES.items()},'approve':{'CEO','ADMIN'},'reject':DEPARTMENT_ROLES|{'CEO','ADMIN','ESTIMATOR'}}
        require_roles(user,allowed.get(action,set()))
        if schedule.status!='SUBMITTED' or not review:raise ValidationError('СМГ не находится на согласовании.')
        approved=set(review.decisions.filter(action='APPROVE').values_list('section',flat=True))
        if role=='CEO' and not REQUIRED_SECTIONS<=approved:raise ValidationError('Дождитесь согласования всех служб, включая Службу СБО.')
        if action=='reject':
            if role=='ESTIMATOR' and not schedule.estimate_reviews.filter(assigned_to=user,status='OPEN').exists():raise PermissionDenied
            if not comment.strip():raise ValidationError('Укажите причину возврата.')
            schedule.status='DRAFT'
            schedule.estimate_reviews.filter(status='APPROVED').update(status='REJECTED',fingerprint='',comment=comment)
        else:
            if review.fingerprint!=estimate_review.signature(schedule):raise ValidationError('СМГ изменился после отправки. Верните его на доработку.')
            if section in approved:raise ValidationError('Раздел уже согласован.')
            if action=='approve':
                if not REQUIRED_SECTIONS<=approved:raise ValidationError('Дождитесь согласования всех служб, включая Службу СБО.')
                estimate_review.require_approved(schedule)
                from .smg_variants import require_available
                require_available(schedule)
                schedule.status='APPROVED'
        DailyScheduleDecision.objects.create(company=user.company,review=review,section=section,action='REJECT' if action=='reject' else 'APPROVE',actor=user,actor_name=user.get_full_name() or user.username,actor_role=role,comment=comment)
    schedule.save(update_fields=['status','approval_round','updated_at'])
    sync(schedule)
    if action=='submit':
        from .preparation import submitted
        submitted(schedule,user)
        from .task_services import state,ACTIVE
        for task in WorkTask.objects.filter(schedule=schedule,key__startswith='smg-repair:',status__in=ACTIVE):state(task,'DONE',user,'Исправленный СМГ отправлен на согласование.')
    if action=='reject':
        from .task_services import ensure
        latest=schedule.estimate_reviews.order_by('-pk').first();assigned=planner(schedule,latest) if latest else schedule.created_by
        ensure(schedule.company,f'smg-repair:{schedule.pk}:{schedule.approval_round}',kind='MANUAL',title='Доработать дневной СМГ и повторно проверить у сметчика',description=comment,assigned_to=assigned,created_by=user,schedule=schedule,construction_object_id=schedule.construction_object_id)
    return schedule
