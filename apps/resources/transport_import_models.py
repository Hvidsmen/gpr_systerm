from django.conf import settings
from django.db import models
from core.models import BaseCompanyModel
from core.fact_history import ActualHistoryMixin


class TransportImportBatch(BaseCompanyModel):
    date=models.DateField('Дата сводки')
    files=models.JSONField(default=list)
    fingerprint=models.CharField(max_length=64)
    applied_at=models.DateTimeField(null=True)
    created_by=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.SET_NULL,null=True,related_name='+')


class TransportImportRow(ActualHistoryMixin,BaseCompanyModel):
    batch=models.ForeignKey(TransportImportBatch,on_delete=models.PROTECT,related_name='rows')
    source_file=models.CharField(max_length=255)
    source_sheet=models.CharField(max_length=100)
    source_row=models.PositiveIntegerField()
    date=models.DateField()
    original=models.JSONField(default=dict,editable=False)
    data=models.JSONField(default=dict)
    status=models.CharField(max_length=12,choices=[('PENDING','Нужно исправить'),('READY','К загрузке'),('APPLIED','Загружена'),('DUPLICATE','Повтор закрыт')],default='PENDING')
    error=models.TextField(blank=True)
    equipment=models.ForeignKey('resources.Equipment',on_delete=models.PROTECT,null=True,related_name='import_rows')
    duplicate_of=models.ForeignKey('resources.Equipment',on_delete=models.PROTECT,null=True,related_name='+')
    camp=models.ForeignKey('personnel.Camp',on_delete=models.PROTECT,null=True,related_name='+')
    resolved_by=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.SET_NULL,null=True,related_name='+')
    resolved_at=models.DateTimeField(null=True)

    class Meta:
        ordering=['batch_id','pk']

    def __str__(self):return f'{self.source_file} · {self.source_sheet}, строка {self.source_row}'
