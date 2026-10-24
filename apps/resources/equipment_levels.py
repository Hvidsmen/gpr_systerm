"""Equipment categories for GPR, model variants for daily SMG."""
from django.db.models import Q
from .models import EquipmentType

def gpr_types(queryset):return queryset.filter(parent__isnull=True)

def smg_types(queryset):
    # Existing catalogues without variants remain usable.
    return queryset.filter(Q(parent__isnull=False)|Q(model_variants__isnull=True)).distinct()
