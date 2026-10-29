"""One actionable inbox task per catalogue request, with direction-scoped access."""
from datetime import timedelta
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from core.permissions import role_code
from apps.accounts.models import User
from apps.planning.models import WorkTask
from apps.planning.task_services import ensure, state
from .models import CatalogueReviewRequest

REVIEWERS = {'labor': 'PERSONNEL_CATALOG_MANAGER', 'equipment': 'EQUIPMENT_CATALOG_MANAGER'}
LABELS = {'labor': 'должность', 'equipment': 'технику для планирования'}


@transaction.atomic
def sync_task(proposal):
    proposal=CatalogueReviewRequest.objects.select_for_update().select_related(
        'brigade','equipment_type','version','schedule','reviewed_by','created_by__role').defer('version__snapshot').get(pk=proposal.pk,company_id=proposal.company_id)
    role=REVIEWERS[proposal.kind]
    reviewers=list(User.objects.filter(company_id=proposal.company_id,is_active=True,role__code=role).order_by('pk')[:2])
    fallback=None
    if not reviewers:
        author=proposal.created_by
        fallback=author if author.is_active and author.company_id==proposal.company_id and role_code(author)=='ADMIN' else User.objects.filter(company_id=proposal.company_id,is_active=True).filter(Q(role__code='ADMIN')|Q(is_superuser=True)).order_by('pk').first()
    title=('Проверить новую '+LABELS[proposal.kind]+': '+str(proposal.resource))[:255]
    owner=proposal.version if proposal.version_id else proposal.schedule if proposal.schedule_id else None
    description='Подтвердите создание, выберите существующую позицию либо отклоните с причиной. Решение принимается в карточке заявки.\n'+proposal.origin_label
    if not reviewers and proposal.status=='PENDING':
        description+='\nОтветственный этого направления не назначен. Проверку может выполнить администратор.'
    task=ensure(proposal.company,f'catalogue-review:{proposal.pk}',kind='CATALOGUE',title=title,description=description,
        catalogue_request=proposal,created_by=proposal.created_by,target_role=role,
        assigned_to=reviewers[0] if len(reviewers)==1 else fallback,
        construction_object_id=owner.construction_object_id if owner else None,
        due_date=timezone.localdate(proposal.created_at)+timedelta(days=1))
    task=WorkTask.objects.select_for_update().select_related('assigned_to__role').get(pk=task.pk,company_id=proposal.company_id)
    updates=[]
    for field,value in [('title',title),('description',description),('target_role',role),('catalogue_request_id',proposal.pk)]:
        if getattr(task,field)!=value:setattr(task,field,value);updates.append(field)
    if proposal.status=='PENDING':
        current=task.assigned_to
        eligible=bool(current and current.is_active and (role_code(current)==role or not reviewers and role_code(current)=='ADMIN'))
        if not eligible:
            replacement=reviewers[0].pk if len(reviewers)==1 else fallback.pk if fallback else None
            if task.assigned_to_id!=replacement:
                task.assigned_to_id=replacement;task.started_at=None;updates+=['assigned_to','started_at']
    if updates:task.save(update_fields=[*updates,'updated_at'])
    if proposal.status!='PENDING' and task.status in {'OPEN','IN_REVIEW'}:
        state(task,'DONE',proposal.reviewed_by,proposal.get_status_display()+(': '+proposal.comment if proposal.comment else ''))
    return task


def sync_company(company):
    requests=CatalogueReviewRequest.objects.filter(company=company).filter(Q(status='PENDING')|Q(work_task__status__in=['OPEN','IN_REVIEW'])).distinct()
    for proposal in requests.iterator():sync_task(proposal)
