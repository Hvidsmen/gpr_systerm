"""Named people and individual machines in daily operational facts."""
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from core.models import BaseCompanyModel


class ShiftActivity(BaseCompanyModel):
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
        requires_work=not self.general and getattr(self,'state','WORKING')=='WORKING'
        if requires_work and not self.work_id and not self.activity.strip():raise ValidationError({'activity':'Укажите работу либо причину её отсутствия.'})
        if getattr(self,'state','WORKING')!='WORKING' and not self.activity.strip():raise ValidationError({'activity':'Укажите причину простоя или описание ремонта.'})
        if self.general and self.work_id:raise ValidationError({'general':'Для назначения на объект в целом уберите выбранную работу.'})


class EquipmentActivity(ShiftActivity):
    STATES=[('WORKING','Работала'),('IDLE','Исправна, не задействована'),('REPAIR','В ремонте')]
    equipment=models.ForeignKey('resources.Equipment',on_delete=models.PROTECT,verbose_name='Конкретная техника')
    state=models.CharField('Состояние',max_length=12,choices=STATES,default='WORKING')
    class Meta:
        constraints=[models.UniqueConstraint(fields=['equipment','date','shift'],name='one_equipment_shift_location')]
    def clean(self):
        super().clean()
        if self.state=='WORKING' and not self.work_id and not self.activity.strip():raise ValidationError({'activity':'Опишите, что делала техника общего назначения, или выберите работу.'})
        if EmployeeActivity.objects.filter(equipment_id=self.equipment_id,date=self.date,shift=self.shift).exclude(construction_object_id=self.construction_object_id).exists():raise ValidationError({'equipment':'Экипаж этой техники уже учтён на другом объекте в этой смене.'})
        if self.state!='WORKING' and self.work_id:raise ValidationError({'work':'При ремонте или простое опишите состояние без выполненной работы.'})
    def __str__(self):return f'{self.equipment} · {self.date} · смена {self.shift}'


class EmployeeActivity(ShiftActivity):
    employee=models.ForeignKey('resources.Employee',on_delete=models.PROTECT,verbose_name='Сотрудник (ФИО)')
    position=models.ForeignKey('resources.Position',on_delete=models.PROTECT,null=True,blank=True,verbose_name='Должность на дату факта')
    equipment=models.ForeignKey('resources.Equipment',on_delete=models.PROTECT,null=True,blank=True,verbose_name='Техника экипажа')
    class Meta:
        constraints=[models.UniqueConstraint(fields=['employee','date','shift'],name='one_employee_shift_location')]
    def clean(self):
        super().clean()
        if self.equipment_id and EquipmentActivity.objects.filter(equipment_id=self.equipment_id,date=self.date,shift=self.shift).exclude(construction_object_id=self.construction_object_id).exists():raise ValidationError({'equipment':'Техника в этой смене находится на другом объекте.'})
        if self.position_id and self.position.company_id!=self.company_id:raise ValidationError({'employee':'Должность другой компании.'})
    def __str__(self):return f'{self.employee} · {self.date} · смена {self.shift}'


class FactSectionDay(BaseCompanyModel):
    SECTIONS=[('works','Работы'),('labor','Персонал'),('mechanization','Механизация и ГСМ')]
    construction_object=models.ForeignKey('projects.ConstructionObject',on_delete=models.CASCADE)
    date=models.DateField()
    section=models.CharField(max_length=16,choices=SECTIONS)
    deviation_comment=models.TextField('Причина отклонения по ресурсам',blank=True)
    revision=models.PositiveIntegerField(default=0)
    confirmed_at=models.DateTimeField(null=True,blank=True)
    confirmed_by=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.SET_NULL,null=True,blank=True)
    class Meta:
        constraints=[models.UniqueConstraint(fields=['construction_object','date','section'],name='one_section_day_confirmation')]
