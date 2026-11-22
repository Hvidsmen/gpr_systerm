"""Maintain explicit Excel site mappings and apply them to pending movement rows."""
from django.db import transaction
from django.utils import timezone
from .models import MovementExcelSiteMapping,MovementReviewRequest


def request_values(mapping,kind):
    exact=mapping.construction_object
    project_object=exact.parent if exact.parent_id else exact
    values={'construction_object_id':project_object.pk,'site_object_id':exact.pk,'updated_at':timezone.now()}
    if kind=='ARRIVAL':values.update(to_camp_id=mapping.camp_id)
    else:values.update(from_camp_id=mapping.camp_id)
    return values


@transaction.atomic
def apply_to_pending(mapping):
    """Apply a saved rule to matching, not-yet-processed Excel requests."""
    return apply_many_to_pending([mapping])


@transaction.atomic
def apply_many_to_pending(mappings):
    """Apply several saved rules after scanning pending Excel requests once."""
    mappings=list(mappings)
    if not mappings:return 0
    company=mappings[0].company
    by_source={mapping.source_site_normalized:mapping for mapping in mappings}
    candidates=MovementReviewRequest.objects.filter(company=company,source='EXCEL',status__in=['OPEN','RETURNED']).values_list('pk','site','kind')
    matched={key:{kind:[] for kind in ('ARRIVAL','DEPARTURE','TRANSFER')} for key in by_source}
    for pk,site,kind in candidates:
        key=MovementExcelSiteMapping.normalize(site)
        if key in matched and kind in matched[key]:matched[key][kind].append(pk)
    changed=0
    for key,mapping in by_source.items():
        for kind,ids in matched[key].items():
            if ids:changed+=MovementReviewRequest.objects.filter(pk__in=ids).update(**request_values(mapping,kind))
    return changed
