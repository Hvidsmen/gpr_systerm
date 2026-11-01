"""Production-side configuration; learning data lives in separate SQLite files."""
import uuid
from django.conf import settings
from django.db import models
from core.models import BaseCompanyModel


class TrainingCopy(BaseCompanyModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField('Учебная группа', max_length=150)
    members = models.ManyToManyField(settings.AUTH_USER_MODEL, related_name='training_copies', verbose_name='Участники')
    port = models.PositiveIntegerField('Порт учебного портала', unique=True)
    enabled = models.BooleanField('Включена', default=True)
    requested_at = models.DateTimeField(null=True, blank=True, editable=False)
    applied_request_at = models.DateTimeField(null=True, blank=True, editable=False)
    last_reset_at = models.DateTimeField(null=True, blank=True, editable=False)
    state = models.CharField(max_length=12, default='QUEUED', choices=[('QUEUED','Ожидает запуска'),('RESETTING','Обновляется'),('READY','Готова'),('ERROR','Ошибка'),('STOPPED','Остановлена')])
    error = models.CharField(max_length=500, blank=True, editable=False)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name='+')

    class Meta:
        ordering = ['name', 'pk']
        constraints = [models.UniqueConstraint(fields=['company','name'], name='unique_training_group_name')]


class TrainingResetRun(BaseCompanyModel):
    copy = models.ForeignKey(TrainingCopy, on_delete=models.CASCADE, related_name='runs')
    started_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True)
    status = models.CharField(max_length=10, default='RUNNING', choices=[('RUNNING','Выполняется'),('DONE','Обновлена'),('ERROR','Ошибка')])
    automatic = models.BooleanField(default=True)
    message = models.CharField(max_length=500, blank=True)


class TrainingTicketUse(models.Model):
    """Only the isolated database uses this table: an entry ticket is single-use."""
    nonce = models.UUIDField(primary_key=True, editable=False)
    used_at = models.DateTimeField(auto_now_add=True)
