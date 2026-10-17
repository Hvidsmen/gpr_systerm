"""Catalog alternatives from the plan object and explicitly connected history."""
from apps.works.models import ProjectWork
from .models import DailySchedule,MacroHistorySource,GlobalPlanReview,GlobalPlanVersion


def catalog_versions(obj):
    version=obj.source if isinstance(obj,DailySchedule) else obj
    result=[];seen=set()
    while version and version.workspace_id and version.workspace_id not in seen:
        seen.add(version.workspace_id);result.append(version)
        workspace=version.workspace
        if workspace.continued_from_review_id:
            source_id=GlobalPlanReview.objects.filter(pk=workspace.continued_from_review_id).values_list('version_id',flat=True).get()
            version=GlobalPlanVersion.objects.defer('snapshot').get(pk=source_id,company_id=version.company_id)
        else:version=None
    return result


def alternatives(obj):
    version=obj.source if isinstance(obj,DailySchedule) else obj
    objects={version.construction_object_id}
    objects.update(MacroHistorySource.objects.filter(company_id=version.company_id,workspace_id__in=[v.workspace_id for v in catalog_versions(obj)]).values_list('construction_object_id',flat=True))
    return ProjectWork.objects.filter(company_id=version.company_id,section__construction_object_id__in=objects,merged_source__isnull=True).select_related('section__construction_object').order_by('section__construction_object__name','section__name','name','pk')


def local_work(user,version,source):
    if source.section.construction_object_id==version.construction_object_id:return source
    from .macro_carryover import ensure_work
    from .estimate_review import review_period
    from apps.works.progress import work_specification
    from apps.works.prices import history_prices
    spec=work_specification(source)
    spec['revenue_prices']=[{'effective_from':p.effective_from.isoformat(),'price':str(p.price)} for p in history_prices(source)]
    data={'source_work_id':source.pk,'work_id':source.pk,'items':{},'spec':spec}
    data=ensure_work(user,version,review_period(version)[0],data,catalog=True)
    return ProjectWork.objects.select_for_update().select_related('section__construction_object').get(pk=data['work_id'],company=user.company)
