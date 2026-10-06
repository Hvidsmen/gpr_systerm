"""
Административная панель для модуля производства.
"""
from django.contrib import admin
from .models import (
    DeviationReason, DailyFact, LaborFact, EquipmentFact, FuelFact, LaborPlan,EquipmentPlan
)


@admin.register(DeviationReason)
class DeviationReasonAdmin(admin.ModelAdmin):
    list_display = ['code', 'name', 'is_active']
    list_filter = ['is_active']
    search_fields = ['code', 'name']


@admin.register(DailyFact)
class DailyFactAdmin(admin.ModelAdmin):
    list_display = ['date', 'project_work', 'work_item',
                    'actual_quantity', 'actual_value', 'reported_by']
    list_filter = ['date', 'project_work', 'deviation_reason']
    search_fields = ['project_work__name', 'comment']
    date_hierarchy = 'date'
    readonly_fields = ['reported_by']

    def save_model(self, request, obj, form, change):
        if not obj.reported_by:
            obj.reported_by = request.user
        super().save_model(request, obj, form, change)


@admin.register(LaborFact)
class LaborFactAdmin(admin.ModelAdmin):
    list_display = ['date', 'project_work', 'brigade',
                    'planned_workers', 'actual_workers', 'actual_hours']
    list_filter = ['date', 'project_work', 'brigade']
    search_fields = ['project_work__name', 'brigade']
    date_hierarchy = 'date'

@admin.register(LaborPlan)
class LaborPlanAdmin(admin.ModelAdmin):
    list_display = ['date', 'project', 'project_work', 'brigade',
                    'planned_workers', 'planned_hours', 'hourly_rate']
    list_filter = ['date', 'project', 'project_work', 'brigade']
    search_fields = ['project__name', 'project_work__name', 'brigade__name']
    date_hierarchy = 'date'

@admin.register(EquipmentPlan)
class EquipmentPlanAdmin(admin.ModelAdmin):
    list_display = ['date', 'project', 'project_work', 'equipment_type',
                    'equipment_number', 'planned_count', 'planned_machine_hours']
    list_filter = ['date', 'project', 'project_work', 'equipment_type']
    search_fields = ['project__name', 'project_work__name', 'equipment_type', 'equipment_number']
    date_hierarchy = 'date'


@admin.register(EquipmentFact)
class EquipmentFactAdmin(admin.ModelAdmin):
    list_display = ['date', 'project', 'project_work', 'equipment_type',
                    'equipment_number', 'actual_count', 'machine_hours']
    list_filter = ['date', 'project', 'project_work', 'equipment_type']
    search_fields = ['project__name', 'project_work__name', 'equipment_type', 'equipment_number']
    date_hierarchy = 'date'

