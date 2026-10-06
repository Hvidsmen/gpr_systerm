from django.contrib import admin
from .models import Position, Employee, Brigade, BrigadeMember, EquipmentType, Equipment, FuelType


@admin.register(Position)
class PositionAdmin(admin.ModelAdmin):
    list_display = ['code', 'name', 'default_hourly_rate']


@admin.register(Employee)
class EmployeeAdmin(admin.ModelAdmin):
    list_display = ['last_name', 'first_name', 'position', 'is_active']
    list_filter = ['is_active', 'position']


@admin.register(Brigade)
class BrigadeAdmin(admin.ModelAdmin):
    list_display =  ['code', 'name',  'description', 'is_active']


@admin.register(BrigadeMember)
class BrigadeMemberAdmin(admin.ModelAdmin):
    list_display = ['brigade', 'employee', 'role_in_brigade']


@admin.register(EquipmentType)
class EquipmentTypeAdmin(admin.ModelAdmin):
    list_display = [ 'name', 'category','is_active']


@admin.register(Equipment)
class EquipmentAdmin(admin.ModelAdmin):
    list_display = ['plate_number', 'name', 'type', 'is_active']
    list_filter = ['type', 'is_active']


@admin.register(FuelType)
class FuelTypeAdmin(admin.ModelAdmin):
    list_display = ['code', 'name', 'unit', 'default_price']

from .models import EquipmentCategory

@admin.register(EquipmentCategory)
class EquipmentCategoryAdmin(admin.ModelAdmin):
    list_display = ['name', 'company']
