"""Reviewed reasons and private supporting documents for individual work shutdowns."""
from pathlib import Path
from uuid import uuid4
from django.conf import settings
from django.core.files.storage import FileSystemStorage
from django.db import models
from core.models import BaseCompanyModel
from core.fact_history import ActualHistoryMixin


class PrivateActuationStorage(FileSystemStorage):
    @property
    def base_location(self):return settings.ACTUATION_FILES_ROOT
    @property
    def location(self):return str(Path(self.base_location).resolve())
    @property
    def base_url(self):return None


def document_storage():
    return PrivateActuationStorage()


def document_path(instance, filename):
    return f'{instance.company_id}/{uuid4().hex}'


class ActuationReason(BaseCompanyModel):
    STATES=[('PENDING','На проверке'),('APPROVED','Принята'),('MERGED','Заменена существующей'),('REJECTED','Удалена из списка')]
    name=models.CharField('Причина актировки',max_length=200)
    normalized_name=models.CharField(max_length=200)
    status=models.CharField(max_length=16,choices=STATES,default='PENDING')
    created_by=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.SET_NULL,null=True,blank=True,related_name='proposed_actuation_reasons')
    reviewed_by=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.SET_NULL,null=True,blank=True,related_name='reviewed_actuation_reasons')
    reviewed_at=models.DateTimeField(null=True,blank=True)
    replacement=models.ForeignKey('self',on_delete=models.PROTECT,null=True,blank=True)
    review_comment=models.TextField(blank=True)
    revision=models.PositiveIntegerField(default=0)
    task=models.OneToOneField('planning.WorkTask',on_delete=models.SET_NULL,null=True,blank=True,related_name='actuation_reason')
    class Meta:
        ordering=['name','pk']
        constraints=[models.UniqueConstraint(fields=['company','normalized_name'],name='unique_company_actuation_reason')]
    def __str__(self):return self.name
    @property
    def label(self):
        suffix={'PENDING':' · на проверке','REJECTED':' · удалена руководителем','MERGED':' · заменена'}.get(self.status,'')
        return self.name+suffix


class ActuationDocument(ActualHistoryMixin,BaseCompanyModel):
    fact=models.ForeignKey('production.DailyFact',on_delete=models.SET_NULL,null=True,related_name='actuation_documents')
    construction_object=models.ForeignKey('projects.ConstructionObject',on_delete=models.PROTECT)
    date=models.DateField()
    file=models.FileField(storage=document_storage,upload_to=document_path)
    original_name=models.CharField(max_length=255)
    size=models.PositiveBigIntegerField()
    checksum=models.CharField(max_length=64)
    uploaded_by=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.SET_NULL,null=True,related_name='actuation_documents')
    batch_token=models.UUIDField()
    ordinal=models.PositiveIntegerField()
    removed_at=models.DateTimeField(null=True,blank=True)
    removed_by=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.SET_NULL,null=True,blank=True,related_name='removed_actuation_documents')
    class Meta:
        ordering=['created_at','pk']
        constraints=[models.UniqueConstraint(fields=['company','fact','batch_token','ordinal'],name='unique_actuation_document_upload')]
    def __str__(self):return self.original_name
