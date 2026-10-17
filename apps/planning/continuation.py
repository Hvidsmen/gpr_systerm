"""Extend a GPR without recalculating approved historical months from current catalogs."""
from calendar import monthrange
from copy import deepcopy
from datetime import timedelta
from decimal import Decimal
from django.core.exceptions import ValidationError,PermissionDenied
from django.db import transaction
from core.permissions import require_roles,PLAN_ROLES
from apps.works.prices import rate_on
from .models import GlobalPlanVersion,PlanningWorkspace
from .approval_workflow import current_review


@transaction.atomic
def continue_gpr(user,source,name,end):
    from .workspace_services import WorkspaceService,months_between
    require_roles(user,PLAN_ROLES)
    source=GlobalPlanVersion.objects.select_for_update().defer('snapshot').get(pk=source.pk,company_id=user.company_id)
    if source.status not in {'APPROVED','COMPLETED'}:raise ValidationError('Продолжить можно только утвержденный или завершенный ГПР.')
    if source.workspace_id and source.workspace.parent_gpr_id:raise ValidationError('Для СМГ субобъекта создайте новый месячный СМГ.')
    review=source.review_rounds.defer("snapshot").filter(number=source.approval_round).first()
    if review:review.version=source
    if not review or not review.was_approved:raise ValidationError('У исходного плана нет утвержденного раунда согласования.')
    start=source.end_date+timedelta(days=1)
    if start.day!=1:raise ValidationError('Исходный ГПР должен заканчиваться последним днем месяца.')
    if end<start or end.day!=monthrange(end.year,end.month)[1]:raise ValidationError('Выберите конец нового периода — последний день месяца после исходного ГПР.')
    if len(months_between(source.start_date,end))>120:raise ValidationError('Общий период не должен превышать десять лет.')
    workspace=PlanningWorkspace(company=user.company,construction_object=source.construction_object,name=name,start_date=source.start_date,end_date=end,continued_from_review=review,continuation_start=start)
    workspace.full_clean();workspace.save()
    version=WorkspaceService.new_version(workspace,user,'BASELINE',previous=source)
    workspace.baseline_version=version;workspace.save(update_fields=['baseline_version'])
    return workspace


def merge_history(version,snapshot):
    """Read the frozen source only when building the combined snapshot, never at creation."""
    from .workspace_services import finalize_work
    workspace=version.workspace
    cutoff=workspace.continuation_start.isoformat()
    parent=workspace.continued_from_review.snapshot
    return merge_parent(version,snapshot,parent,cutoff)


def merge_parent(version,snapshot,parent,cutoff):
    from .workspace_services import finalize_work
    workspace=version.workspace
    historical={s['id']:s for s in parent.get('works',[])}
    current={s['id']:s for s in snapshot.get('works',[])}
    result=[]
    for wid in sorted(historical.keys()|current.keys()):
        old=historical.get(wid);new=current.get(wid)
        if old is None:
            result.append(new);continue
        old_rows=[r for r in old.get('plans',[]) if r['date']<cutoff]
        old_daily=[r for r in old.get('daily',[]) if r['date']<cutoff]
        # A forecast can contain old fact rows: replace that portion by the exact approved GPR.
        new_rows=[r for r in new.get('plans',[]) if r['date']>=cutoff] if new else []
        if not new_rows:
            spec=deepcopy(old);spec['plans']=deepcopy(old_rows);spec['daily']=deepcopy(old_daily);result.append(spec);continue
        def shape(s):return (s.get('kind'),s.get('unit'),[(i['id'],i.get('unit'),Decimal(i['norm']),Decimal(i.get('weight',0))) for i in s.get('items',[])])
        if shape(old)!=shape(new):raise ValidationError(f'Изменился состав работы «{old["name"]}». Для нового состава заведите отдельную работу.')
        # Continue the cumulative series using frozen historical quantities.
        spec=finalize_work(new,new_rows,workspace,old.get('context_plans',[])+old_rows)
        spec['plans']=deepcopy(old_rows)+spec['plans']
        spec['daily']=deepcopy(old_daily)+[r for r in spec['daily'] if r['date']>=cutoff]
        spec['context_plans']=deepcopy(old.get('context_plans',[]))
        prices=[deepcopy(p) for p in old.get('revenue_prices',[]) if p['effective_from']<cutoff]
        if not prices:prices=[{'effective_from':'0001-01-01','price':str(old.get('unit_price',0))}]
        prices.append({'effective_from':cutoff,'price':str(rate_on(new.get('revenue_prices',[]),workspace.continuation_start,Decimal(new.get('unit_price',0))))})
        prices.extend(deepcopy(p) for p in new.get('revenue_prices',[]) if p['effective_from']>=cutoff)
        spec['revenue_prices']=prices
        result.append(spec)
    snapshot['works']=result
    for kind in ('labor','equipment','fuel'):
        snapshot.setdefault('resources',{})[kind]=deepcopy([r for r in parent.get('resources',{}).get(kind,[]) if r['date']<cutoff])+[r for r in snapshot.get('resources',{}).get(kind,[]) if r['date']>=cutoff]
    inputs=snapshot.setdefault('monthly_inputs',{})
    for kind in ('works','resources'):
        inputs[kind]=deepcopy([r for r in parent.get('monthly_inputs',{}).get(kind,[]) if r['month']<cutoff])+[r for r in inputs.get(kind,[]) if r['month']>=cutoff]
    if workspace.continued_from_review_id:snapshot['continued_from_review_id']=workspace.continued_from_review_id
    if parent.get('macro_history_sources'):snapshot['macro_history_sources']=deepcopy(parent['macro_history_sources'])
    snapshot['continuation_start']=cutoff
    return snapshot
