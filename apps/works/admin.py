from django.contrib import admin
from core.permissions import role_code
from .models import WorkTemplate, WorkTemplateVersion, WorkTemplateItem, ProjectWork, ProjectWorkItem


@admin.register(WorkTemplate)
class WorkTemplateAdmin(admin.ModelAdmin):
    list_display = ['name', 'unit', 'is_active']
    list_filter = ['is_active']
    search_fields = ['code', 'name']


@admin.register(WorkTemplateVersion)
class WorkTemplateVersionAdmin(admin.ModelAdmin):
    list_display = ['template', 'version_number', 'is_current']
    list_filter = ['is_current']


@admin.register(WorkTemplateItem)
class WorkTemplateItemAdmin(admin.ModelAdmin):
    exclude = ["weight"]
    list_display = ['name', 'version', 'sequence']
    list_filter = ['version']


class LegacyStructureAdminMixin:
    def lookup_allowed(self,lookup,value,request=None):
        if request and role_code(request.user)!='ADMIN' and lookup.startswith('is_legacy_structure'):return False
        return super().lookup_allowed(lookup,value,request)
    def save_model(self,request,obj,form,change):
        obj._allow_legacy_structure_change=role_code(request.user)=='ADMIN'
        return super().save_model(request,obj,form,change)
    def get_exclude(self,request,obj=None):
        excluded=list(super().get_exclude(request,obj) or ())
        if role_code(request.user)!='ADMIN':excluded.append('is_legacy_structure')
        return excluded
    def get_list_display(self,request):
        fields=super().get_list_display(request)
        return [*fields,'is_legacy_structure'] if role_code(request.user)=='ADMIN' else fields
    def get_list_filter(self,request):
        fields=super().get_list_filter(request)
        return [*fields,'is_legacy_structure'] if role_code(request.user)=='ADMIN' else fields


@admin.register(ProjectWork)
class ProjectWorkAdmin(LegacyStructureAdminMixin,admin.ModelAdmin):
    list_display = ['name', 'section', 'unit', 'unit_price', 'status']
    list_filter = ['status']
    search_fields = ['code', 'name']


@admin.register(ProjectWorkItem)
class ProjectWorkItemAdmin(LegacyStructureAdminMixin,admin.ModelAdmin):
    exclude = ["planned_quantity"]
    list_display = ['name', 'project_work', 'sequence']
    list_filter = ['project_work']

from .models import WorkGroup, MeasurementUnit
admin.site.register(WorkGroup)
admin.site.register(MeasurementUnit)
