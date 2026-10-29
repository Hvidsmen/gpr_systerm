"""Physical accommodation is independent of personnel camp movements."""
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q,F
from core.fact_history import ActualHistoryMixin
from .models import PersonnelModel


class LodgingBuilding(ActualHistoryMixin,PersonnelModel):
    camp=models.ForeignKey('personnel.Camp',on_delete=models.PROTECT,related_name='lodging_buildings')
    name=models.CharField('Вагон-дом / корпус',max_length=200)
    inventory_number=models.CharField('Инвентарный номер',max_length=80,blank=True)
    description=models.TextField('Сведения о вагоне',max_length=4000,blank=True)
    kind=models.CharField('Тип / назначение',max_length=100,blank=True)
    is_active=models.BooleanField('Используется',default=True)
    class Meta:
        ordering=['camp__name','name','pk']
        constraints=[models.UniqueConstraint(fields=['camp','name'],name='unique_camp_lodging_building')]
    def clean(self):
        if self.camp_id and self.camp.company_id!=self.company_id:raise ValidationError('ВЖГ другой компании.')
    def __str__(self):return self.name


class LodgingBed(ActualHistoryMixin,PersonnelModel):
    STATES=[('AVAILABLE','Доступно'),('RESERVED','Зарезервировано'),('UNAVAILABLE','Недоступно')]
    building=models.ForeignKey(LodgingBuilding,on_delete=models.PROTECT,related_name='beds')
    room=models.CharField('Комната',max_length=60,blank=True)
    number=models.CharField('Номер места',max_length=60)
    state=models.CharField('Доступность',max_length=15,choices=STATES,default='AVAILABLE')
    note=models.CharField('Примечание к месту',max_length=500,blank=True)
    class Meta:
        ordering=['building__name','room','number','pk']
        constraints=[models.UniqueConstraint(fields=['building','room','number'],name='unique_building_room_bed')]
    def clean(self):
        if self.building_id and self.building.company_id!=self.company_id:raise ValidationError('Здание другой компании.')
    def __str__(self):return f'{self.building} · '+(f'комн. {self.room} · ' if self.room else '')+f'место {self.number}'


class LodgingStay(ActualHistoryMixin,PersonnelModel):
    employee=models.ForeignKey('resources.Employee',on_delete=models.PROTECT,related_name='lodging_stays')
    camp=models.ForeignKey('personnel.Camp',on_delete=models.PROTECT,related_name='lodging_stays')
    bed=models.ForeignKey(LodgingBed,on_delete=models.PROTECT,related_name='stays')
    arrived_on=models.DateField('Дата заселения')
    departed_on=models.DateField('Дата выселения',null=True,blank=True)
    planned_departure=models.DateField('Плановая дата выселения',null=True,blank=True)
    keys_issued=models.BooleanField('Ключи выданы',default=False)
    key_number=models.CharField('Номер ключа',max_length=80,blank=True)
    pass_number=models.CharField('Номер пропуска',max_length=80,blank=True)
    organization_snapshot=models.CharField('Организация при заселении',max_length=300,blank=True,editable=False)
    position_snapshot=models.CharField('Должность при заселении',max_length=300,blank=True,editable=False)
    department_snapshot=models.CharField('Подразделение при заселении',max_length=250,blank=True,editable=False)
    comment=models.TextField('Примечание',max_length=4000,blank=True)
    recorded_by=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.SET_NULL,null=True,related_name='+')
    cancelled_at=models.DateTimeField(null=True,blank=True)
    cancelled_by=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.SET_NULL,null=True,blank=True,related_name='+')
    cancellation_reason=models.CharField(max_length=500,blank=True)
    class Meta:
        ordering=['-arrived_on','-pk']
        indexes=[models.Index(fields=['company','employee','arrived_on','departed_on'],name='lodging_employee_period')]
        constraints=[models.CheckConstraint(condition=Q(departed_on=None)|Q(departed_on__gte=F('arrived_on')),name='lodging_departure_after_arrival')]
    def clean(self):
        for name in ('employee','camp','bed'):
            obj=getattr(self,name,None)
            if obj and obj.company_id!=self.company_id:raise ValidationError('Данные другой компании.')
        if self.bed_id and self.bed.building.camp_id!=self.camp_id:raise ValidationError('Место находится в другом ВЖГ.')
        if self.departed_on and self.arrived_on and self.departed_on<self.arrived_on:raise ValidationError('Выселение не может быть раньше заселения.')
        if self.planned_departure and self.arrived_on and self.planned_departure<self.arrived_on:raise ValidationError('Плановое освобождение не может быть раньше заселения.')
        if self.cancelled_at or not self.arrived_on or self.departed_on==self.arrived_on:return
        overlapping=type(self).objects.filter(company_id=self.company_id,cancelled_at=None).exclude(pk=self.pk).exclude(departed_on=F('arrived_on')).filter(Q(departed_on=None)|Q(departed_on__gt=self.arrived_on))
        if self.departed_on:overlapping=overlapping.filter(arrived_on__lt=self.departed_on)
        if overlapping.filter(bed_id=self.bed_id).exists():raise ValidationError('Место уже занято на этот период.')
        if overlapping.filter(employee_id=self.employee_id).exists():raise ValidationError('У сотрудника уже есть проживание на этот период. Оформите переселение.')
    def __str__(self):return f'{self.employee} · {self.bed} · {self.arrived_on}'


class LodgingEvent(ActualHistoryMixin,PersonnelModel):
    KINDS=[('CHECKIN','Заселение'),('MOVE','Переселение'),('CHECKOUT','Выселение'),('UPDATE','Изменение сведений'),('CANCEL','Отмена записи')]
    employee=models.ForeignKey('resources.Employee',on_delete=models.PROTECT,related_name='+')
    camp=models.ForeignKey('personnel.Camp',on_delete=models.PROTECT,related_name='lodging_events')
    date=models.DateField()
    kind=models.CharField(max_length=12,choices=KINDS)
    from_bed=models.ForeignKey(LodgingBed,on_delete=models.PROTECT,null=True,blank=True,related_name='+')
    to_bed=models.ForeignKey(LodgingBed,on_delete=models.PROTECT,null=True,blank=True,related_name='+')
    stay=models.ForeignKey(LodgingStay,on_delete=models.PROTECT,related_name='events')
    comment=models.TextField(max_length=4000,blank=True)
    recorded_by=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.SET_NULL,null=True,related_name='+')
    class Meta:ordering=['-date','-pk']
    def clean(self):
        for name in ('employee','camp','from_bed','to_bed','stay'):
            obj=getattr(self,name,None)
            if obj and obj.company_id!=self.company_id:raise ValidationError('Событие другой компании.')
    def __str__(self):return f'{self.employee} · {self.get_kind_display()} · {self.date}'


class LodgingControl(PersonnelModel):
    resource=models.CharField(max_length=10,choices=[('employee','Сотрудник'),('bed','Место')])
    resource_id=models.PositiveBigIntegerField()
    revision=models.PositiveIntegerField(default=0)
    locked_by=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.SET_NULL,null=True,blank=True,related_name='+')
    session=models.UUIDField(null=True,blank=True)
    expires_at=models.DateTimeField(null=True,blank=True)
    class Meta:constraints=[models.UniqueConstraint(fields=['company','resource','resource_id'],name='unique_lodging_resource_control')]


class LodgingResolution(ActualHistoryMixin,PersonnelModel):
    employee=models.ForeignKey('resources.Employee',on_delete=models.PROTECT,related_name='+')
    camp=models.ForeignKey('personnel.Camp',on_delete=models.PROTECT,related_name='+')
    date=models.DateField()
    fingerprint=models.CharField(max_length=64)
    reason=models.CharField('Объяснение расхождения',max_length=1000)
    recorded_by=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.SET_NULL,null=True,related_name='+')
    class Meta:constraints=[models.UniqueConstraint(fields=['company','employee','fingerprint'],name='unique_lodging_resolution')]
    def clean(self):
        if self.employee.company_id!=self.company_id or self.camp.company_id!=self.company_id:raise ValidationError('Сверка другой компании.')
    def __str__(self):return f'{self.employee} · Сверка · {self.date}'
