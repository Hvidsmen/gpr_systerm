"""Daily fuel stocks are shared by everyone working in a physical camp."""
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models.functions import Round
from core.fact_history import ActualHistoryMixin
from core.models import BaseCompanyModel

# Historical type rows remain as source records; the current ledger is TOTAL.
FUEL_CHOICES=[('TOTAL','ГСМ ВЖГ'),('DIESEL','Дизельное топливо'),('PETROL_92','Бензин АИ-92'),('PETROL_95','Бензин АИ-95'),
    ('PETROL_98','Бензин АИ-98'),('GAS','Газ'),('OIL','Моторное масло'),('OTHER','Другое')]


class CampFuelDay(ActualHistoryMixin,BaseCompanyModel):
    camp=models.ForeignKey('personnel.Camp',on_delete=models.PROTECT,related_name='fuel_days',verbose_name='ВЖГ')
    date=models.DateField('Дата')
    fuel_type=models.CharField('ГСМ',max_length=20,choices=FUEL_CHOICES,default='TOTAL')
    opening_balance=models.DecimalField('Остаток на начало, м³',max_digits=15,decimal_places=3,default=0)
    received_liters=models.DecimalField('Приход, м³',max_digits=15,decimal_places=3,default=0)
    actual_liters=models.DecimalField('Расход, м³',max_digits=15,decimal_places=3,default=0)
    actual_balance=models.DecimalField('Остаток на конец, м³',max_digits=15,decimal_places=3,default=0,editable=False)
    comment=models.TextField('Комментарий',blank=True,max_length=4000)
    recorded_by=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.SET_NULL,null=True,editable=False,related_name='+')

    class Meta:
        ordering=['date','fuel_type','pk']
        constraints=[models.UniqueConstraint(fields=['camp','fuel_type','date'],name='one_camp_fuel_type_day'),
            models.CheckConstraint(condition=models.Q(opening_balance__gte=0,received_liters__gte=0,actual_liters__gte=0,actual_balance__gte=0),name='camp_fuel_nonnegative'),
            models.CheckConstraint(condition=models.Q(actual_balance=Round(models.F('opening_balance')+models.F('received_liters')-models.F('actual_liters'),precision=3)),name='camp_fuel_balanced')]

    def clean(self):
        super().clean()
        if self.camp_id and self.camp.company_id!=self.company_id:raise ValidationError('ВЖГ другой компании.')

    def __str__(self):return f'{self.camp} · {self.get_fuel_type_display()} · {self.date}'
