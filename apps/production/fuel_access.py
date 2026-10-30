"""Fuel ledger policy, without granting permission to change transport."""
from django.core.exceptions import PermissionDenied
from core.permissions import require_roles,role_code
from . import transport

OPERATORS = transport.OPERATORS | {'FUEL_WAREHOUSE'}
READERS = transport.READERS | {'FUEL_WAREHOUSE'}


def camps_for(user):
    from apps.personnel.models import Camp
    camps=Camp.objects.filter(company=user.company)
    if role_code(user) in {'MECHANIZATION_DISPATCHER','ALL_FACT_DISPATCHER','PLANNER','FUEL_WAREHOUSE'}:
        camps=camps.filter(object_links__construction_object__in=user.assigned_objects.filter(company=user.company))
    return camps.distinct().order_by('name','pk')


def authorize(user, camp, write=True):
    require_roles(user, OPERATORS if write else READERS)
    if not camps_for(user).filter(pk=camp.pk).exists():
        raise PermissionDenied('ВЖГ не связан с доступными вам объектами.')
