from django.contrib import admin
from .models import (
    LoadProfile,
    LoadProfileItem,
    ProductionCalendar,
    CalendarDay,
    MonthlyPlan,
    PlanVersion,
    DailyPlan,
    DailyBaseline,
)


@admin.register(LoadProfile)
class LoadProfileAdmin(admin.ModelAdmin):
    list_display = ["code", "name"]
    search_fields = ["code", "name"]


@admin.register(LoadProfileItem)
class LoadProfileItemAdmin(admin.ModelAdmin):
    list_display = ["profile", "workday_number", "percentage"]
    list_filter = ["profile"]


@admin.register(ProductionCalendar)
class ProductionCalendarAdmin(admin.ModelAdmin):
    list_display = ["code", "name", "year", "is_default"]
    list_filter = ["year", "is_default"]


@admin.register(CalendarDay)
class CalendarDayAdmin(admin.ModelAdmin):
    list_display = ["date", "calendar", "is_working", "is_holiday", "is_shortened"]
    list_filter = ["calendar", "is_working", "is_holiday"]


@admin.register(MonthlyPlan)
class MonthlyPlanAdmin(admin.ModelAdmin):
    list_display = [
        "project_work",
        "year",
        "month",
        "planned_quantity",
        "planned_value",
    ]
    list_filter = ["year", "month"]


@admin.register(PlanVersion)
class PlanVersionAdmin(admin.ModelAdmin):
    list_display = [
        "monthly_plan",
        "version_number",
        "status",
        "is_baseline",
        "approved_at",
    ]
    list_filter = ["status", "is_baseline"]


@admin.register(DailyPlan)
class DailyPlanAdmin(admin.ModelAdmin):
    list_display = ["plan_version", "work_item", "date", "planned_quantity"]
    list_filter = ["plan_version"]


@admin.register(DailyBaseline)
class DailyBaselineAdmin(admin.ModelAdmin):
    list_display = ["project_work", "work_item", "date", "baseline_quantity"]
    list_filter = ["project_work"]


from .models import GlobalPlanVersion


@admin.register(GlobalPlanVersion)
class GlobalPlanVersionAdmin(admin.ModelAdmin):
    list_display = [
        "construction_object",
        "version_number",
        "start_date",
        "end_date",
        "status",
    ]
    readonly_fields = [field.name for field in GlobalPlanVersion._meta.fields] + [
        "source_versions"
    ]

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


from .models import PlanningWorkspace, WorkMonthAllocation, ResourceMonthAllocation


@admin.register(PlanningWorkspace, WorkMonthAllocation, ResourceMonthAllocation)
class WorkspaceDataAdmin(admin.ModelAdmin):
    def get_readonly_fields(self, request, obj=None):
        return [field.name for field in self.model._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
