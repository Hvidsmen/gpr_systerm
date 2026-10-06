from django.db import models
from django.utils.translation import gettext_lazy as _

from core.enums import AuditAction


class AuditLog(models.Model):
    user = models.ForeignKey(
        'accounts.User', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='audit_logs',
        verbose_name=_('пользователь')
    )
    action = models.CharField(_('действие'), max_length=50, choices=AuditAction.choices)
    entity_type = models.CharField(_('тип объекта'), max_length=100)
    entity_id = models.CharField(_('ID объекта'), max_length=100)
    old_value = models.JSONField(_('старое значение'), null=True, blank=True)
    new_value = models.JSONField(_('новое значение'), null=True, blank=True)
    comment = models.TextField(_('комментарий'), blank=True)
    ip_address = models.GenericIPAddressField(_('IP адрес'), null=True, blank=True)
    created_at = models.DateTimeField(_('создано'), auto_now_add=True, db_index=True)

    class Meta:
        verbose_name = _('запись аудита')
        verbose_name_plural = _('записи аудита')
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['entity_type', 'entity_id']),
        ]

    def __str__(self):
        return f"{self.created_at} - {self.user} - {self.get_action_display()}"