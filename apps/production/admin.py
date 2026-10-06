from django.contrib import admin
from .models import (
    DailyFact,
    DeviationReason,
    LaborPlan,
    LaborFact,
    EquipmentPlan,
    EquipmentFact,
    FuelPlan,
    FuelFact,
    LegacyResourceRecord,
)


@admin.register(DailyFact)
class DailyFactAdmin(admin.ModelAdmin):
    list_display = [
        "date",
        "project_work",
        "work_item",
        "actual_quantity",
        "actual_value",
    ]
    list_filter = ["date", "project_work"]


@admin.register(LaborPlan, LaborFact, EquipmentPlan, EquipmentFact, FuelPlan, FuelFact)
class ResourceAdmin(admin.ModelAdmin):
    list_display = ["date", "construction_object", "company"]
    list_filter = ["construction_object", "date"]


@admin.register(DeviationReason)
class DeviationReasonAdmin(admin.ModelAdmin):
    list_display = ["name", "company"]


@admin.register(LegacyResourceRecord)
class LegacyResourceAdmin(admin.ModelAdmin):
    list_display = ["source_model", "source_pk", "company", "resolved"]
    readonly_fields = [
        "source_model",
        "source_pk",
        "payload",
        "reason",
        "resolved",
        "construction_object",
        "restored_pk",
        "company",
    ]

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
