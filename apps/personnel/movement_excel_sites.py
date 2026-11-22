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
    candidates=MovementReviewRequest.objects.filter(company=mapping.company,source='EXCEL',status__in=['OPEN','RETURNED']).values_list('pk','site','kind')
    matched=[(pk,kind) for pk,site,kind in candidates if MovementExcelSiteMapping.normalize(site)==mapping.source_site_normalized]
    changed=0
    for kind in ('ARRIVAL','DEPARTURE','TRANSFER'):
        ids=[pk for pk,row_kind in matched if row_kind==kind]
        if ids:changed+=MovementReviewRequest.objects.filter(pk__in=ids).update(**request_values(mapping,kind))
    return changed
