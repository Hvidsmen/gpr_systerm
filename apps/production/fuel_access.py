"""Fuel ledger policy, without granting permission to change transport."""
from django.core.exceptions import PermissionDenied
from core.permissions import require_roles
from . import transport

OPERATORS = transport.OPERATORS | {'FUEL_WAREHOUSE'}
READERS = transport.READERS | {'FUEL_WAREHOUSE'}


def authorize(user, camp, write=True):
    require_roles(user, OPERATORS if write else READERS)
    if not transport.camps_for(user).filter(pk=camp.pk).exists():
        raise PermissionDenied('ВЖГ не связан с доступными вам объектами.')
