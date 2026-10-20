"""Transition from several sub-object GPRs to one macro GPR with frozen provenance."""
from copy import deepcopy
from datetime import timedelta
from calendar import monthrange
from django.core.exceptions import ValidationError,PermissionDenied
from django.db import transaction
from core.permissions import require_roles,PLAN_ROLES
from apps.projects.models import ConstructionObject
from .models import GlobalPlanVersion,PlanningWorkspace,MacroHistorySource
from .workspace_services import WorkspaceService,months_between


@transaction.atomic
def create_macro(user,macro,sources,name,start,new_start,end):
    require_roles(user,PLAN_ROLES)
    from core.permissions import require_planning_object
    require_planning_object(user,macro.pk)
    for source in sources:require_planning_object(user,source.construction_object_id)
    macro=ConstructionObject.objects.select_for_update().get(pk=macro.pk,company=user.company)
    if not sources:raise ValidationError('Выберите хотя бы один утвержденный ГПР субобъекта.')
    if start.day!=1 or new_start.day!=1 or not start<new_start<=end or end.day!=monthrange(end.year,end.month)[1] or len(months_between(start,end))>120:raise ValidationError('Укажите начало истории, начало нового месяца и конец общего периода (до десяти лет).')
    history_end=new_start-timedelta(days=1)
    if len({s.pk for s in sources})!=len(sources):raise ValidationError('Для каждого субобъекта выберите одну версию ГПР.')
    versions=list(GlobalPlanVersion.objects.select_for_update().select_related('construction_object').defer('snapshot').filter(pk__in=[s.pk for s in sources],company=user.company))
    if len(versions)!=len(sources):raise PermissionDenied('Источник другой компании или отсутствует.')
    if len({v.construction_object_id for v in versions})!=len(versions):raise ValidationError('Для каждого субобъекта выберите одну версию ГПР.')
    reviews=[]
    for v in versions:
        child=v.construction_object
        if child.pk==macro.pk or child.project_id!=macro.project_id or child.parent_id not in {None,macro.pk}:raise ValidationError('Источники должны быть субобъектами выбранного макрообъекта в том же проекте.')
        if v.status not in {'APPROVED','COMPLETED'} or v.start_date>start or v.end_date<history_end:raise ValidationError('Каждый источник должен быть утвержден и охватывать весь выбранный исторический период.')
        if v.workspace_id and v.workspace.parent_gpr_id:raise ValidationError('Выберите самостоятельный ГПР, а не месячный СМГ.')
        review=v.review_rounds.defer('snapshot').filter(number=v.approval_round).first()
        if not review or not review.was_approved:raise ValidationError('У источника нет утвержденного раунда согласования.')
        reviews.append((v,review))
    workspace=PlanningWorkspace(company=user.company,construction_object=macro,name=name,start_date=start,end_date=end,continuation_start=new_start,is_macro_consolidation=True)
    workspace.full_clean();workspace.save()
    version=WorkspaceService.new_version(workspace,user,'BASELINE')
    workspace.baseline_version=version;workspace.save(update_fields=['baseline_version'])
    for v,review in reviews:
        child=v.construction_object
        if child.parent_id is None:
            child.parent=macro;child.full_clean();child.save(update_fields=['parent'])
        MacroHistorySource.objects.create(company=user.company,workspace=workspace,review=review,construction_object=child,start=start,end=history_end)
    return workspace


def history_links(workspace):
    return workspace.macro_history_sources.select_related('construction_object','review__version').defer('review__snapshot','review__version__snapshot').order_by('construction_object__name')


def merge_macro_history(version,snapshot):
    from .continuation import merge_parent
    from .report_projection import period_snapshot
    parent={'works':[],'resources':{k:[] for k in ('labor','equipment','fuel')},'monthly_inputs':{'works':[],'resources':[]}}
    provenance=[]
    for link in history_links(version.workspace):
        source=link.review.version
        # Project the exact chosen approval round even if its version is later resubmitted.
        frozen=period_snapshot(source,link.start,link.end,member=link.review,source_key=f'macro-history-{link.review_id}')
        for original in frozen.get('works',[]):
            spec=deepcopy(original)
            spec['plans']=[r for r in spec.get('plans',[]) if link.start.isoformat()<=r['date']<=link.end.isoformat()]
            spec['daily']=[r for r in spec.get('daily',[]) if link.start.isoformat()<=r['date']<=link.end.isoformat()]
            spec['history_source_object_id']=link.construction_object_id
            spec['history_end']=link.end.isoformat()
            spec['section_name']=f"{link.construction_object.name} · {spec.get('section_name','История')}"
            parent['works'].append(spec)
        for kind in parent['resources']:
            parent['resources'][kind].extend({**r,'history_source_object_id':link.construction_object_id} for r in frozen.get('resources',{}).get(kind,[]) if link.start.isoformat()<=r['date']<=link.end.isoformat())
        for kind in ('works','resources'):
            parent['monthly_inputs'][kind].extend(r for r in frozen.get('monthly_inputs',{}).get(kind,[]) if link.start.isoformat()<=r['month']<=link.end.isoformat())
        provenance.append({'object_id':link.construction_object_id,'name':link.construction_object.name,'review_id':link.review_id,'version_id':source.pk,'start':link.start.isoformat(),'end':link.end.isoformat()})
    merged=merge_parent(version,snapshot,parent,version.workspace.continuation_start.isoformat())
    merged['macro_history_sources']=provenance
    return merged


def history_coverage(objects,sources,days):
    """Only an actually selected macro source owns a child's dates; child-only reports stay intact."""
    selected={o.pk for o in objects};covered={}
    for macro_id,entries in sources.items():
        if macro_id not in selected:continue
        for day in days:
            source=next((s for s in entries if s.start<=day<=s.end),None)
            if not source:continue
            for link in source.snapshot.get('macro_history_sources',[]):
                covered.setdefault(link['object_id'],set()).add(day)
    return covered


def rollup_history_resources(user,sources,start,end,resource_facts):
    from apps.production.models import LaborFact,EquipmentFact,FuelFact
    seen=set()
    for macro_id,entries in sources.items():
        for source in entries:
            for link in source.snapshot.get('macro_history_sources',[]):
                from datetime import date
                a=max(start,date.fromisoformat(link['start']),source.start);b=min(end,date.fromisoformat(link['end']),source.end)
                if a>b:continue
                for model,kind in [(LaborFact,'labor'),(EquipmentFact,'equipment'),(FuelFact,'fuel')]:
                    for row in model.objects.filter(company=user.company,construction_object_id=link['object_id'],date__range=(a,b)).values():
                        active=next((s for s in entries if s.start<=row['date']<=s.end),None)
                        identity=(macro_id,kind,row['id'])
                        if active is not source or identity in seen:continue
                        seen.add(identity)
                        key=(macro_id,row['brigade_id']) if kind=='labor' else (macro_id,row['equipment_type_id'],row['equipment_number']) if kind=='equipment' else (macro_id,row['fuel_type'])
                        field='actual_workers' if kind=='labor' else 'actual_count' if kind=='equipment' else 'actual_liters'
                        resource_facts[kind][key][row['date']]+=row[field] or 0
                        if kind=='fuel':resource_facts['balance'][key][row['date']]+=row['actual_balance'] or 0
