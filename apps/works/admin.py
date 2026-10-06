from django.contrib import admin
from .models import WorkTemplate, WorkTemplateVersion, WorkTemplateItem, ProjectWork, ProjectWorkItem


@admin.register(WorkTemplate)
class WorkTemplateAdmin(admin.ModelAdmin):
    list_display = ['code', 'name', 'unit', 'is_active']
    list_filter = ['is_active']
    search_fields = ['code', 'name']


@admin.register(WorkTemplateVersion)
class WorkTemplateVersionAdmin(admin.ModelAdmin):
    list_display = ['template', 'version_number', 'is_current']
    list_filter = ['is_current']


@admin.register(WorkTemplateItem)
class WorkTemplateItemAdmin(admin.ModelAdmin):
    list_display = ['name', 'version', 'sequence', 'weight']
    list_filter = ['version']


@admin.register(ProjectWork)
class ProjectWorkAdmin(admin.ModelAdmin):
    list_display = ['code', 'name', 'section', 'unit', 'unit_price', 'status']
    list_filter = ['status']
    search_fields = ['code', 'name']


@admin.register(ProjectWorkItem)
class ProjectWorkItemAdmin(admin.ModelAdmin):
    list_display = ['name', 'project_work', 'sequence', 'weight']
    list_filter = ['project_work']