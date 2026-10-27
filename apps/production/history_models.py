from django.conf import settings
from django.db import models
from core.models import BaseTimestampedModel


class RevisionQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise ValueError('Историю факта нельзя изменять.')
    def delete(self):
        raise ValueError('Историю факта нельзя удалять.')
    def bulk_create(self, objs, **kwargs):
        if kwargs.get('ignore_conflicts') or kwargs.get('update_conflicts'):
            raise ValueError('Историю факта нельзя перезаписывать.')
        return super().bulk_create(objs, **kwargs)


class FactRevision(BaseTimestampedModel):
    company = models.ForeignKey('accounts.Company', on_delete=models.PROTECT, related_name='fact_revisions')
    entity_type = models.CharField(max_length=80)
    entity_id = models.CharField(max_length=40)
    operation = models.CharField(max_length=8, choices=[('CREATE','Добавлено'),('UPDATE','Изменено'),('DELETE','Удалено')])
    before = models.JSONField(null=True)
    after = models.JSONField(null=True)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name='+')
    actor_name = models.CharField(max_length=300)
    actor_role = models.CharField(max_length=50, blank=True)
    group_id = models.UUIDField(db_index=True)
    reason = models.TextField(blank=True)
    direction = models.CharField(max_length=16)
    fact_date = models.DateField(null=True, db_index=True)
    object_id = models.PositiveBigIntegerField(null=True, db_index=True)
    camp_ids = models.JSONField(default=list)
    label = models.TextField()
    objects = models.Manager.from_queryset(RevisionQuerySet)()

    class Meta:
        ordering = ['-created_at','-pk']
        indexes = [models.Index(fields=['company','entity_type','entity_id'],name='fact_revision_entity')]

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValueError('Запись истории неизменяема. Исправление создаёт новую версию факта.')
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValueError('Историю факта нельзя удалять.')
