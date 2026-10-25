"""Independent work-only plan for the complete construction object."""
from django.db import models
from django.core.exceptions import ValidationError
from core.models import BaseCompanyModel


class ObjectWorkPlan(BaseCompanyModel):
    construction_object=models.ForeignKey('projects.ConstructionObject',on_delete=models.PROTECT,related_name='object_work_plans',verbose_name='Строительный объект')
    name=models.CharField('Название плана',max_length=255,default='План на весь объект')
    description=models.TextField('Описание',blank=True)
    created_by=models.ForeignKey('accounts.User',on_delete=models.PROTECT,related_name='created_object_work_plans')
    gpr_versions=models.ManyToManyField('planning.GlobalPlanVersion',blank=True,related_name='object_work_plans',verbose_name='Связанные ГПР')
    smg_schedules=models.ManyToManyField('planning.DailySchedule',blank=True,related_name='object_work_plans',verbose_name='Связанные СМГ')

    def clean(self):
        if self.construction_object_id and self.construction_object.company_id!=self.company_id:raise ValidationError({'construction_object':'Объект другой компании.'})

    def __str__(self):return self.name

    class Meta:
        ordering=['-updated_at','-pk']
        verbose_name='План на весь объект'
        verbose_name_plural='Планы на весь объект'


class ObjectWorkPlanRow(BaseCompanyModel):
    plan=models.ForeignKey(ObjectWorkPlan,on_delete=models.CASCADE,related_name='rows')
    work=models.ForeignKey('works.ProjectWork',on_delete=models.CASCADE,related_name='object_plan_rows')
    work_item=models.ForeignKey('works.ProjectWorkItem',on_delete=models.CASCADE,null=True,blank=True,related_name='object_plan_rows')
    quantity=models.DecimalField('Плановый объём',max_digits=16,decimal_places=6,default=0)

    def clean(self):
        if self.plan_id and self.plan.company_id!=self.company_id:raise ValidationError('План другой компании.')
        if self.work_id and (self.work.company_id!=self.company_id or self.work.section.construction_object_id!=self.plan.construction_object_id):raise ValidationError('Работа другого объекта.')
        if self.work_item_id and (self.work_item.company_id!=self.company_id or self.work_item.project_work_id!=self.work_id):raise ValidationError('Работа не принадлежит выбранному комплексу.')
        if self.quantity is not None and self.quantity<0:raise ValidationError({'quantity':'Объём не может быть отрицательным.'})

    @property
    def label(self):return self.work_item.name if self.work_item_id else self.work.name

    @property
    def unit(self):return self.work_item.unit if self.work_item_id else self.work.unit

    class Meta:
        ordering=['work__section__name','work__complex_object__name','work__name','work_id','work_item_id','pk']
        constraints=[models.UniqueConstraint(fields=['plan','work'],condition=models.Q(work_item__isnull=True),name='object_plan_unique_work'),models.UniqueConstraint(fields=['plan','work_item'],condition=models.Q(work_item__isnull=False),name='object_plan_unique_item'),models.CheckConstraint(condition=models.Q(quantity__gte=0),name='object_plan_nonnegative_volume')]
