"""Planner works -> parallel manager resources / estimator -> manager submission."""
import hashlib,json
from django.core.exceptions import PermissionDenied,ValidationError
from django.db import transaction
from django.utils import timezone
from core.permissions import require_roles,role_code,require_planning_object
from .models import PlanPreparation,DailySchedule,WorkTask
from . import estimate_review


def record(owner):
    return PlanPreparation.objects.filter(**({'schedule':owner} if isinstance(owner,DailySchedule) else {'version':owner})).first()


def resource_signature(owner):
    data={'assignments':list(owner.resource_assignments.order_by('pk').values())}
    if isinstance(owner,DailySchedule):data['resources']=list(owner.entries.exclude(kind='work').order_by('pk').values())
    else:
        start,end=estimate_review.review_period(owner)
        data['resources']=list(owner.resource_allocations.filter(month__range=(start,end)).order_by('pk').values())
    from apps.works.models import ProjectWork,ProjectWorkItem
    ids=estimate_review.work_ids(owner)
    data['work_catalog']=list(ProjectWork.objects.filter(company=owner.company,pk__in=ids).order_by('pk').values('pk','unit','kind'))
    data['work_items']=list(ProjectWorkItem.objects.filter(company=owner.company,project_work_id__in=ids).order_by('pk').values('pk','project_work_id','quantity_per_unit','unit','parent_id'))
    if isinstance(owner,DailySchedule):
        data['work_inputs']=[{'work':e.metadata['id'],'rows':[{k:r.get(k) for k in ('date','item_id','quantity')} for r in e.rows]} for e in owner.entries.filter(kind='work').order_by('pk')]
    else:data['work_inputs']=list(estimate_review.review_allocations(owner).order_by('pk').values('work_id','month','quantity','item_quantities','selected_item_ids'))
    for rows in data.values():
        for row in rows:
            row.pop('updated_at',None);row.pop('created_at',None)
            if not row.get('selected_item_ids'):row.pop('selected_item_ids',None)
    return hashlib.sha256(json.dumps(data,sort_keys=True,default=str).encode()).hexdigest()


def require_resource_editor(user,owner):
    require_roles(user,{'ADMIN','MANAGER'})
    if user.company_id!=owner.company_id:raise PermissionDenied
    preparation=record(owner)
    if role_code(user)!='ADMIN' and (not preparation or preparation.manager_id!=user.pk):raise PermissionDenied('Ресурсы определяет назначенный руководитель проекта.')
    if owner.status not in {'DRAFT','REJECTED'}:raise ValidationError('Ресурсы меняются только в черновике.')
    return preparation


@transaction.atomic
def handoff(user,owner,manager,estimator):
    require_roles(user,{'ADMIN','PLANNER'})
    if owner.company_id!=user.company_id:raise PermissionDenied
    require_planning_object(user,owner.construction_object_id)
    owner=type(owner).objects.select_for_update().get(pk=owner.pk,company=user.company)
    if owner.status not in {'DRAFT','REJECTED'}:raise ValidationError('Передать можно только черновик плана.')
    if manager.company_id!=owner.company_id or not manager.is_active or role_code(manager) not in {'MANAGER','ADMIN'}:raise ValidationError('Выберите действующего руководителя проекта своей компании.')
    key={'schedule':owner} if isinstance(owner,DailySchedule) else {'version':owner}
    preparation,_=PlanPreparation.objects.update_or_create(**key,defaults={'company':owner.company,'manager':manager,'handed_by':user,'resources_fingerprint':'','resources_ready_at':None})
    task=estimate_review.assign(user,owner,estimator)
    from .task_services import ensure,state
    worktask=ensure(owner.company,f'prepare-resources:{preparation.pk}',kind='RESOURCES',title='Определить ресурсы для выполнения работ',description='Работы параллельно проверяет сметчик. Подготовьте ресурсы и запустите согласование после успешной проверки работ.',assigned_to=manager,created_by=user,construction_object_id=owner.construction_object_id,**key)
    worktask.assigned_to=manager;worktask.save(update_fields=['assigned_to'])
    from .task_services import event
    event(worktask,user,'Передано руководителю',f'Руководитель: {manager.full_name}. Работы параллельно переданы сметчику.')
    if worktask.status!='OPEN':state(worktask,'OPEN',user,'Работы переданы на подготовку ресурсов.')
    return preparation


@transaction.atomic
def ready(user,owner):
    if owner.company_id!=user.company_id:raise PermissionDenied
    owner=type(owner).objects.select_for_update().get(pk=owner.pk,company=user.company)
    preparation=require_resource_editor(user,owner)
    if not preparation:raise ValidationError('Планировщик должен передать работы руководителю и сметчику.')
    preparation.resources_fingerprint=resource_signature(owner);preparation.resources_ready_at=timezone.now()
    preparation.save(update_fields=['resources_fingerprint','resources_ready_at','updated_at'])
    from .task_services import event
    task=WorkTask.objects.filter(company=owner.company,key=f'prepare-resources:{preparation.pk}').first()
    if task:event(task,user,'Ресурсы определены','Ресурсы подтверждены руководителем проекта.')
    return preparation


def require_submission(user,owner):
    require_roles(user,{'ADMIN','MANAGER'})
    if user.company_id!=owner.company_id:raise PermissionDenied
    preparation=record(owner)
    if not preparation:raise ValidationError('Сначала передайте работы руководителю проекта и сметчику.')
    if role_code(user)!='ADMIN' and preparation.manager_id!=user.pk:raise PermissionDenied('Согласование запускает назначенный руководитель проекта.')
    if not preparation.resources_ready_at or preparation.resources_fingerprint!=resource_signature(owner):raise ValidationError('Руководитель проекта должен завершить определение ресурсов. После изменений подтвердите ресурсы заново.')
    estimate_review.require_approved(owner)


def submitted(owner,user):
    preparation=record(owner)
    if preparation:
        from .task_services import state
        for task in WorkTask.objects.filter(company=owner.company,key=f'prepare-resources:{preparation.pk}',status__in=['OPEN','IN_REVIEW']):state(task,'DONE',user,'Ресурсы определены, план отправлен на согласование.')
