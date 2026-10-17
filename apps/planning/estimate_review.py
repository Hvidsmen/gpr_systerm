"""Mandatory estimator review of live plan inputs and effective dated catalog prices."""
import hashlib
import json
from datetime import date
from decimal import Decimal
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone
from core.permissions import require_roles, PLAN_ROLES, role_code
from apps.works.models import ProjectWork, ProjectWorkItem, WorkPrice
from .models import EstimateReviewTask, EstimateReviewEvent, GlobalPlanVersion, DailySchedule, WorkMonthAllocation


def target(task):
    return task.version if task.version_id else task.schedule


def work_ids(obj):
    if isinstance(obj, DailySchedule):
        return {e.metadata['id'] for e in obj.entries.filter(kind='work')}
    return set(obj.work_allocations.values_list('work_id',flat=True))


def editable(obj):
    if obj.status not in {'DRAFT','REJECTED','SUBMITTED'}:
        raise ValidationError('Проверка доступна для черновика, плана на доработке или на согласовании.')


def signature(obj):
    ids=work_ids(obj)
    works=list(ProjectWork.objects.filter(company_id=obj.company_id,pk__in=ids).order_by('pk').values())
    items=list(ProjectWorkItem.objects.filter(company_id=obj.company_id,project_work_id__in=ids).order_by('pk').values())
    prices=list(WorkPrice.objects.filter(company_id=obj.company_id,work_id__in=ids).order_by('pk').values())
    if isinstance(obj,DailySchedule):
        inputs=list(obj.entries.order_by('pk').values('key','kind','label','metadata','rows','revision'))
    else:
        inputs={'works':list(obj.work_allocations.order_by('pk').values()),'resources':list(obj.resource_allocations.order_by('pk').values()),'month':obj.planning_month,'scenario':obj.scenario}
    # Catalog timestamps are audit data, not planning inputs.
    for rows in (works,items,prices):
        for row in rows:
            row.pop('updated_at',None);row.pop('created_at',None)
    return hashlib.sha256(json.dumps([inputs,works,items,prices],sort_keys=True,default=str).encode()).hexdigest()


def require_approved(obj):
    task=obj.estimate_reviews.order_by('-pk').first()
    if not task or task.status!='APPROVED' or task.fingerprint!=signature(obj):
        raise ValidationError('Сначала передайте план сметчику и получите подтверждение цен, названий и состава работ. После изменений нужна повторная проверка.')
    return task


@transaction.atomic
def assign(user,obj,estimator):
    require_roles(user,PLAN_ROLES)
    if obj.company_id!=user.company_id:raise PermissionDenied
    editable(obj)
    if not estimator.is_active or estimator.company_id!=user.company_id or role_code(estimator)!='ESTIMATOR':raise ValidationError('Выберите действующего сметчика своей компании.')
    if isinstance(obj,DailySchedule):DailySchedule.objects.select_for_update().get(pk=obj.pk)
    else:GlobalPlanVersion.objects.select_for_update().only('pk').get(pk=obj.pk)
    task=obj.estimate_reviews.filter(status='OPEN').order_by('-pk').first()
    if task:
        task.assigned_to=estimator;task.save(update_fields=['assigned_to','updated_at'])
    else:
        task=EstimateReviewTask.objects.create(company=user.company,created_by=user,assigned_to=estimator,**({'schedule':obj} if isinstance(obj,DailySchedule) else {'version':obj}))
    event(task,user,'ASSIGN',{'estimator':estimator.pk})
    return task


def event(task,user,action,details):
    EstimateReviewEvent.objects.create(company=user.company,task=task,actor=user,action=action,details=details)


def lock_task(user,task,*,editing=False):
    require_roles(user,{'ESTIMATOR'})
    task=EstimateReviewTask.objects.select_for_update().select_related('version','schedule').defer('version__snapshot').get(pk=task.pk,company=user.company)
    if task.assigned_to_id!=user.pk:raise PermissionDenied('Задание назначено другому сметчику.')
    editable(target(task))
    if task.status!='OPEN':raise ValidationError('Для дальнейших исправлений планировщик должен повторно назначить проверку.')
    obj=target(task)
    if isinstance(obj,DailySchedule):DailySchedule.objects.select_for_update().get(pk=obj.pk)
    else:GlobalPlanVersion.objects.select_for_update().only('pk').get(pk=obj.pk)
    if editing and obj.status=='SUBMITTED':
        return_to_draft(task,user,'Сметчик внес исправления; требуется повторное согласование обновленного плана.')
    return task


def return_to_draft(task,user,comment):
    obj=target(task)
    if isinstance(obj,DailySchedule) or obj.status!='SUBMITTED':return
    from .approval_workflow import current_review
    from .approval_context import authorized_transition
    from .models import GlobalPlanDecision
    review=current_review(obj)
    GlobalPlanDecision.objects.create(company=user.company,review=review,section='ESTIMATE',action='REJECT',actor=user,actor_name=user.get_full_name() or user.username,actor_role='ESTIMATOR',comment=comment)
    obj.status='REJECTED';obj.comment=comment;obj.approved_at=None;obj.approved_by=None
    with authorized_transition(obj.pk):obj.save(update_fields=['status','comment','approved_at','approved_by','updated_at'])


def scoped_work(task,work_id):
    obj=target(task)
    if int(work_id) not in work_ids(obj):raise PermissionDenied('Работа не входит в задание.')
    return ProjectWork.objects.select_for_update().get(pk=work_id,company_id=task.company_id)


def decision_signature(work):
    from .workspace_services import specification
    return hashlib.sha256(json.dumps(specification(work),sort_keys=True,default=str).encode()).hexdigest()


@transaction.atomic
def change(user,task,work_id,*,name=None,price=None,effective_from=None,reason='',item_data=None):
    task=lock_task(user,task,editing=True);work=scoped_work(task,work_id)
    changes={}
    if name is not None and name.strip()!=work.name:
        changes['name']=[work.name,name.strip()];work.name=name.strip();work.full_clean();work.save()
    if price is not None:
        if not reason.strip():raise ValidationError('Укажите основание изменения цены.')
        from apps.works.prices import change_price
        previous=work.price_history.filter(effective_from=effective_from).order_by('-pk').first()
        entry=change_price(user,work.pk,price,effective_from,reason,corrects=previous,review_task=task)
        changes['price']={'id':entry.pk,'price':str(entry.price),'effective_from':entry.effective_from.isoformat(),'reason':reason}
    if item_data is not None:
        item_id=item_data.pop('id',None)
        item=ProjectWorkItem.objects.select_for_update().get(pk=item_id,project_work=work,company=user.company) if item_id else ProjectWorkItem(company=user.company,project_work=work)
        for field,value in item_data.items():setattr(item,field,value)
        item.full_clean();item.save();changes['item']={'id':item.pk,'name':item.name}
    obj=target(task)
    if isinstance(obj,GlobalPlanVersion):obj.snapshot={};obj.save(update_fields=['snapshot'])
    task.decisions.pop(str(work.pk),None);task.save(update_fields=['decisions','updated_at'])
    event(task,user,'CHANGE',{'work':work.pk,**changes})
    return task


@transaction.atomic
def review_work(user,task,work_id,approved,comment=''):
    task=lock_task(user,task);work=scoped_work(task,work_id)
    if not approved and not comment.strip():raise ValidationError('Укажите причину отклонения и какую работу из справочника использовать.')
    if not approved:return_to_draft(task,user,comment.strip())
    task.decisions[str(work.pk)]={'approved':approved,'comment':comment.strip(),'signature':decision_signature(work)}
    task.save(update_fields=['decisions','updated_at']);event(task,user,'WORK_APPROVE' if approved else 'WORK_REJECT',{'work':work.pk,'comment':comment})
    return task


@transaction.atomic
def replace_work(user,task,work_id,replacement_id):
    """Merge duplicates in this draft only; facts and approved versions stay intact."""
    task=lock_task(user,task,editing=True);work=scoped_work(task,work_id);obj=target(task)
    replacement=ProjectWork.objects.select_for_update().get(pk=replacement_id,company=user.company)
    if replacement.pk==work.pk:raise ValidationError('Выберите другую работу.')
    if replacement.section.construction_object_id!=work.section.construction_object_id or replacement.unit!=work.unit or replacement.kind!=work.kind:raise ValidationError('Для замены должны совпадать объект, единица и тип работы.')
    if hasattr(replacement,'merged_source'):raise ValidationError('Выбранная работа архивная.')
    # Explicit mapping by unique name + unit; never guess between ambiguous subworks.
    mapping={}
    for item in work.items.all():
        candidates=list(replacement.items.filter(name=item.name,unit=item.unit,quantity_per_unit=item.quantity_per_unit).values_list('pk',flat=True))
        if len(candidates)!=1:raise ValidationError('Составные работы имеют разный состав. Сначала согласуйте названия, единицы и нормативы подработ.')
        mapping[str(item.pk)]=candidates[0]
    if work.items.count()!=replacement.items.count():raise ValidationError('Состав подработ должен совпадать.')
    if isinstance(obj,DailySchedule):
        raise ValidationError('Замену работы выполните в новой версии месячного ГПР, затем создайте СМГ из исправленного плана.')
    if obj.workspace_id and obj.workspace.parent_gpr_id:raise ValidationError('Для СМГ субобъекта сначала обновите связь с работой ГПР.')
    for row in obj.work_allocations.select_for_update().filter(work=work):
        if row.daily_override is not None:raise ValidationError('Перед заменой проверьте сохраненный дневной план работы.')
        dest=obj.work_allocations.filter(work=replacement,month=row.month).first()
        quantities={str(mapping[k]):v for k,v in row.item_quantities.items()}
        if dest:
            if dest.daily_override is not None or dest.load_profile_id!=row.load_profile_id:raise ValidationError('У дублей разные профили или сохраненные дневные планы. Требуется ручное сопоставление.')
            if bool(dest.item_quantities)!=bool(quantities):raise ValidationError('У дублей разный способ задания объемов подработ.')
            dest.quantity+=row.quantity
            dest.item_quantities={k:str(Decimal(v)+Decimal(quantities.get(k,0))) for k,v in dest.item_quantities.items()} if dest.item_quantities else quantities
            dest.full_clean();dest.save();row.delete()
        else:
            row.work=replacement;row.item_quantities=quantities;row.full_clean();row.save()
    # Preserve provenance and expose source in the immutable audit log; receipts stay on original work.
    task.decisions.pop(str(work.pk),None);task.decisions.pop(str(replacement.pk),None);task.save(update_fields=['decisions','updated_at'])
    obj.snapshot={};obj.save(update_fields=['snapshot'])
    event(task,user,'REPLACE',{'source':work.pk,'target':replacement.pk,'source_name':work.name,'target_name':replacement.name})
    return task


def reprice_schedule(schedule):
    from apps.works.prices import history_prices,rate_on
    for entry in schedule.entries.filter(kind='work'):
        work=ProjectWork.objects.get(pk=entry.metadata['id'],company_id=schedule.company_id)
        prices=history_prices(work)
        from .workspace_services import specification
        entry.metadata.update(specification(work))
        entry.metadata['name']=work.name;entry.metadata['unit_price']=str(work.unit_price)
        entry.metadata['revenue_prices']=[{'effective_from':p.effective_from.isoformat(),'price':str(p.price),'id':p.pk} for p in prices]
        if work.kind=='SIMPLE':
            for row in entry.rows:row['value']=str((Decimal(row['quantity'])*rate_on(prices,date.fromisoformat(row['date']),work.unit_price)).quantize(Decimal('.01')))
        entry.label=work.name;entry.save(update_fields=['metadata','rows','label'])


@transaction.atomic
def finish(user,task,approve,comment=''):
    task=lock_task(user,task);obj=target(task)
    works=ProjectWork.objects.filter(company=user.company,pk__in=work_ids(obj))
    if approve:
        if isinstance(obj,GlobalPlanVersion) and obj.status=='SUBMITTED':
            from .approval_workflow import current_review
            review=current_review(obj)
            if review.input_fingerprint and review.input_fingerprint!=signature(obj):
                raise ValidationError('Справочник или план изменился после отправки. Верните план на доработку и отправьте обновленный расчет на повторное согласование.')
        for work in works:
            d=task.decisions.get(str(work.pk),{})
            if not d.get('approved') or d.get('signature')!=decision_signature(work):raise ValidationError(f'Проверьте название, цену и подработы: {work.name}.')
        if isinstance(obj,DailySchedule):reprice_schedule(obj)
        task.status='APPROVED';task.fingerprint=signature(obj);task.approved_at=timezone.now()
        if not isinstance(obj,DailySchedule) and obj.status=='SUBMITTED':
            from .approval_workflow import current_review
            from .models import GlobalPlanDecision
            review=current_review(obj)
            if not review.decisions.filter(section='ESTIMATE',action='APPROVE').exists():
                GlobalPlanDecision.objects.create(company=user.company,review=review,section='ESTIMATE',action='APPROVE',actor=user,actor_name=user.get_full_name() or user.username,actor_role='ESTIMATOR',comment=comment or 'Названия, цены и состав работ проверены.')
    else:
        if not comment.strip():raise ValidationError('Укажите, что необходимо исправить.')
        return_to_draft(task,user,comment.strip())
        task.status='REJECTED';task.fingerprint=''
    task.comment=comment.strip();task.save();event(task,user,task.status,{'comment':comment})
    return task
