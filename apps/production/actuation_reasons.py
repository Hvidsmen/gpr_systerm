"""Reason review decisions update linked facts atomically and retain the original request."""
from datetime import timedelta
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import F, Q
from django.utils import timezone
from core.permissions import require_roles, role_code
from core.fact_history import history_actor
from apps.accounts.models import User, Company
from apps.projects.models import ConstructionObject
from apps.planning.models import WorkTask
from apps.planning.task_services import ensure, state, event
from .actuation_models import ActuationReason

EDITORS={'ADMIN','DISPATCHER','ALL_FACT_DISPATCHER','FOREMAN'}
REVIEWERS={'ADMIN','PRODUCTION_HEAD'}
DEFAULTS=('Актировка по погодным условиям','Актировка из-за проблем с транспортом')


def normalize(name):return ' '.join(str(name).split()).casefold()


def ensure_defaults(company):
    for name in DEFAULTS:
        ActuationReason.objects.get_or_create(company=company,normalized_name=normalize(name),defaults={'name':name,'status':'APPROVED'})


def choices(company,retained=()):
    ensure_defaults(company)
    return [(str(r.pk),r.label) for r in ActuationReason.objects.filter(company=company).filter(Q(status__in=['APPROVED','PENDING'])|Q(pk__in=retained)).order_by('name','pk')]


def reason_text(company,ids):
    reasons={r.pk:r for r in ActuationReason.objects.filter(company=company,pk__in=ids)}
    return '; '.join(reasons[pk].label if reasons[pk].status=='REJECTED' else reasons[pk].name for pk in ids if pk in reasons)


@transaction.atomic
def sync_task(reason):
    heads=list(User.objects.filter(company=reason.company,is_active=True,role__code='PRODUCTION_HEAD').order_by('pk')[:2])
    fallback=User.objects.filter(company=reason.company,is_active=True).filter(Q(role__code='ADMIN')|Q(is_superuser=True)).order_by('pk').first() if not heads else None
    assigned=heads[0] if len(heads)==1 else fallback
    task=ensure(reason.company,f'actuation-reason:{reason.pk}',kind='CATALOGUE',title=('Проверить причину актировки: '+reason.name)[:255],
        description='Примите новую причину, замените существующей либо удалите из списка. Решение применяется к связанным актировкам; исходная заявка и акты сохраняются.',
        created_by=reason.created_by,target_role='PRODUCTION_HEAD',assigned_to=assigned,due_date=timezone.localdate(reason.created_at)+timedelta(days=1))
    if reason.task_id!=task.pk:reason.task=task;reason.save(update_fields=['task'])
    if reason.status=='PENDING':
        eligible=task.assigned_to_id and User.objects.filter(pk=task.assigned_to_id,is_active=True).filter(Q(role__code='PRODUCTION_HEAD') if heads else Q(role__code='ADMIN')|Q(is_superuser=True)).exists()
        if not eligible and task.assigned_to_id!=(assigned.pk if assigned else None):
            task.assigned_to=assigned;task.started_at=None;task.save(update_fields=['assigned_to','started_at','updated_at'])
        if task.status not in {'OPEN','IN_REVIEW'}:state(task,'OPEN',reason.created_by,'Причина повторно направлена на проверку.')
    elif task.status in {'OPEN','IN_REVIEW'}:state(task,'DONE',reason.reviewed_by,reason.get_status_display()+(': '+reason.review_comment if reason.review_comment else ''))
    return task


def sync_company(company):
    for reason in ActuationReason.objects.filter(company=company,status='PENDING').iterator():sync_task(reason)


@transaction.atomic
def propose(user,name):
    require_roles(user,EDITORS)
    name=' '.join(str(name).split())
    if not name or len(name)>200:raise ValidationError('Введите название причины длиной до 200 символов.')
    Company.objects.select_for_update().get(pk=user.company_id)
    ensure_defaults(user.company)
    reason,created=ActuationReason.objects.get_or_create(company=user.company,normalized_name=normalize(name),defaults={'name':name,'created_by':user})
    if reason.status=='MERGED':return reason.replacement
    if reason.status=='REJECTED':raise ValidationError('Эту причину руководитель удалил из списка. Выберите другую причину или уточните название.')
    if reason.status=='PENDING':sync_task(reason)
    return reason


@transaction.atomic
def decide(user,pk,action,revision,target=None,comment=''):
    require_roles(user,REVIEWERS)
    from .models import DailyFact
    candidates=[f for f in DailyFact.objects.filter(company=user.company).exclude(actuation_type='').select_related('project_work__section') if pk in f.actuation_reason_ids]
    object_ids=sorted({f.project_work.section.construction_object_id for f in candidates})
    list(ConstructionObject.objects.select_for_update().filter(company=user.company,pk__in=object_ids).order_by('pk'))
    reason=ActuationReason.objects.select_for_update().get(pk=pk,company=user.company)
    if reason.status!='PENDING' or reason.revision!=revision:raise ValidationError('Решение уже принято. Обновите заявку.')
    if action not in {'approve','replace','reject'}:raise ValidationError('Выберите решение.')
    if action=='replace':
        target=ActuationReason.objects.select_for_update().filter(pk=target,company=user.company,status='APPROVED').exclude(pk=pk).first()
        if not target:raise ValidationError('Выберите существующую принятую причину.')
        reason.replacement=target
    reason.status={'approve':'APPROVED','replace':'MERGED','reject':'REJECTED'}[action]
    reason.reviewed_by=user;reason.reviewed_at=timezone.now();reason.review_comment=comment;reason.revision+=1;reason.save()
    with history_actor(user,'Проверка причины актировки: '+reason.get_status_display()):
        for fact in DailyFact.objects.select_for_update().filter(pk__in=[f.pk for f in candidates]).select_related('project_work__section'):
            if action=='replace':fact.actuation_reason_ids=list(dict.fromkeys(target.pk if k==pk else k for k in fact.actuation_reason_ids))
            fact.actuation_reason=reason_text(user.company,fact.actuation_reason_ids)
            fact.save(update_fields=['actuation_reason_ids','actuation_reason','updated_at'])
    task=sync_task(reason)
    event(task,user,'Решение по причине актировки',reason.get_status_display()+(' → '+target.name if action=='replace' else '')+(': '+comment if comment else ''))
    from apps.audit.models import AuditLog
    AuditLog.objects.create(user=user,action='actuation_reason_'+action,entity_type='production.ActuationReason',entity_id=str(reason.pk),new_value={'status':reason.status,'replacement':reason.replacement_id,'comment':comment})
    return reason
