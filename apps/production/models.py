"""
Модели модуля производства (Production).
Хранят факты выполнения работ: объёмы, люди, техника, топливо.
"""
from django.db import models
from django.conf import settings
from django.utils.translation import gettext_lazy as _

from core.models import BaseCompanyModel


class DeviationReason(BaseCompanyModel):
    """Справочник причин отклонений факта от плана."""
    code = models.CharField(_('код'), max_length=20, unique=True)
    name = models.CharField(_('название'), max_length=150)
    description = models.TextField(_('описание'), blank=True)
    is_active = models.BooleanField(_('активна'), default=True)

    class Meta:
        verbose_name = _('причина отклонения')
        verbose_name_plural = _('причины отклонений')
        ordering = ['code']

    def __str__(self):
        return f"{self.code} — {self.name}"


class DailyFact(BaseCompanyModel):
    """Дневной факт выполнения работы (объём)."""
    project_work = models.ForeignKey(
        'works.ProjectWork',
        on_delete=models.CASCADE,
        related_name='daily_facts',
        verbose_name=_('работа')
    )
    work_item = models.ForeignKey(
        'works.ProjectWorkItem',
        on_delete=models.CASCADE,
        related_name='daily_facts',
        verbose_name=_('подработа'),
        blank=True,
        null=True
    )
    date = models.DateField(_('дата'))
    actual_quantity = models.DecimalField(
        _('фактический объём'),
        max_digits=12,
        decimal_places=3,
        default=0
    )
    actual_value = models.DecimalField(
        _('фактическая стоимость'),
        max_digits=15,
        decimal_places=2,
        default=0
    )
    reported_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='reported_facts',
        verbose_name=_('внёс')
    )
    deviation_reason = models.ForeignKey(
        DeviationReason,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='facts',
        verbose_name=_('причина отклонения')
    )
    comment = models.TextField(_('комментарий'), blank=True)

    class Meta:
        verbose_name = _('дневной факт')
        verbose_name_plural = _('дневные факты')
        ordering = ['-date', 'project_work']
        unique_together = [['project_work', 'work_item', 'date']]

    def __str__(self):
        return f"{self.project_work} — {self.date}: {self.actual_quantity}"



class LaborFact(BaseCompanyModel):
    """Факт по людским ресурсам (бригадам)."""
    project = models.ForeignKey(
        'projects.Project',
        on_delete=models.CASCADE,
        related_name='labor_facts',
        verbose_name=_('проект'),
        null=True,
        blank=True
    )
    project_work = models.ForeignKey(
        'works.ProjectWork',
        on_delete=models.CASCADE,
        related_name='labor_facts',
        verbose_name=_('работа'),
        null=True,
        blank=True
    )
    date = models.DateField(_('дата'))
    brigade = models.ForeignKey(
        'resources.Brigade',
        on_delete=models.PROTECT,
        related_name='labor_facts',
        verbose_name=_('бригада')
    )
    planned_workers = models.IntegerField(
        _('план, чел'),
        default=0,
        null=True,
        blank=True
    )
    actual_workers = models.IntegerField(
        _('факт, чел'),
        default=0,
        null=True,
        blank=True
    )
    planned_hours = models.DecimalField(
        _('план, чел-час'),
        max_digits=8,
        decimal_places=2,
        null=True,
        blank=True
    )
    actual_hours = models.DecimalField(
        _('факт, чел-час'),
        max_digits=8,
        decimal_places=2,
        null=True,
        blank=True
    )
    hourly_rate = models.DecimalField(
        _('ставка, ₽/час'),
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True
    )
    comment = models.TextField(_('комментарий'), blank=True)

    class Meta:
        verbose_name = _('факт по людям')
        verbose_name_plural = _('факты по людям')
        ordering = ['-date', 'brigade']

    def __str__(self):
        work_name = self.project_work.name if self.project_work else 'Без работы'
        return f"{self.brigade} — {self.date}: {self.actual_workers} чел. ({work_name})"




class LaborPlan(BaseCompanyModel):
    """План по людским ресурсам на день."""
    project = models.ForeignKey(
        'projects.Project',
        on_delete=models.CASCADE,
        related_name='labor_plans',
        verbose_name=_('проект'),
        null = True,
        blank=True
    )
    project_work = models.ForeignKey(
        'works.ProjectWork',
        on_delete=models.CASCADE,
        related_name='labor_plans',
        verbose_name=_('работа'),
        null=True,
        blank=True
    )
    plan_version = models.ForeignKey(
        'planning.PlanVersion',
        on_delete=models.CASCADE,
        related_name='labor_plans',
        verbose_name=_('версия плана'),
        blank=True,
        null=True
    )
    date = models.DateField(_('дата'))
    brigade = models.ForeignKey(
        'resources.Brigade',
        on_delete=models.PROTECT,
        related_name='labor_plans',
        verbose_name=_('бригада')
    )
    planned_workers = models.IntegerField(_('план, чел'), default=0)
    planned_hours = models.DecimalField(
        _('план, чел-час'),
        max_digits=8,
        decimal_places=2,
        null=True,
        blank=True
    )
    hourly_rate = models.DecimalField(
        _('ставка, ₽/час'),
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True
    )
    comment = models.TextField(_('комментарий'), blank=True)

    class Meta:
        verbose_name = _('план по людям')
        verbose_name_plural = _('планы по людям')
        ordering = ['date', 'brigade']
        unique_together = [['project_work', 'date', 'brigade']]

    def __str__(self):
        return f"{self.brigade} — {self.date}: {self.planned_workers} чел."


class EquipmentPlan(BaseCompanyModel):
    """План по технике на день."""
    project = models.ForeignKey(
        'projects.Project',
        on_delete=models.CASCADE,
        related_name='equipment_plans',
        verbose_name=_('проект'),
        null=True,
        blank=True
    )
    project_work = models.ForeignKey(
        'works.ProjectWork',
        on_delete=models.CASCADE,
        related_name='equipment_plans',
        verbose_name=_('работа'),
        null=True,
        blank=True
    )
    plan_version = models.ForeignKey(
        'planning.PlanVersion',
        on_delete=models.CASCADE,
        related_name='equipment_plans',
        verbose_name=_('версия плана'),
        blank=True,
        null=True
    )
    date = models.DateField(_('дата'))
    equipment_type = models.ForeignKey(  # ← ИЗМЕНИЛИ на ForeignKey
        'resources.EquipmentType',
        on_delete=models.PROTECT,
        related_name='equipment_plans',
        verbose_name=_('вид техники')
    )
    equipment_number = models.CharField(
        _('гос. номер / инв. №'),
        max_length=50,
        blank=True
    )
    planned_count = models.IntegerField(_('план, ед'), default=0)
    planned_machine_hours = models.DecimalField(
        _('план, маш-час'),
        max_digits=8,
        decimal_places=2,
        null=True,
        blank=True
    )
    hourly_rate = models.DecimalField(
        _('ставка, ₽/маш-час'),
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True
    )
    comment = models.TextField(_('комментарий'), blank=True)

    class Meta:
        verbose_name = _('план по технике')
        verbose_name_plural = _('планы по технике')
        ordering = ['date', 'equipment_type__name']

    def __str__(self):
        work_name = self.project_work.name if self.project_work else 'Без работы'
        return f"{self.equipment_type.name} — {self.date}: {self.planned_count} ед. ({work_name})"


class EquipmentFact(BaseCompanyModel):
    """Факт по механизмам и технике."""
    project = models.ForeignKey(
        'projects.Project',
        on_delete=models.CASCADE,
        related_name='equipment_facts',
        verbose_name=_('проект'),
        null=True,
        blank=True
    )
    project_work = models.ForeignKey(
        'works.ProjectWork',
        on_delete=models.CASCADE,
        related_name='equipment_facts',
        verbose_name=_('работа'),
        null=True,
        blank=True
    )
    date = models.DateField(_('дата'))
    equipment_type = models.ForeignKey(  # ← ИЗМЕНИЛИ на ForeignKey
        'resources.EquipmentType',
        on_delete=models.PROTECT,
        related_name='equipment_facts',
        verbose_name=_('вид техники')
    )
    equipment_number = models.CharField(
        _('гос. номер / инв. №'),
        max_length=50,
        blank=True,
        help_text=_('Государственный регистрационный номер или инвентарный')
    )
    planned_count = models.IntegerField(_('план, ед'), default=0)
    actual_count = models.IntegerField(_('факт, ед'), default=0)
    machine_hours = models.DecimalField(
        _('машино-часы'),
        max_digits=8,
        decimal_places=2,
        null=True,
        blank=True
    )
    hourly_rate = models.DecimalField(
        _('ставка, ₽/маш-час'),
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True
    )
    comment = models.TextField(_('комментарий'), blank=True)

    class Meta:
        verbose_name = _('факт по технике')
        verbose_name_plural = _('факты по технике')
        ordering = ['-date', 'equipment_type__name']

    def __str__(self):
        work_name = self.project_work.name if self.project_work else 'Без работы'
        return f"{self.equipment_type.name} — {self.date}: {self.machine_hours} м/ч ({work_name})"


class FuelFact(BaseCompanyModel):
    """Факт по ГСМ."""
    FUEL_TYPE_CHOICES = [
        ('DIESEL', _('Дизельное топливо')),
        ('PETROL_92', _('Бензин АИ-92')),
        ('PETROL_95', _('Бензин АИ-95')),
        ('PETROL_98', _('Бензин АИ-98')),
        ('GAS', _('Газ (пропан/метан)')),
        ('OIL', _('Масло моторное')),
        ('OTHER', _('Другое')),
    ]

    project = models.ForeignKey(
        'projects.Project',
        on_delete=models.CASCADE,
        related_name='fuel_facts',
        verbose_name=_('проект'),
        null=True,
        blank=True
    )
    project_work = models.ForeignKey(
        'works.ProjectWork',
        on_delete=models.CASCADE,
        related_name='fuel_facts',
        verbose_name=_('работа'),
        null=True,
        blank=True
    )
    date = models.DateField(_('дата'))
    fuel_type = models.CharField(
        _('вид ГСМ'),
        max_length=20,
        choices=FUEL_TYPE_CHOICES,
        default='DIESEL'
    )
    actual_liters = models.DecimalField(
        _('факт, л'),
        max_digits=10,
        decimal_places=2,
        default=0
    )
    price_per_liter = models.DecimalField(
        _('цена за литр, ₽'),
        max_digits=8,
        decimal_places=2,
        null=True,
        blank=True
    )
    equipment_ref = models.CharField(
        _('привязка к технике'),
        max_length=150,
        blank=True
    )
    comment = models.TextField(_('комментарий'), blank=True)

    class Meta:
        verbose_name = _('факт по ГСМ')
        verbose_name_plural = _('факты по ГСМ')
        ordering = ['-date', 'fuel_type']

    def __str__(self):
        work_name = self.project_work.name if self.project_work else 'Без работы'
        return f"{self.get_fuel_type_display()} — {self.date}: {self.actual_liters} л ({work_name})"


class FuelPlan(BaseCompanyModel):
    """План по ГСМ на день."""
    FUEL_TYPE_CHOICES = [
        ('DIESEL', _('Дизельное топливо')),
        ('PETROL_92', _('Бензин АИ-92')),
        ('PETROL_95', _('Бензин АИ-95')),
        ('PETROL_98', _('Бензин АИ-98')),
        ('GAS', _('Газ (пропан/метан)')),
        ('OIL', _('Масло моторное')),
        ('OTHER', _('Другое')),
    ]

    project = models.ForeignKey(
        'projects.Project',
        on_delete=models.CASCADE,
        related_name='fuel_plans',
        verbose_name=_('проект'),
        null=True,
        blank=True
    )
    project_work = models.ForeignKey(
        'works.ProjectWork',
        on_delete=models.CASCADE,
        related_name='fuel_plans',
        verbose_name=_('работа'),
        null=True,
        blank=True
    )
    plan_version = models.ForeignKey(
        'planning.PlanVersion',
        on_delete=models.CASCADE,
        related_name='fuel_plans',
        verbose_name=_('версия плана'),
        blank=True,
        null=True
    )
    date = models.DateField(_('дата'))
    fuel_type = models.CharField(
        _('вид ГСМ'),
        max_length=20,
        choices=FUEL_TYPE_CHOICES,
        default='DIESEL'
    )
    planned_liters = models.DecimalField(
        _('план, л'),
        max_digits=10,
        decimal_places=2,
        default=0
    )
    price_per_liter = models.DecimalField(
        _('цена за литр, ₽'),
        max_digits=8,
        decimal_places=2,
        null=True,
        blank=True
    )
    equipment_ref = models.CharField(
        _('привязка к технике'),
        max_length=150,
        blank=True
    )
    comment = models.TextField(_('комментарий'), blank=True)

    class Meta:
        verbose_name = _('план по ГСМ')
        verbose_name_plural = _('планы по ГСМ')
        ordering = ['date', 'fuel_type']

    def __str__(self):
        work_name = self.project_work.name if self.project_work else 'Без работы'
        return f"{self.get_fuel_type_display()} — {self.date}: {self.planned_liters} л ({work_name})"
