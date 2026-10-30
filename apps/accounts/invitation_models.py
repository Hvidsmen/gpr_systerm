"""Company-scoped invitation outbox; never stores Windows passwords."""
import uuid
from django.db import models
from django.conf import settings
from core.models import BaseTimestampedModel

class CompanyMailSettings(BaseTimestampedModel):
    company=models.OneToOneField('accounts.Company',on_delete=models.CASCADE,related_name='mail_settings')
    host=models.CharField('SMTP-сервер',max_length=255,blank=True)
    port=models.PositiveIntegerField('Порт',default=587)
    security=models.CharField('Защита соединения',max_length=8,choices=[('STARTTLS','STARTTLS'),('SSL','SSL/TLS'),('NONE','Без шифрования')],default='STARTTLS')
    username=models.CharField('Логин почтового ящика',max_length=254,blank=True)
    password_encrypted=models.TextField(blank=True,editable=False)
    from_email=models.EmailField('Адрес отправителя',blank=True)
    portal_url=models.URLField('Адрес портала',default='http://192.168.70.9:8001/')
    updated_by=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.SET_NULL,null=True,blank=True,related_name='+')

class InvitationBatch(BaseTimestampedModel):
    id=models.UUIDField(primary_key=True,default=uuid.uuid4,editable=False)
    company=models.ForeignKey('accounts.Company',on_delete=models.CASCADE)
    created_by=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.SET_NULL,null=True,related_name='invitation_batches')
    portal_url=models.URLField()
    confirmed_at=models.DateTimeField(null=True,blank=True)
    last_error=models.CharField(max_length=1000,blank=True)
    paused=models.BooleanField(default=False)
    retry_after=models.DateTimeField(null=True,blank=True)
    class Meta:ordering=['-created_at']

class UserInvitation(BaseTimestampedModel):
    STATUS=[('DRAFT','Готово к отправке'),('BLOCKED','Не готово'),('QUEUED','В очереди'),('SENDING','Отправляется'),('SENT','Передано почтовому серверу'),('FAILED','Ошибка отправки'),('CANCELLED','Отменено'),('UNKNOWN','Результат требует проверки')]
    id=models.UUIDField(primary_key=True,default=uuid.uuid4,editable=False)
    batch=models.ForeignKey(InvitationBatch,on_delete=models.CASCADE,related_name='items')
    recipient=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.SET_NULL,null=True,related_name='invitations')
    full_name=models.CharField(max_length=350)
    recipient_email=models.CharField(max_length=254,blank=True)
    windows_login=models.CharField(max_length=150,blank=True)
    role_name=models.CharField(max_length=100,blank=True)
    fingerprint=models.CharField(max_length=64,blank=True)
    subject=models.CharField(max_length=255,blank=True)
    body=models.TextField(blank=True)
    html_body=models.TextField(blank=True)
    status=models.CharField(max_length=10,choices=STATUS,default='DRAFT',db_index=True)
    error=models.CharField(max_length=1000,blank=True)
    attempts=models.PositiveIntegerField(default=0)
    started_at=models.DateTimeField(null=True,blank=True)
    sent_at=models.DateTimeField(null=True,blank=True)
    class Meta:
        ordering=['created_at','id']
        constraints=[models.UniqueConstraint(fields=['batch','recipient'],name='unique_batch_invitation_recipient')]
