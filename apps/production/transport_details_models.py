from django.db import models
from django.conf import settings
from core.models import BaseCompanyModel
from core.fact_history import ActualHistoryMixin

DETAIL_FIELDS=[('shift_one','1-я смена · водитель / машинист'),('shift_two','2-я смена · водитель / машинист'),
    ('in_gpr','Участвует в ГПР'),('repair_status','Состояние по сводке'),('defects','Неисправности и поломки'),
    ('repair_date','Дата постановки на ремонт'),('request_details','Номер и дата заявки'),('request_status','Согласование и оплата заявок'),
    ('parts_status','Поставка запчастей'),('expected_parts_date','Плановая дата поставки запчастей'),('maintenance','ТО и готовность к эксплуатации'),('comment','Примечание')]


class EquipmentDayDetails(ActualHistoryMixin,BaseCompanyModel):
    equipment=models.ForeignKey('resources.Equipment',on_delete=models.PROTECT,related_name='daily_details')
    camp=models.ForeignKey('personnel.Camp',on_delete=models.PROTECT,related_name='equipment_details')
    date=models.DateField('Дата')
    shift_one=models.CharField('1-я смена · водитель / машинист',max_length=400,blank=True)
    shift_two=models.CharField('2-я смена · водитель / машинист',max_length=400,blank=True)
    in_gpr=models.BooleanField('Участвует в ГПР',null=True,blank=True)
    repair_status=models.CharField('Состояние по сводке',max_length=200,blank=True)
    defects=models.TextField('Неисправности и поломки',max_length=10000,blank=True)
    repair_date=models.CharField('Дата постановки на ремонт',max_length=200,blank=True)
    request_details=models.TextField('Номер и дата заявки',max_length=10000,blank=True)
    request_status=models.TextField('Согласование и оплата заявок',max_length=10000,blank=True)
    parts_status=models.TextField('Поставка запчастей',max_length=10000,blank=True)
    expected_parts_date=models.CharField('Плановая дата поставки запчастей',max_length=200,blank=True)
    maintenance=models.TextField('ТО и готовность к эксплуатации',max_length=10000,blank=True)
    comment=models.TextField('Примечание',max_length=10000,blank=True)
    recorded_by=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.SET_NULL,null=True,related_name='+')

    class Meta:
        ordering=['-date','-pk']
        constraints=[models.UniqueConstraint(fields=['equipment','date'],name='one_machine_day_details')]

    def __str__(self):return f'{self.equipment} · {self.date} · Сведения по технике'

    @property
    def display_fields(self):return [(label,'Да' if value is True else 'Нет' if value is False else value) for name,label in DETAIL_FIELDS if (value:=getattr(self,name)) not in (None,'')]
