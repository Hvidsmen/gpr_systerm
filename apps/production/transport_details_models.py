from django.db import models
from django.conf import settings
from core.models import BaseCompanyModel
from core.fact_history import ActualHistoryMixin

from .transport_detail_values import DETAIL_FIELDS,STATUS_KINDS,REPAIR_STAGES


class EquipmentServiceStatus(BaseCompanyModel):
    kind=models.CharField('Справочник',max_length=20,choices=STATUS_KINDS)
    name=models.CharField('Название',max_length=150)
    is_active=models.BooleanField('Действует',default=True)
    class Meta:
        ordering=['kind','name','pk']
        constraints=[models.UniqueConstraint(fields=['company','kind','name'],name='unique_company_equipment_status')]
    def __str__(self):return self.name



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

    source_details=models.JSONField('Исходные сведения',default=dict,blank=True,editable=False)
    shift_one_employee=models.ForeignKey('resources.Employee',on_delete=models.PROTECT,null=True,blank=True,related_name='+',verbose_name='1-я смена · водитель / машинист')
    shift_two_employee=models.ForeignKey('resources.Employee',on_delete=models.PROTECT,null=True,blank=True,related_name='+',verbose_name='2-я смена · водитель / машинист')
    repair_stage=models.CharField('Этап ремонта',max_length=20,choices=REPAIR_STAGES,blank=True)
    repair_started_on=models.DateField('Дата постановки на ремонт',null=True,blank=True)
    parts_expected_on=models.DateField('Плановая дата поставки запчастей',null=True,blank=True)
    request_date=models.DateField('Дата заявки',null=True,blank=True)
    request_stage=models.ForeignKey(EquipmentServiceStatus,on_delete=models.PROTECT,null=True,blank=True,related_name='+',verbose_name='Статус заявки')
    parts_stage=models.ForeignKey(EquipmentServiceStatus,on_delete=models.PROTECT,null=True,blank=True,related_name='+',verbose_name='Поставка запчастей')
    maintenance_stage=models.ForeignKey(EquipmentServiceStatus,on_delete=models.PROTECT,null=True,blank=True,related_name='+',verbose_name='ТО и готовность')

    class Meta:
        ordering=['-date','-pk']
        constraints=[models.UniqueConstraint(fields=['equipment','date'],name='one_machine_day_details')]

    def __str__(self):return f'{self.equipment} · {self.date} · Сведения по технике'

    @property
    def display_fields(self):return [(label,'Да' if value is True else 'Нет' if value is False else value) for name,label in DETAIL_FIELDS if (value:=getattr(self,'get_repair_stage_display')() if name=='repair_stage' else getattr(self,name)) not in (None,'')]

    @property
    def unparsed_source_fields(self):
        from .transport_detail_values import SOURCE_DETAIL_FIELDS
        return [(label,self.source_details[name]) for name,label in SOURCE_DETAIL_FIELDS if self.source_details.get(name) not in (None,'')]
