from django.contrib import admin
from .models import AuditLog


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ['created_at', 'user', 'action', 'entity_type', 'entity_id']
    list_filter = ['action', 'entity_type']
    search_fields = ['entity_id', 'comment']
    readonly_fields = ['created_at']