"""Collaborative formation followed by planner-controlled submission."""
import hashlib,json
from django.core.exceptions import PermissionDenied,ValidationError
from django.db import transaction
from django.db.models import Q
from uuid import uuid4
from django.utils import timezone
from core.permissions import require_roles,role_code,require_planning_object
from .models import PlanPreparation,DailySchedule,WorkTask
from . import estimate_review


def record(owner):
    return PlanPreparation.objects.filter(**({'schedule':owner} if isinstance(owner,DailySchedule) else {'version':owner})).first()


def resource_editor_allowed(user, owner, preparation=None):
    preparation=preparation or record(owner)
    role=role_code(user)
    if role=='ADMIN':return True
    if role not in {'MANAGER','SITE_CHIEF'} or not preparation:return False
    if preparation.resource_editors.filter(pk=user.pk).exists():return True
    # Existing records created before collaborative formation keep their owner.
    return preparation.manager_id==user.pk


def project_approver_allowed(user,owner):
    """Either appointed project manager/site chief may close the shared stage."""
    return role_code(user) in {'MANAGER','SITE_CHIEF'} and resource_editor_allowed(user,owner)


def resource_signature(owner):
    from .resource_targets import assignment_values
    data={'assignments':assignment_values(owner)}
    from .resource_links import binding_values
    links=binding_values(owner)
    if links:data['resource_work_links']=links
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
            # New parent-work provenance belongs to work entries. Keep existing
            # resource confirmations valid when this nullable field is added.
            if row.get('kind')!='work':row.pop('gpr_work_id',None)
            if not row.get('selected_item_ids'):row.pop('selected_item_ids',None)
    return hashlib.sha256(json.dumps(data,sort_keys=True,default=str).encode()).hexdigest()


def require_resource_editor(user,owner):
    require_roles(user,{'ADMIN','MANAGER','SITE_CHIEF'})
    if user.company_id!=owner.company_id:raise PermissionDenied
    preparation=record(owner)
    if not resource_editor_allowed(user,owner,preparation):raise PermissionDenied('Ресурсы формирует назначенный руководитель проекта или начальник участка.')
    if owner.status not in {'DRAFT','REJECTED'}:raise ValidationError('Ресурсы меняются только в черновике.')
    return preparation


@transaction.atomic
def handoff(user,owner,manager,estimator,resource_editors=()):
    require_roles(user,{'ADMIN','PLANNER'})
    if owner.company_id!=user.company_id:raise PermissionDenied
    require_planning_object(user,owner.construction_object_id)
    owner=type(owner).objects.select_for_update().get(pk=owner.pk,company=user.company)
    if owner.status not in {'DRAFT','REJECTED'}:raise ValidationError('Передать можно только черновик плана.')
    if manager.company_id!=owner.company_id or not manager.is_active or role_code(manager) not in {'MANAGER','SITE_CHIEF','ADMIN'}:raise ValidationError('Выберите действующего руководителя проекта или начальника участка своей компании.')
    editors={manager,*resource_editors}
    if any(person.company_id!=owner.company_id or not person.is_active or role_code(person) not in {'MANAGER','SITE_CHIEF','ADMIN'} for person in editors):
        raise ValidationError('Участниками ресурсной части могут быть руководители проекта и начальники участка своей компании.')
    key={'schedule':owner} if isinstance(owner,DailySchedule) else {'version':owner}
    preparation,_=PlanPreparation.objects.update_or_create(**key,defaults={'company':owner.company,'manager':manager,'handed_by':user,'resources_fingerprint':'','resources_ready_at':None,'resources_ready_by':None})
    preparation.resource_editors.set(editors)
    task=estimate_review.assign(user,owner,estimator)
    from .task_services import ensure,state
    candidates=resource_tasks(owner,preparation).select_for_update()
    worktask=candidates.filter(status__in=['OPEN','IN_REVIEW']).order_by('-pk').first()
    if worktask is None:
        previous=candidates.order_by('-pk').first()
        task_key=(f'prepare-resources:{preparation.pk}:retry:{uuid4()}' if previous and previous.status=='CLOSED_ADMIN' else previous.key if previous else f'prepare-resources:{preparation.pk}')
        worktask=ensure(owner.company,task_key,kind='RESOURCES',title='Определить ресурсы для выполнения работ',description='Работы параллельно проверяет сметчик. Совместно подготовьте ресурсную часть; после подтверждения планировщик проверит маршрут и запустит согласование.',assigned_to=manager,created_by=user,construction_object_id=owner.construction_object_id,**key)
    worktask.assigned_to=manager;worktask.save(update_fields=['assigned_to'])
    from .task_services import event
    names=', '.join(sorted(person.full_name for person in editors))
    event(worktask,user,'Передано на совместное формирование',f'Ресурсная часть: {names}. Работы параллельно переданы сметчику.')
    if worktask.status!='OPEN':state(worktask,'OPEN',user,'Работы переданы на подготовку ресурсов.')
    return preparation


@transaction.atomic
def ready(user,owner):
    if owner.company_id!=user.company_id:raise PermissionDenied
    owner=type(owner).objects.select_for_update().get(pk=owner.pk,company=user.company)
    preparation=require_resource_editor(user,owner)
    if not preparation:raise ValidationError('Планировщик должен передать работы руководителю и сметчику.')
    preparation.resources_fingerprint=resource_signature(owner);preparation.resources_ready_at=timezone.now()
    preparation.resources_ready_by=user
    preparation.save(update_fields=['resources_fingerprint','resources_ready_at','resources_ready_by','updated_at'])
    from .task_services import event
    task=resource_tasks(owner,preparation).order_by('-pk').first()
    if task:event(task,user,'Ресурсы определены','Ресурсы подтверждены руководителем проекта или начальником участка.')
    return preparation


def require_submission(user,owner):
    require_roles(user,{'ADMIN','PLANNER'})
    if user.company_id!=owner.company_id:raise PermissionDenied
    preparation=record(owner)
    if not preparation:raise ValidationError('Сначала передайте работы руководителю проекта и сметчику.')
    from core.permissions import require_planning_object
    require_planning_object(user,owner.construction_object_id)
    from apps.resources.catalogue_review import require_reviewed
    require_reviewed(owner)
    if not preparation.resources_ready_at or preparation.resources_fingerprint!=resource_signature(owner):raise ValidationError('Руководитель проекта или начальник участка должен подтвердить ресурсную часть. После изменений подтвердите ресурсы заново.')
    estimate_review.require_approved(owner)


def submitted(owner,user):
    preparation=record(owner)
    if preparation:
        from .task_services import state
        for task in resource_tasks(owner,preparation).filter(status__in=['OPEN','IN_REVIEW']):state(task,'DONE',user,'Ресурсы определены, план отправлен на согласование.')


def task_context(user,owner):
    """Show actual task state after handing a plan over, without recreating tasks."""
    preparation=record(owner)
    manager_task=resource_tasks(owner,preparation).order_by('-pk').first() if preparation else None
    latest=owner.estimate_reviews.order_by('-pk').first()
    estimator_task=latest.work_tasks.filter(kind='ESTIMATE').order_by('-pk').first() if latest else None
    from .task_services import visible
    from django.urls import reverse
    allowed=set(visible(user).filter(pk__in=[t.pk for t in [manager_task,estimator_task] if t]).values_list('pk',flat=True))
    return {'preparation_manager_task':manager_task,'preparation_estimator_task':estimator_task,
            'preparation_manager_task_url':reverse('planning:task_detail',args=[manager_task.pk]) if manager_task and manager_task.pk in allowed else '',
            'preparation_estimator_task_url':reverse('planning:task_detail',args=[estimator_task.pk]) if estimator_task and estimator_task.pk in allowed else ''}


def require_resource_input(user,owner):
    """Planner may draft resources; manager confirmation stays separate."""
    if role_code(user)=='PLANNER' and (isinstance(owner,DailySchedule) or owner.workspace_id and not owner.workspace.parent_gpr_id):
        if user.company_id!=owner.company_id or not isinstance(owner,DailySchedule) and owner.workspace.is_archived:raise PermissionDenied
        require_planning_object(user,owner.construction_object_id)
        if owner.status not in {'DRAFT','REJECTED'}:raise ValidationError('Ресурсы меняются только в черновике.')
        return record(owner)
    return require_resource_editor(user,owner)


def resource_tasks(owner,preparation=None):
    preparation=preparation or record(owner)
    if not preparation:return WorkTask.objects.none()
    base=f'prepare-resources:{preparation.pk}'
    key={'schedule':owner} if isinstance(owner,DailySchedule) else {'version':owner}
    return WorkTask.objects.filter(Q(key=base)|Q(key__startswith=base+':retry:'),company=owner.company,**key)
