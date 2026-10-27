"""Mandatory estimator review of live plan inputs and effective dated catalog prices."""
import hashlib
import json
from datetime import date
from calendar import monthrange
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
        # Period projections retain historical specs with empty daily arrays.
        # They are not rows of this month's SMG and must not require review.
        return {e.metadata['id'] for e in obj.entries.filter(kind='work').exclude(original=[],rows=[])}
    return set(review_allocations(obj).exclude(quantity=0,item_quantities={}).values_list('work_id',flat=True))


def review_period(obj):
    if isinstance(obj,DailySchedule):
        start=obj.month
        return start,start.replace(day=monthrange(start.year,start.month)[1])
    if obj.planning_month:
        start=obj.planning_month
        return start,start.replace(day=monthrange(start.year,start.month)[1])
    return (obj.workspace.continuation_start if obj.workspace_id and obj.workspace.continuation_start else obj.start_date),obj.end_date


def review_allocations(obj):
    start,end=review_period(obj)
    return obj.work_allocations.filter(month__range=(start,end))


def editable(obj):
    if obj.status not in {'DRAFT','REJECTED','SUBMITTED'}:
        raise ValidationError('Проверка доступна для черновика, плана на доработке или на согласовании.')


def signature(obj,*,legacy=False,works_only=False):
    # Before monthly scoping, pending rounds used all allocation rows and no period key.
    # Recompute that exact format only to verify unchanged pre-update submissions.
    ids=set(obj.work_allocations.values_list('work_id',flat=True)) if legacy and not isinstance(obj,DailySchedule) else work_ids(obj)
    if legacy and isinstance(obj,DailySchedule):ids={e.metadata['id'] for e in obj.entries.filter(kind='work')}
    works=list(ProjectWork.objects.filter(company_id=obj.company_id,pk__in=ids).order_by('pk').values())
    items=list(ProjectWorkItem.objects.filter(company_id=obj.company_id,project_work_id__in=ids).order_by('pk').values())
    prices=list(WorkPrice.objects.filter(company_id=obj.company_id,work_id__in=ids).order_by('pk').values())
    if isinstance(obj,DailySchedule):
        entries=obj.entries.filter(kind='work') if works_only else obj.entries
        inputs=list(entries.order_by('pk').values('key','kind','label','metadata','rows','revision'))
    else:
        start,end=review_period(obj)
        inputs={'works':list((obj.work_allocations if legacy else review_allocations(obj)).order_by('pk').values()),'resources':list((obj.resource_allocations if legacy else obj.resource_allocations.filter(month__range=(start,end))).order_by('pk').values()),'month':obj.planning_month,'scenario':obj.scenario}
        for row in inputs['works']:
            if not row.get('selected_item_ids'):row.pop('selected_item_ids',None)
        if works_only:inputs.pop('resources',None)
        if not legacy:inputs['period']=[start,end]
        from .resource_targets import assignment_values
        assignments=assignment_values(obj)
        if assignments and not works_only:inputs['resource_assignments']=assignments
        from .resource_links import binding_values
        links=binding_values(obj)
        if links and not works_only:inputs['resource_work_links']=links
    # Catalog timestamps are audit data, not planning inputs.
    for rows in (works,items,prices):
        for row in rows:
            row.pop('updated_at',None);row.pop('created_at',None)
            if 'complex_object_id' in row and row['complex_object_id'] is None:row.pop('complex_object_id')
    return hashlib.sha256(json.dumps([inputs,works,items,prices],sort_keys=True,default=str).encode()).hexdigest()


def require_approved(obj):
    task=obj.estimate_reviews.order_by('-pk').first()
    if not check_valid(obj,task):
        raise ValidationError('Сначала передайте план сметчику и получите подтверждение цен, названий и состава работ. После изменений нужна повторная проверка.')
    return task


def check_valid(obj,task):
    if not task or task.status!='APPROVED':return False
    if task.fingerprint.startswith('works-v1:'):return task.fingerprint=='works-v1:'+signature(obj,works_only=True)
    return task.fingerprint==signature(obj) or isinstance(obj,DailySchedule) and task.fingerprint==signature(obj,legacy=True)


@transaction.atomic
def assign(user,obj,estimator):
    require_roles(user,PLAN_ROLES)
    if obj.company_id!=user.company_id:raise PermissionDenied
    editable(obj)
    if not estimator.is_active or estimator.company_id!=user.company_id or role_code(estimator) not in {'ESTIMATOR','ADMIN'}:raise ValidationError('Выберите действующего сметчика своей компании.')
    if isinstance(obj,DailySchedule):DailySchedule.objects.select_for_update().get(pk=obj.pk)
    else:GlobalPlanVersion.objects.select_for_update().only('pk').get(pk=obj.pk)
    task=obj.estimate_reviews.filter(status='OPEN').order_by('-pk').first()
    if task:
        task.assigned_to=estimator;task.save(update_fields=['assigned_to','updated_at'])
    else:
        task=EstimateReviewTask.objects.create(company=user.company,created_by=user,assigned_to=estimator,**({'schedule':obj} if isinstance(obj,DailySchedule) else {'version':obj}))
    event(task,user,'ASSIGN',{'estimator':estimator.pk})
    from .task_services import estimate_task,sync_version
    list(task.work_tasks.filter(kind='ESTIMATE').select_for_update().order_by('pk'))
    estimate_task(task,restart_closed=True)
    if task.version_id:sync_version(obj)
    return task


def event(task,user,action,details):
    EstimateReviewEvent.objects.create(company=user.company,task=task,actor=user,action=action,details=details)


def lock_task(user,task,*,editing=False):
    require_roles(user,{'ESTIMATOR','ADMIN'})
    task=EstimateReviewTask.objects.select_for_update().select_related('version','schedule__source').defer('version__snapshot','schedule__source__snapshot').get(pk=task.pk,company=user.company)
    if role_code(user)!='ADMIN' and task.assigned_to_id!=user.pk:raise PermissionDenied('Задание назначено другому сметчику.')
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
    if isinstance(obj,DailySchedule):
        if obj.status=='SUBMITTED':
            from .daily_schedule_approval import transition
            updated=transition(user,obj,'reject',comment)
            obj.status=updated.status
        return
    if obj.status!='SUBMITTED':return
    from .approval_workflow import current_review
    from .approval_context import authorized_transition
    from .models import GlobalPlanDecision
    review=current_review(obj)
    GlobalPlanDecision.objects.create(company=user.company,review=review,section='ESTIMATE',action='REJECT',actor=user,actor_name=user.get_full_name() or user.username,actor_role=role_code(user),comment=comment)
    obj.status='REJECTED';obj.comment=comment;obj.approved_at=None;obj.approved_by=None
    with authorized_transition(obj.pk):obj.save(update_fields=['status','comment','approved_at','approved_by','updated_at'])
    from .task_services import sync_version,create_repair
    sync_version(obj)
    create_repair(obj,user,comment)


def scoped_work(task,work_id):
    obj=target(task)
    if int(work_id) not in work_ids(obj):raise PermissionDenied('Работа не входит в задание.')
    return ProjectWork.objects.select_for_update().get(pk=work_id,company_id=task.company_id)


def decision_signature(work,obj=None):
    from .workspace_services import specification
    data=[specification(work)]
    if obj is not None:
        data.append(list(obj.entries.filter(kind='work',metadata__id=work.pk).values('rows','metadata','revision')) if isinstance(obj,DailySchedule) else list(review_allocations(obj).filter(work=work).order_by('month','pk').values()))
    return hashlib.sha256(json.dumps(data,sort_keys=True,default=str).encode()).hexdigest()


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
    task.decisions[str(work.pk)]={'approved':approved,'comment':comment.strip(),'signature':decision_signature(work,target(task))}
    task.save(update_fields=['decisions','updated_at']);event(task,user,'WORK_APPROVE' if approved else 'WORK_REJECT',{'work':work.pk,'comment':comment})
    return task


def retire_allocation(row):
    # Keep an explicit zero for forecast bases and transferred remainder receipts.
    if row.version.version_kind=='FORECAST' or row.version.work_carryovers.filter(month=row.month,work_id=row.work_id).exists():
        row.quantity=0;row.item_quantities={};row.daily_override=None;row.save()
    else:row.delete()


@transaction.atomic
def replacement_comment(user,task,work_id,replacement_id,comment):
    task=lock_task(user,task);work=scoped_work(task,work_id)
    if not replacement_id:return comment
    try:replacement_id=int(replacement_id)
    except (TypeError,ValueError):raise ValidationError('Выберите работу из списка рекомендаций.')
    from apps.works.planning_access import planning_works
    from .estimate_catalog import alternatives
    replacement=planning_works(alternatives(target(task)),user,whole=True).filter(pk=replacement_id,unit=work.unit,kind=work.kind).exclude(pk=work.pk).first()
    if replacement is None:raise ValidationError('Рекомендуемая работа недоступна или несовместима с исходной.')
    recommendation=f'Рекомендуемая замена: {replacement.section.construction_object.name} · {replacement.section.name} · {replacement.name} · {replacement.unit} · ID {replacement.pk}.'
    return recommendation+('\n'+comment.strip() if comment.strip() else '')


@transaction.atomic
def replace_work(user,task,work_id,replacement_id):
    """Merge duplicates in this draft only; facts and approved versions stay intact."""
    task=lock_task(user,task,editing=True);work=scoped_work(task,work_id);obj=target(task)
    from apps.works.planning_access import planning_works
    from .estimate_catalog import alternatives,local_work
    replacement=planning_works(alternatives(obj),user,whole=True).select_for_update().filter(pk=replacement_id).first()
    if replacement is None:raise ValidationError('Работа не принадлежит объекту или подключенной истории.')
    catalog_source=replacement.pk
    if not isinstance(obj,DailySchedule):replacement=local_work(user,obj,replacement)
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
    moved=Decimal(0)
    for row in review_allocations(obj).select_for_update().filter(work=work):
        moved+=row.quantity
        if row.daily_override is not None:raise ValidationError('Перед заменой проверьте сохраненный дневной план работы.')
        dest=obj.work_allocations.filter(work=replacement,month=row.month).first()
        quantities={str(mapping[k]):v for k,v in row.item_quantities.items()}
        if dest:
            if dest.daily_override is not None or dest.load_profile_id!=row.load_profile_id:raise ValidationError('У дублей разные профили или сохраненные дневные планы. Требуется ручное сопоставление.')
            if bool(dest.item_quantities)!=bool(quantities):raise ValidationError('У дублей разный способ задания объемов подработ.')
            dest.quantity+=row.quantity
            dest.item_quantities={k:str(Decimal(v)+Decimal(quantities.get(k,0))) for k,v in dest.item_quantities.items()} if dest.item_quantities else quantities
            dest.full_clean();dest.save()
            retire_allocation(row)
        else:
            if obj.version_kind=='FORECAST':
                WorkMonthAllocation.objects.create(company=user.company,version=obj,work=replacement,month=row.month,quantity=row.quantity,item_quantities=quantities,load_profile=row.load_profile)
                retire_allocation(row)
            else:
                destination=WorkMonthAllocation(company=user.company,version=obj,work=replacement,month=row.month,quantity=row.quantity,item_quantities=quantities,load_profile=row.load_profile)
                destination.full_clean();destination.save()
                retire_allocation(row)
    # Preserve provenance and expose source in the immutable audit log; receipts stay on original work.
    task.decisions.pop(str(work.pk),None);task.decisions.pop(str(replacement.pk),None);task.save(update_fields=['decisions','updated_at'])
    obj.snapshot={};obj.save(update_fields=['snapshot'])
    event(task,user,'REPLACE',{'source':work.pk,'target':replacement.pk,'source_name':work.name,'target_name':replacement.name,'catalog_source':catalog_source,'volume_adjustments':{str(work.pk):str(-moved),str(replacement.pk):str(moved)}})
    return task


@transaction.atomic
def review_selected(user,task,selected):
    task=lock_task(user,task)
    try:ids={int(value) for value in selected}
    except (ValueError,TypeError):raise ValidationError('Неверный выбор работ.')
    if not ids:raise ValidationError('Выберите работы для проверки.')
    if not ids<=work_ids(target(task)):raise PermissionDenied('Выбраны работы вне проверяемого периода.')
    for work_id in sorted(ids):review_work(user,task,work_id,True)
    return len(ids)


@transaction.atomic
def merge_duplicates(user,task,selected,replacement):
    task=lock_task(user,task)
    try:ids={int(value) for value in selected};destination=int(replacement)
    except (ValueError,TypeError):raise ValidationError('Выберите работы и итоговую работу.')
    if len(ids)<2 or destination not in ids:raise ValidationError('Выберите минимум две работы и итоговую работу из выбранных.')
    if not ids<=work_ids(target(task)):raise PermissionDenied('Выбраны работы вне проверяемого периода.')
    for source in sorted(ids-{destination}):replace_work(user,task,source,destination)
    return len(ids)-1


@transaction.atomic
def merge_composite(user,task,selected,values):
    from collections import defaultdict
    from apps.works.merge_service import source_works
    from apps.works.prices import history_prices,rate_on
    from apps.works.progress import work_specification,quantity_from_totals
    from .workspace_services import work_rows
    task=lock_task(user,task,editing=True);obj=target(task)
    if isinstance(obj,DailySchedule) or obj.workspace.parent_gpr_id:
        raise ValidationError('Создайте составную работу в ГПР, затем обновите СМГ.')
    works=source_works(user,selected,lock=True)
    if not {w.pk for w in works}<=work_ids(obj):raise PermissionDenied('Работы вне проверяемого периода.')
    if values['section'].construction_object_id!=obj.construction_object_id:raise ValidationError('Раздел другого объекта.')
    start,_=review_period(obj)
    price=sum((rate_on(history_prices(w),start,w.unit_price)*values[f'norm_{w.pk}'] for w in works),Decimal(0)).quantize(Decimal('.01'))
    parent=ProjectWork(company=user.company,section=values['section'],name=values['name'],unit=values['unit'],kind='COMPOSITE',allow_fractional=values['allow_fractional'],work_group=values.get('work_group'),unit_price=price)
    parent.full_clean();parent._price_actor=user;parent.save()
    items={}
    for index,work in enumerate(works):
        item=ProjectWorkItem(company=user.company,project_work=parent,name=work.name,unit=work.unit,quantity_per_unit=values[f'norm_{work.pk}'],weight=Decimal(100//len(works) if index<len(works)-1 else 100-(100//len(works))*(len(works)-1)),load_profile=values[f'profile_{work.pk}'],sequence=index)
        item.full_clean();item.save();items[work.pk]=item
    spec=work_specification(parent);months=defaultdict(list)
    for row in review_allocations(obj).select_for_update().filter(work_id__in=items):months[row.month].append(row)
    adjustments={str(w.pk):Decimal(0) for w in works}
    adjustments['target']=Decimal(0)
    for month,allocations in months.items():
        quantities={str(i.pk):'0' for i in items.values()};daily=[]
        for row in allocations:
            quantities[str(items[row.work_id].pk)]=str(row.quantity)
            source=ProjectWork.objects.get(pk=row.work_id)
            from .workspace_services import specification
            rows=row.daily_override if row.daily_override is not None else work_rows(obj.workspace,specification(source),month,row.quantity,row.load_profile,{})
            daily.extend({**r,'item_id':items[row.work_id].pk} for r in rows)
        allocation=WorkMonthAllocation(company=user.company,version=obj,work=parent,month=month,quantity=quantity_from_totals(spec,{int(k):Decimal(v) for k,v in quantities.items()}),item_quantities=quantities,daily_override=daily)
        allocation.full_clean();allocation.save()
        adjustments['target']+=allocation.quantity
        for row in allocations:adjustments[str(row.work_id)]-=row.quantity
        for row in allocations:
            retire_allocation(row)
    for work in works:task.decisions.pop(str(work.pk),None)
    task.save(update_fields=['decisions','updated_at']);obj.snapshot={};obj.save(update_fields=['snapshot'])
    adjustments[str(parent.pk)]=adjustments.pop('target')
    event(task,user,'MERGE_COMPOSITE',{'sources':[w.pk for w in works],'target':parent.pk,'norms':{str(w.pk):str(values[f'norm_{w.pk}']) for w in works},'start':start.isoformat(),'volume_adjustments':{k:str(v) for k,v in adjustments.items()}})
    return parent


def merge_volume_adjustments(version):
    """Move monthly goals with local merges, preserving future remainder proportions."""
    from collections import defaultdict
    ids=[];current=version;seen=set()
    while current and current.pk not in seen and current.workspace_id==version.workspace_id and current.version_kind=='FORECAST':
        seen.add(current.pk);ids.append(current.pk)
        current=GlobalPlanVersion.objects.only('pk','workspace_id','version_kind','previous_version_id').filter(pk=current.previous_version_id).first() if current.previous_version_id else None
    result=defaultdict(Decimal)
    for event in EstimateReviewEvent.objects.filter(company_id=version.company_id,task__version_id__in=ids,action__in=['REPLACE','MERGE_COMPOSITE']):
        for key,value in event.details.get('volume_adjustments',{}).items():result[int(key)]+=Decimal(value)
    return result


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
        from .task_services import require_no_remarks
        require_no_remarks(task)
        if isinstance(obj,GlobalPlanVersion) and obj.status=='SUBMITTED':
            from .approval_workflow import current_review
            review=current_review(obj)
            if review.input_fingerprint and review.input_fingerprint!=signature(obj) and review.input_fingerprint!=signature(obj,legacy=True):
                raise ValidationError('Справочник или план изменился после отправки. Верните план на доработку и отправьте обновленный расчет на повторное согласование.')
        for work in works:
            if work.kind=='COMPOSITE' and work.unit_price>0 and sum(work.items.values_list('weight',flat=True),Decimal(0))!=100:
                raise ValidationError(f'Для расчёта оценочной стоимости сумма весов работ в составе «{work.name}» должна быть 100%.')
            d=task.decisions.get(str(work.pk),{})
            if not d.get('approved') or d.get('signature')!=decision_signature(work,obj):raise ValidationError(f'Проверьте название, цену и подработы: {work.name}.')
        if isinstance(obj,DailySchedule):reprice_schedule(obj)
        task.status='APPROVED';task.fingerprint='works-v1:'+signature(obj,works_only=True);task.approved_at=timezone.now()
        if not isinstance(obj,DailySchedule) and obj.status=='SUBMITTED':
            from .approval_workflow import current_review
            from .models import GlobalPlanDecision
            review=current_review(obj)
            if not review.decisions.filter(section='ESTIMATE',action='APPROVE').exists():
                GlobalPlanDecision.objects.create(company=user.company,review=review,section='ESTIMATE',action='APPROVE',actor=user,actor_name=user.get_full_name() or user.username,actor_role=role_code(user),comment=comment or 'Названия, цены и состав работ проверены.')
    else:
        if not comment.strip():raise ValidationError('Укажите, что необходимо исправить.')
        return_to_draft(task,user,comment.strip())
        task.status='REJECTED';task.fingerprint=''
    task.comment=comment.strip();task.save();event(task,user,task.status,{'comment':comment})
    from .task_services import estimate_task,sync_version,create_repair
    estimate_task(task)
    if task.schedule_id:
        from .daily_schedule_approval import sync
        sync(obj)
    if task.version_id:
        sync_version(obj)
        if not approve:create_repair(obj,user,comment)
    return task
