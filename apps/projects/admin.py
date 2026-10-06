from django.contrib import admin
from .models import Project, ConstructionObject, Section


@admin.register(Project)
class ProjectAdmin(admin.ModelAdmin):
    list_display = ['code', 'name', 'status', 'start_date', 'end_date']
    list_filter = ['status']
    search_fields = ['code', 'name']


@admin.register(ConstructionObject)
class ConstructionObjectAdmin(admin.ModelAdmin):
    list_display = ['code', 'name', 'project']
    list_filter = ['project']
    search_fields = ['code', 'name']


@admin.register(Section)
class SectionAdmin(admin.ModelAdmin):
    list_display = ['code', 'name', 'construction_object']  # ← ИСПРАВЛЕНО (было 'object')
    list_filter = ['construction_object']  # ← ИСПРАВЛЕНО (было 'object')
    search_fields = ['code', 'name']