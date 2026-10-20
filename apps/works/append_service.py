"""Append existing simple works to a composite, preserving frozen GPR history."""
from copy import deepcopy
from collections import defaultdict
from decimal import Decimal
import hashlib, json
from django.core.exceptions import ValidationError
from django.db import transaction
from core.permissions import scope_queryset, require_roles, PLAN_ROLES
from .models import ProjectWork
from .merge_service import source_works, fingerprint, apply_merge
from .progress import work_specification, cumulative_series, quantity_from_totals, WorkProgressService


def validate_target(user, pk, works, lock=False):
    query = scope_queryset(ProjectWork.objects.filter(kind='COMPOSITE', merged_source__isnull=True),user)
    if lock:query=query.select_for_update()
    try:target=query.get(pk=pk,section__construction_object_id=works[0].section.construction_object_id)
    except (ProjectWork.DoesNotExist,ValueError,TypeError):raise ValidationError('Выберите доступный комплекс того же объекта.')
    if target.monthly_plans.exists():
        raise ValidationError('У комплекса есть помесячные планы старого формата. Добавление в такой комплекс пока недоступно: требуется перенос его истории в ГПР.')
    from apps.planning.models import GlobalPlanVersion
    ids={target.pk,*[w.pk for w in works]}
    for version in GlobalPlanVersion.objects.exclude(workspace__is_archived=True).filter(company=user.company,construction_object_id=target.section.construction_object_id,status='SUBMITTED'):
        if version.work_allocations.filter(work_id__in=ids).exists() or any(spec['id'] in ids for spec in version.snapshot.get('works',[])):
            raise ValidationError('Связанный ГПР находится на согласовании. Сначала верните его на доработку.')
    if target.daily_facts.filter(work_item__isnull=True).exists():
        raise ValidationError('У комплекса есть факт без указания работы в составе. Сначала исправьте эти записи.')
    return target


def append_fingerprint(works,target,values):
    from apps.planning.models import MonthlyPlan,PlanVersion
    from apps.planning.workspace_services import captured_profile
    from .models import WorkPrice
    ids=[target.pk,*[w.pk for w in works]]
    profiles={str(w.pk):captured_profile(values[f'profile_{w.pk}']) for w in works}
    data={'base':fingerprint([*works,target]),'items':list(target.items.order_by('pk').values()),
          'prices':list(WorkPrice.objects.filter(work_id__in=ids).order_by('pk').values()),
          'monthly':list(MonthlyPlan.objects.filter(project_work_id__in=ids).order_by('pk').values()),
          'legacy_versions':list(PlanVersion.objects.filter(monthly_plan__project_work_id__in=ids).order_by('pk').values()),'profiles':profiles}
    return hashlib.sha256(json.dumps(data,sort_keys=True,default=str).encode()).hexdigest()


def append_preview(user,works,values):
    from apps.production.models import DailyFact
    from apps.planning.models import GlobalPlanVersion,WorkMonthAllocation,MonthlyPlan
    from apps.planning.global_services import build_snapshot
    target=validate_target(user,values['target'].pk,works)
    spec=work_specification(target)
    before=deepcopy(spec)
    spec['items'] += [{'id':-w.pk,'name':w.name,'unit':w.unit,'norm':str(values[f'norm_{w.pk}']),'weight':'0'} for w in works]
    facts=list(DailyFact.objects.filter(company=user.company,project_work_id__in=[target.pk,*[w.pk for w in works]]).order_by('date','pk'))
    rows=[(r.date,r.work_item_id if r.project_work_id==target.pk else -r.project_work_id,r.actual_quantity) for r in facts]
    series=cumulative_series(spec,rows)
    after=next(reversed(series.values()))['cumulative'] if series else Decimal(0)
    sources=[{'work':w,'actual':sum((r.actual_quantity for r in facts if r.project_work_id==w.pk),Decimal(0)), 'norm':values[f'norm_{w.pk}']} for w in works]
    plans=[]
    ids={target.pk,*[w.pk for w in works]}
    for version in GlobalPlanVersion.objects.exclude(workspace__is_archived=True).filter(company=user.company,construction_object_id=target.section.construction_object_id).order_by('pk'):
        if not (version.work_allocations.filter(work_id__in=ids).exists() or any(s['id'] in ids for s in version.snapshot.get('works',[]))):continue
        snapshot=build_snapshot(version) if version.status in {'DRAFT','REJECTED'} else version.snapshot
        specs={s['id']:s for s in snapshot.get('works',[])}
        totals=defaultdict(lambda:defaultdict(Decimal))
        for wid, original in specs.items():
            if wid not in ids:continue
            for row in original.get('plans',[]):
                item_id=row.get('item_id') if wid==target.pk else -wid
                totals[row['date'][:7]][item_id]+=Decimal(row['quantity'])
        allocations={(row.work_id,row.month.strftime('%Y-%m')):row for row in version.work_allocations.filter(work_id__in=ids)}
        months=set(totals)|{key[1] for key in allocations}
        for month in sorted(months):
            quantities=totals[month]
            old=allocations.get((target.pk,month))
            if old:
                if old.item_quantities:quantities.update({int(k):Decimal(v) for k,v in old.item_quantities.items()})
                else:quantities.update({i['id']:old.quantity*Decimal(i['norm']) for i in specs.get(target.pk,{}).get('items',[])})
            parts=[]
            for work in works:
                allocation=allocations.get((work.pk,month))
                if allocation:quantities[-work.pk]=allocation.quantity
                parts.append({'work':work,'quantity':quantities.get(-work.pk,Decimal(0))})
            plans.append({'version':version,'month':month,'before':old.quantity if old else quantity_from_totals(before,quantities) if before['items'] else Decimal(0),'after':quantity_from_totals(spec,quantities),'parts':parts})
    # Older individual monthly plans are preserved and copied into a draft of the target.
    legacy=list(MonthlyPlan.objects.filter(company=user.company,project_work__in=works).select_related('project_work').order_by('year','month','pk'))
    return {'target':target,'sources':sources,'plans':plans,'legacy_plans':legacy,'fact_before':WorkProgressService.completed(target) if before['items'] else Decimal(0),'fact_after':after,'fingerprint':append_fingerprint(works,target,values)}


@transaction.atomic
def apply_append(user, ids, values, expected):
    from apps.projects.models import ConstructionObject
    require_roles(user,PLAN_ROLES)
    works=source_works(user,ids,lock=True,minimum=1)
    ConstructionObject.objects.select_for_update().get(pk=works[0].section.construction_object_id,company=user.company)
    target=validate_target(user,values['target'].pk,works,lock=True)
    list(target.items.select_for_update().order_by('pk'))
    from apps.planning.models import GlobalPlanVersion, WorkMonthAllocation
    from apps.production.models import DailyFact
    locked_ids=[target.pk,*[work.pk for work in works]]
    list(GlobalPlanVersion.objects.select_for_update().filter(company=user.company,construction_object_id=target.section.construction_object_id).order_by('pk').values_list('pk',flat=True))
    list(WorkMonthAllocation.objects.select_for_update().filter(company=user.company,work_id__in=locked_ids).order_by('pk').values_list('pk',flat=True))
    list(DailyFact.objects.select_for_update().filter(company=user.company,project_work_id__in=locked_ids).order_by('pk').values_list('pk',flat=True))
    if append_fingerprint(works,target,values)!=expected:
        raise ValidationError('Состав комплекса, планы, цены или факт изменились после просмотра. Выполните просмотр заново.')
    return apply_merge(user,ids,values,fingerprint(works),minimum=1,target=target)
