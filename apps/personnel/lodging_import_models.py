from django.db import models
from django.core.exceptions import ValidationError
from django.conf import settings
from .models import PersonnelModel


class LodgingImportBatch(PersonnelModel):
    camp=models.ForeignKey('personnel.Camp',on_delete=models.PROTECT)
    filename=models.CharField(max_length=250)
    digest=models.CharField(max_length=64)
    snapshot_date=models.DateField()
    created_by=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.PROTECT)
    def clean(self):
        if self.camp_id and self.camp.company_id!=self.company_id:raise ValidationError('ВЖГ другой компании.')
        if self.created_by_id and self.created_by.company_id!=self.company_id:raise ValidationError('Автор другой компании.')
    class Meta:
        constraints=[models.UniqueConstraint(fields=['company','camp','digest'],name='unique_lodging_import_file')]


class LodgingImportRow(PersonnelModel):
    batch=models.ForeignKey(LodgingImportBatch,on_delete=models.CASCADE,related_name='rows')
    source_row=models.PositiveIntegerField()
    data=models.JSONField(default=dict)
    state=models.CharField(max_length=12,choices=[('PENDING','На проверке'),('APPLIED','Загружено')],default='PENDING')
    warning=models.TextField(blank=True)
    stay=models.ForeignKey('personnel.LodgingStay',on_delete=models.PROTECT,null=True,blank=True)
    def clean(self):
        if self.batch_id and self.batch.company_id!=self.company_id:raise ValidationError('Загрузка другой компании.')
        if self.stay_id and self.stay.company_id!=self.company_id:raise ValidationError('Проживание другой компании.')
    class Meta:
        ordering=['source_row','pk']
        constraints=[models.UniqueConstraint(fields=['batch','source_row'],name='unique_lodging_source_row')]
