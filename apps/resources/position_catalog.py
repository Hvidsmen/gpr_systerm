"""Bridge the shared labor catalogue to legacy position references."""
from django.db import transaction
from .models import Brigade, Position, PositionPlanningBrigade


@transaction.atomic
def position_for(brigade):
    brigade = Brigade.objects.select_for_update().get(pk=brigade.pk)
    mapping = PositionPlanningBrigade.objects.filter(brigade=brigade, company=brigade.company).select_related('position').first()
    if mapping:
        position = mapping.position
        if position.name != brigade.name[:100]:
            position.name = brigade.name[:100]
            position.save(update_fields=['name'])
        return position
    position = Position.objects.create(company=brigade.company, code=f'CAT-{brigade.pk}', name=brigade.name[:100])
    PositionPlanningBrigade.objects.create(company=brigade.company, brigade=brigade, position=position)
    return position


@transaction.atomic
def brigade_for(position):
    position = Position.objects.select_for_update().get(pk=position.pk)
    mapping = PositionPlanningBrigade.objects.filter(position=position, company=position.company).first()
    if mapping:
        return mapping.brigade_id
    brigade = Brigade.objects.filter(company=position.company, name__iexact=position.name, position_mapping__isnull=True, merge_source__isnull=True).order_by('pk').first()
    if brigade is None:
        # bulk_create avoids the shared-catalogue creation signal until this legacy
        # position has been linked. Supply a stable unique code explicitly.
        brigade = Brigade(company=position.company, code=f'POS-{position.pk}', name=position.name)
        Brigade.objects.bulk_create([brigade])
    PositionPlanningBrigade.objects.create(company=position.company, brigade=brigade, position=position)
    return brigade.pk


def sync_catalog_position(sender, instance, raw=False, **kwargs):
    if not raw:
        position_for(instance)


def available_positions(company):
    return Position.objects.filter(company=company, planning_mapping__company=company,
        planning_mapping__brigade__company=company,
        planning_mapping__brigade__is_active=True,
        planning_mapping__brigade__merge_source__isnull=True).select_related('planning_mapping__brigade')


def position_label(position):
    return position.planning_mapping.brigade.name
