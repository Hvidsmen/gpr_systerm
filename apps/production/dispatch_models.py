"""Named people and individual machines in daily operational facts."""

from core.fact_history import ActualHistoryMixin
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from core.models import BaseCompanyModel


class ShiftActivity(ActualHistoryMixin, BaseCompanyModel):
    construction_object=models.ForeignKey('projects.ConstructionObject',on_delete=models.PROTECT,verbose_name='Объект')
    date=models.DateField('Дата')
    shift=models.PositiveSmallIntegerField('Смена',default=1)
    general=models.BooleanField('Назначение на объект в целом',default=False)
    work=models.ForeignKey('works.ProjectWork',on_delete=models.PROTECT,null=True,blank=True,verbose_name='Работа / комплекс работ')
    work_item=models.ForeignKey('works.ProjectWorkItem',on_delete=models.PROTECT,null=True,blank=True,verbose_name='Работа в составе комплекса')
    activity=models.TextField('Что делал / причина отсутствия работы',blank=True)
    class Meta:
        abstract=True
    def clean(self):
        super().clean()
        if not self.company_id:return
        if self.shift is None or not 1<=self.shift<=24:raise ValidationError({'shift':'Номер смены от 1 до 24.'})
        for field in ['construction_object','work','work_item','employee','equipment']:
            value=getattr(self,field,None)
            if value and value.company_id!=self.company_id:raise ValidationError({field:'Запись другой компании.'})
        if self.work_id:
            if self.work.section.construction_object_id!=self.construction_object_id:raise ValidationError({'work':'Работа другого объекта.'})
            if self.work.kind=='COMPOSITE' and not self.work_item_id:raise ValidationError({'work_item':'Выберите работу в составе комплекса.'})
        if self.work_item_id and (not self.work_id or self.work_item.project_work_id!=self.work_id):raise ValidationError({'work_item':'Работа не принадлежит выбранному комплексу.'})
        requires_work=getattr(self,'assignment_reviewed',True) and not self.general and getattr(self,'state','WORKING')=='WORKING'
        if requires_work and not self.work_id and not self.activity.strip():raise ValidationError({'activity':'Укажите работу либо причину её отсутствия.'})
        if getattr(self,'state','WORKING')!='WORKING' and not self.activity.strip():raise ValidationError({'activity':'Укажите причину простоя или описание ремонта.'})
        if self.general and self.work_id:raise ValidationError({'general':'Для назначения на объект в целом уберите выбранную работу.'})


class EquipmentActivity(ShiftActivity):
    STATES=[('WORKING','Работала'),('IDLE','Исправна, не задействована'),('REPAIR','В ремонте')]
    equipment=models.ForeignKey('resources.Equipment',on_delete=models.PROTECT,verbose_name='Конкретная техника')
    state=models.CharField('Состояние',max_length=12,choices=STATES,default='WORKING')
    standing_assignment=models.ForeignKey('production.EquipmentAssignment',on_delete=models.PROTECT,null=True,blank=True,editable=False,related_name='daily_facts')
    source_state=models.ForeignKey('production.EquipmentStateChange',on_delete=models.PROTECT,null=True,blank=True,editable=False,related_name='daily_facts')
    class Meta:
        constraints=[models.UniqueConstraint(fields=['equipment','date','shift'],name='one_equipment_shift_location')]
    def clean(self):
        super().clean()
        if self.state=='WORKING' and not self.work_id and not self.activity.strip():raise ValidationError({'activity':'Опишите, что делала техника общего назначения, или выберите работу.'})
        from .transport_models import EquipmentTransportEvent
        if self.equipment_id and self.date and EquipmentTransportEvent.objects.filter(equipment_id=self.equipment_id,cancelled_at=None,date__lte=self.date).exclude(kind='CONDITION').exists():
            from .transport import present,condition_on
            location=present(self.company,self.date).filter(equipment_id=self.equipment_id).first()
            if not location:raise ValidationError({'equipment':'Техника не находится на ВЖГ на эту дату. Проверьте журнал транспорта.'})
            if self.state in {'WORKING','IDLE'} and condition_on(self.equipment,self.date)!='READY':
                # A confirmed transport day is independent of the latest technical state.
                # Legacy forms cannot create this exception from posted model fields.
                from .transport_models import EquipmentDayRecord
                confirmed=EquipmentDayRecord.objects.filter(pk=getattr(self,'_transport_day_id',None),company_id=self.company_id,equipment_id=self.equipment_id,date=self.date,construction_object_id=self.construction_object_id,status='WORKED' if self.state=='WORKING' else 'IDLE').exists()
                if not confirmed:raise ValidationError({'state':'Техника не исправна по журналу ВЖГ. Внесите фактическую работу за день через учёт транспорта.'})
            previous=type(self).objects.filter(pk=self.pk).values('construction_object_id').first() if self.pk else None
            if (not previous or previous['construction_object_id']!=self.construction_object_id) and not location.camp.object_links.filter(company=self.company,construction_object_id=self.construction_object_id).exists():
                raise ValidationError({'construction_object':'Объект не связан с ВЖГ, на котором находится машина.'})
        from .equipment_operations_models import EquipmentAssignment
        if self.equipment_id and self.date and EquipmentAssignment.objects.filter(equipment_id=self.equipment_id,shift=self.shift,start_date__lte=self.date,cancelled_at__isnull=True).filter(models.Q(end_date__isnull=True)|models.Q(end_date__gte=self.date)).exclude(construction_object_id=self.construction_object_id).exists():
            raise ValidationError({'equipment':'На эту дату и смену техника назначена другому объекту. Сначала завершите прежнее назначение.'})
        if self.standing_assignment_id:
            assignment=self.standing_assignment
            if (assignment.company_id,assignment.equipment_id,assignment.construction_object_id,assignment.shift)!=(self.company_id,self.equipment_id,self.construction_object_id,self.shift):
                raise ValidationError('Источник назначения не соответствует факту техники.')
        if self.source_state_id and (self.source_state.company_id,self.source_state.equipment_id)!=(self.company_id,self.equipment_id):
            raise ValidationError('Источник состояния другой техники.')
        if EmployeeActivity.objects.filter(equipment_id=self.equipment_id,date=self.date,shift=self.shift).exclude(construction_object_id=self.construction_object_id).exists():raise ValidationError({'equipment':'Экипаж этой техники уже учтён на другом объекте в этой смене.'})
        if self.state!='WORKING' and self.work_id:raise ValidationError({'work':'При ремонте или простое опишите состояние без выполненной работы.'})
    def __str__(self):return f'{self.equipment} · {self.date} · смена {self.shift}'


class EmployeeActivity(ShiftActivity):
    links_revision=models.PositiveIntegerField(default=0,editable=False)
    entry_direction=models.CharField('Направление ввода',max_length=5,choices=[('works','Производство'),('labor','Персонал')],blank=True,default='',editable=False)
    assignment_reviewed=models.BooleanField(default=True,editable=False)
    employee=models.ForeignKey('resources.Employee',on_delete=models.PROTECT,verbose_name='Сотрудник (ФИО)')
    position=models.ForeignKey('resources.Position',on_delete=models.PROTECT,null=True,blank=True,verbose_name='Должность на дату факта')
    planning_position=models.ForeignKey('resources.Position',on_delete=models.PROTECT,null=True,blank=True,related_name='+',verbose_name='Закрывает должность СМГ')
    equipment=models.ForeignKey('resources.Equipment',on_delete=models.PROTECT,null=True,blank=True,verbose_name='Техника экипажа')
    reported_by=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.SET_NULL,null=True,blank=True,editable=False,related_name='reported_employee_activities')
    class Meta:
        constraints=[models.UniqueConstraint(fields=['employee','date','shift'],name='one_employee_shift_location')]
    def clean(self):
        super().clean()
        if self.equipment_id and self.date:
            from .equipment_operations_models import EquipmentAssignment
            if EquipmentAssignment.objects.filter(equipment_id=self.equipment_id,shift=self.shift,start_date__lte=self.date,cancelled_at__isnull=True).filter(models.Q(end_date__isnull=True)|models.Q(end_date__gte=self.date)).exclude(construction_object_id=self.construction_object_id).exists():
                raise ValidationError({'equipment':'На эту дату и смену техника назначена другому объекту. Проверьте назначение экипажа.'})
        if self.equipment_id and EquipmentActivity.objects.filter(equipment_id=self.equipment_id,date=self.date,shift=self.shift).exclude(construction_object_id=self.construction_object_id).exists():raise ValidationError({'equipment':'Техника в этой смене находится на другом объекте.'})
        if self.planning_position_id and self.planning_position.company_id!=self.company_id:raise ValidationError({'planning_position':'Должность СМГ другой компании.'})
        if self.position_id and self.position.company_id!=self.company_id:raise ValidationError({'employee':'Должность другой компании.'})
    def __str__(self):return f'{self.employee} · {self.date} · смена {self.shift}'


class EmployeeWorkLink(ActualHistoryMixin, BaseCompanyModel):
    activity = models.ForeignKey(EmployeeActivity, on_delete=models.CASCADE, related_name='work_links')
    work = models.ForeignKey('works.ProjectWork', on_delete=models.PROTECT)
    work_item = models.ForeignKey('works.ProjectWorkItem', on_delete=models.PROTECT, null=True, blank=True)

    class Meta:
        ordering = ['pk']
        constraints = [
            models.UniqueConstraint(fields=['activity','work'], condition=models.Q(work_item__isnull=True), name='unique_employee_simple_work'),
            models.UniqueConstraint(fields=['activity','work','work_item'], condition=models.Q(work_item__isnull=False), name='unique_employee_component_work'),
        ]

    def clean(self):
        super().clean()
        if self.activity.company_id != self.company_id or self.work.company_id != self.company_id:
            raise ValidationError('Связь сотрудника и работы другой компании.')
        if self.work.section.construction_object_id != self.activity.construction_object_id:
            raise ValidationError('Работа другого объекта.')
        if self.work_item_id and (self.work_item.company_id != self.company_id or self.work_item.project_work_id != self.work_id):
            raise ValidationError('Работа не входит в выбранный комплекс.')
        if self.work.kind == 'COMPOSITE' and not self.work_item_id:
            raise ValidationError('Выберите работу в составе комплекса.')


class ProductionEntryDay(BaseCompanyModel):
    roster_started=models.BooleanField(default=False)
    construction_object=models.ForeignKey('projects.ConstructionObject',on_delete=models.CASCADE)
    date=models.DateField()
    dispatcher=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.PROTECT)
    revision=models.PositiveIntegerField(default=0)
    confirmed_at=models.DateTimeField(null=True,blank=True)

    class Meta:
        constraints=[models.UniqueConstraint(fields=['construction_object','date','dispatcher'],name='unique_production_dispatcher_day')]


class WorkFactClaim(BaseCompanyModel):
    construction_object=models.ForeignKey('projects.ConstructionObject',on_delete=models.CASCADE)
    date=models.DateField()
    work=models.ForeignKey('works.ProjectWork',on_delete=models.CASCADE)
    work_item=models.ForeignKey('works.ProjectWorkItem',on_delete=models.CASCADE,null=True,blank=True)
    dispatcher=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.CASCADE)
    session_key=models.UUIDField()
    expires_at=models.DateTimeField(db_index=True)

    class Meta:
        constraints=[
            models.UniqueConstraint(fields=['construction_object','date','work'],condition=models.Q(work_item__isnull=True),name='unique_simple_work_fact_claim'),
            models.UniqueConstraint(fields=['construction_object','date','work','work_item'],condition=models.Q(work_item__isnull=False),name='unique_item_work_fact_claim'),
        ]


class EmployeeFactClaim(BaseCompanyModel):
    construction_object=models.ForeignKey('projects.ConstructionObject',on_delete=models.CASCADE)
    date=models.DateField()
    employee=models.ForeignKey('resources.Employee',on_delete=models.CASCADE)
    shift=models.PositiveSmallIntegerField(default=1)
    dispatcher=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.CASCADE)
    session_key=models.UUIDField()
    expires_at=models.DateTimeField(db_index=True)
    class Meta:
        constraints=[models.UniqueConstraint(fields=['employee','date','shift'],name='unique_employee_fact_claim')]


class FactSectionDay(BaseCompanyModel):
    SECTIONS=[('works','Работы'),('labor','Персонал'),('mechanization','Механизация'),('fuel','ГСМ')]
    construction_object=models.ForeignKey('projects.ConstructionObject',on_delete=models.CASCADE)
    date=models.DateField()
    section=models.CharField(max_length=16,choices=SECTIONS)
    deviation_comment=models.TextField('Причина отклонения по ресурсам',blank=True)
    revision=models.PositiveIntegerField(default=0)
    confirmed_at=models.DateTimeField(null=True,blank=True)
    confirmed_by=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.SET_NULL,null=True,blank=True)
    class Meta:
        constraints=[models.UniqueConstraint(fields=['construction_object','date','section'],name='one_section_day_confirmation')]


class ProductionPresence(BaseCompanyModel):
    construction_object=models.ForeignKey('projects.ConstructionObject',on_delete=models.CASCADE)
    date=models.DateField()
    user=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.CASCADE)
    session_key=models.UUIDField()
    step=models.PositiveSmallIntegerField(default=1)
    last_seen=models.DateTimeField(db_index=True)
    closing_at=models.DateTimeField(null=True,blank=True)
    class Meta:
        constraints=[models.UniqueConstraint(fields=['construction_object','date','session_key'],name='unique_production_presence')]
