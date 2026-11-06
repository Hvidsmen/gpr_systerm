"""Provenance-aware daily crew work assignments."""
from django.core.exceptions import ValidationError
from .dispatch_models import EmployeeWorkLink, ProductionDailyCrewMember, ProductionDailyCrewWorkLink


def _links(activity,fact):
    if fact.operational_key:return activity.work_links.filter(fact=fact)
    return activity.work_links.filter(work_id=fact.project_work_id,work_item_id=fact.work_item_id,fact=None)


def _ensure_link(activity,fact,*,individual=False,crew=None):
    link,created=EmployeeWorkLink.objects.get_or_create(
        company=activity.company,activity=activity,work_id=fact.project_work_id,
        work_item_id=fact.work_item_id,fact=fact if fact.operational_key else None,
        defaults={'assigned_individually':individual,'crew_source':crew},
    )
    fields=[]
    if individual and not link.assigned_individually:link.assigned_individually=True;fields.append('assigned_individually')
    if crew and link.crew_source_id!=crew.pk:link.crew_source=crew;fields.append('crew_source')
    if fields:link.save(update_fields=fields)
    return link


def change_individual(activity,fact,action):
    if action=='add':_ensure_link(activity,fact,individual=True);return
    for link in _links(activity,fact):
        if link.crew_source_id:
            if link.assigned_individually:
                link.assigned_individually=False;link.save(update_fields=['assigned_individually'])
        else:link.delete()


def clear_individual(activity):
    for link in activity.work_links.all():
        if link.crew_source_id:
            if link.assigned_individually:
                link.assigned_individually=False;link.save(update_fields=['assigned_individually'])
        else:link.delete()


def change_crew(activity,crew,fact,action):
    if not ProductionDailyCrewMember.objects.filter(company=activity.company,crew=crew,activity=activity).exists():
        raise ValidationError('Сотрудник уже не состоит в этой бригаде.')
    if action=='add':
        ProductionDailyCrewWorkLink.objects.get_or_create(company=activity.company,crew=crew,fact=fact)
        _ensure_link(activity,fact,crew=crew)
    else:
        ProductionDailyCrewWorkLink.objects.filter(company=activity.company,crew=crew,fact=fact).delete()
        _detach_fact(activity,crew,fact)


def _detach_fact(activity,crew,fact):
    for link in _links(activity,fact).filter(crew_source=crew):
        if link.assigned_individually:
            link.crew_source=None;link.save(update_fields=['crew_source'])
        else:link.delete()


def attach_member(crew,activity):
    changed=False
    for assignment in crew.work_assignments.select_related('fact__project_work__section'):
        before=_links(activity,assignment.fact).filter(crew_source=crew).exists()
        _ensure_link(activity,assignment.fact,crew=crew);changed=changed or not before
    if changed:_refresh(activity)
    return changed


def detach_member(crew,activity):
    links=list(activity.work_links.filter(crew_source=crew))
    for link in links:
        if link.assigned_individually:
            link.crew_source=None;link.save(update_fields=['crew_source'])
        else:link.delete()
    if links:_refresh(activity)
    return bool(links)


def delete_crew_assignments(crew):
    for membership in crew.members.select_related('activity'):
        detach_member(crew,membership.activity)
    crew.work_assignments.all().delete()


def _refresh(activity):
    from .collaboration import apply_relations
    apply_relations(activity)
