from copy import deepcopy
from decimal import Decimal
from django.core.exceptions import ValidationError, PermissionDenied
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from core.permissions import PLAN_ROLES, require_roles, role_code
from .models import CarryoverReviewTask, GlobalPlanVersion, GlobalPlanReview, WorkMonthAllocation, ResourceMonthAllocation, WorkCarryover
from .workspace_services import WorkspaceService
from . import carryover


def tasks_for(user, *, due=True):
    require_roles(user,PLAN_ROLES)
    query=CarryoverReviewTask.objects.filter(company=user.company).select_related('assigned_to','version__construction_object','correction_version','source_version').defer('version__snapshot','correction_version__snapshot','source_version__snapshot')
    from core.permissions import scope_queryset
    query=scope_queryset(query,user)
    if role_code(user)!='ADMIN':query=query.filter(assigned_to=user)
    if due:query=query.filter(due_date__lte=timezone.localdate())
    return query.order_by('due_date','pk')


def is_closed(task):
    return bool(task.correction_version_id and task.correction_version.status in {'APPROVED','COMPLETED'})


def candidate(task):
    if task.correction_version_id:return deepcopy(task.correction_version)
    version=task.version
    if version.status in {'DRAFT','REJECTED'}:return deepcopy(version)
    # Preview needs an editable facade; the original approved plan is never saved.
    version=deepcopy(version);version.status='DRAFT'
    return version


def preview(task):
    if timezone.localdate()<task.due_date:raise ValidationError('Сверка доступна после закрытия предыдущего месяца.')
    version=candidate(task)
    version.status='DRAFT' # read-only calculation even while the correction is submitted.
    data=carryover.preview(version,task.month,task.source_version,include_zero=True)
    receipts_query=task.version.work_carryovers.filter(month=task.month)
    if task.source_version_id:receipts_query=receipts_query.filter(source_version=task.source_version)
    receipts={r.work_id:r for r in receipts_query}
    current={r.work_id:r for r in (task.correction_version or task.version).work_allocations.filter(month=task.month)}
    finalized={r.work_id:r for r in task.correction_version.work_carryovers.filter(month=task.month)} if task.correction_version_id else receipts
    result=[]
    for row in data:
        receipt=receipts.get(row['work_id'])
        if not receipt:continue
        row['forecast_quantity']=str(receipt.forecast_quantity if receipt.forecast_quantity is not None else receipt.quantity)
        row['change']=str(Decimal(row['quantity'])-Decimal(row['forecast_quantity']))
        row['increased']=Decimal(row['change'])>0
        row['was_added']=receipt.added
        row['manual_changed']=bool(receipt.added and row['work_id'] in current and current[row['work_id']].quantity!=finalized.get(row['work_id'],receipt).quantity)
        result.append(row)
    return result


def clone_revision(user,source,month):
    if source.status=='SUBMITTED':raise ValidationError('План еще на согласовании. Дождитесь решения или возврата на доработку.')
    if source.status in {'DRAFT','REJECTED'}:return source
    scenario=source.scenario if source.version_kind=='FORECAST' else 'REMAINING'
    if source.workspace.is_macro_consolidation and source.version_kind=='BASELINE':
        # Finalizing a transferred forecast changes the new month's total;
        # keep the other months of the approved macro baseline intact.
        scenario='BASELINE'
    version=WorkspaceService.new_version(source.workspace,user,'FORECAST',month,scenario,previous=source)
    review_id=source.baseline_review_id
    if not review_id:
        base_id=source.workspace.baseline_version_id
        base=GlobalPlanVersion.objects.only('approval_round').get(pk=base_id)
        review_id=GlobalPlanReview.objects.filter(version_id=base_id,number=base.approval_round).values_list('pk',flat=True).first()
    if not review_id:raise ValidationError('У исходного ГПР отсутствует согласованный раунд базы.')
    version.baseline_review_id=review_id;version.comment='Корректировка прогнозных остатков по закрытому факту.';version.save(update_fields=['baseline_review','comment'])
    for model in [WorkMonthAllocation,ResourceMonthAllocation]:
        query=model.objects.filter(version=source,month__gte=month)
        if scenario=='BASELINE' or source.version_kind=='BASELINE':query=query.filter(month=month)
        fields=[f.attname for f in model._meta.fields if f.name not in {'id','created_at','updated_at','company','version'}]
        copies=[model(company=user.company,version=version,**{key:deepcopy(getattr(row,key)) for key in fields}) for row in query.iterator()]
        model.objects.bulk_create(copies,batch_size=200)
    if source.workspace.is_macro_consolidation and scenario=='BASELINE':
        from .report_projection import period_snapshot
        snapshot=period_snapshot(source,month,month)
        existing=set(version.work_allocations.values_list('work_id',flat=True))
        WorkMonthAllocation.objects.bulk_create([
            WorkMonthAllocation(company=user.company,version=version,work_id=spec['id'],month=month,quantity=0)
            for spec in snapshot.get('works',[]) if not spec.get('history_source_object_id') and spec['id'] not in existing
        ])
    fields=[f.attname for f in WorkCarryover._meta.fields if f.name not in {'id','created_at','updated_at','company','version'}]
    WorkCarryover.objects.bulk_create([WorkCarryover(company=user.company,version=version,**{key:deepcopy(getattr(row,key)) for key in fields}) for row in source.work_carryovers.all()],batch_size=200)
    return version


@transaction.atomic
def reconcile(user,task,selected):
    require_roles(user,PLAN_ROLES)
    task=CarryoverReviewTask.objects.select_for_update().select_related('version','source_version','correction_version').defer('version__snapshot','source_version__snapshot','correction_version__snapshot').get(pk=task.pk,company=user.company)
    if role_code(user)!='ADMIN' and task.assigned_to_id!=user.pk:raise PermissionDenied
    if timezone.localdate()<task.due_date:raise ValidationError('Сверка доступна с первого числа планируемого месяца.')
    if task.correction_version_id and task.correction_version.status not in {'DRAFT','REJECTED'}:raise ValidationError('Корректировка уже отправлена на согласование или утверждена.')
    rows=preview(task)
    # All forecast receipts must be checked together, including zero final remainders.
    if set(selected)!={str(r['work_id']) for r in rows}:raise ValidationError('Для закрытия прогноза проверьте все работы задания.')
    version=task.correction_version or clone_revision(user,task.version,task.month)
    GlobalPlanVersion.objects.select_for_update().only("pk").get(pk=version.pk)
    for data in rows:
        receipt=WorkCarryover.objects.get(version=version,month=task.month,work_id=data['work_id'])
        row=version.work_allocations.filter(month=task.month,work_id=data['work_id']).first()
        final=Decimal(data['quantity']).quantize(Decimal('.001'))
        # Preserve manual planning edits; the finalized remainder remains visible in its note.
        if receipt.added and (row is None or row.quantity==receipt.quantity and (not row.item_quantities or {k:Decimal(v) for k,v in row.item_quantities.items()}=={k:Decimal(v) for k,v in receipt.item_quantities.items()})):
            if row is None:row=WorkMonthAllocation(company=user.company,version=version,month=task.month,work_id=data['work_id'])
            if row.daily_override is not None:raise ValidationError(f'«{data["name"]}»: сначала проверьте сохраненный дневной план.')
            row.quantity=final;row.item_quantities=data['items'];row.full_clean();row.save()
        receipt.quantity=final;receipt.item_quantities=data['items'];receipt.is_forecast=False
        receipt.actual_quantity=Decimal(data['actual']).quantize(Decimal('.001'));receipt.expected_completion=receipt.actual_quantity
        receipt.save(update_fields=['quantity','item_quantities','is_forecast','actual_quantity','expected_completion'])
    version.snapshot={};version.save(update_fields=['snapshot'])
    task.correction_version=version;task.corrected_at=timezone.now();task.save(update_fields=['correction_version','corrected_at','updated_at'])
    return version
