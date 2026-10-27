"""Company-scoped catalogue targets, including merged resource aliases."""
from functools import lru_cache
from urllib.parse import urlencode
from django.urls import reverse
from apps.resources.models import Brigade, EquipmentType


def catalogue_links(company):
    @lru_cache(maxsize=None)
    def resolve(kind, resource_id):
        if kind not in {'labor','equipment'} or not resource_id:
            return {}
        model, stem = (Brigade,'brigade') if kind=='labor' else (EquipmentType,'equipment_type')
        row=model.objects.filter(company=company,pk=resource_id).select_related('merge_source__target').first()
        if row is None:
            return {}
        alias=getattr(row,'merge_source',None)
        if alias:
            row=alias.target
            if row.company_id!=company.pk:
                return {}
        route='resources:'+stem+'_update'
        return {'catalogue_edit_route':route,'catalogue_url':reverse(route,args=[row.pk]),
                'catalogue_read_url':reverse('resources:'+stem+'_list')+'?'+urlencode({'name':row.name})}
    return resolve
