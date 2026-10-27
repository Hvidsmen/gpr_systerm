"""Effective assignments and physical state history, separate from daily facts."""

from core.fact_history import ActualHistoryMixin
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q, F
from core.models import BaseCompanyModel


class EquipmentAssignment(ActualHistoryMixin, BaseCompanyModel):
    equipment = models.ForeignKey('resources.Equipment', on_delete=models.PROTECT, related_name='standing_assignments', verbose_name='Техника')
    construction_object = models.ForeignKey('projects.ConstructionObject', on_delete=models.PROTECT, verbose_name='Объект')
    start_date = models.DateField('С какого дня')
    end_date = models.DateField('Последний день', null=True, blank=True)
    shift = models.PositiveSmallIntegerField('Смена', default=1)
    general = models.BooleanField('На объект в целом', default=False)
    work = models.ForeignKey('works.ProjectWork', on_delete=models.PROTECT, null=True, blank=True, verbose_name='Работа / комплекс')
    work_item = models.ForeignKey('works.ProjectWorkItem', on_delete=models.PROTECT, null=True, blank=True, verbose_name='Работа в составе комплекса')
    activity = models.TextField('Описание работы / основание назначения', max_length=4000)
    revision = models.PositiveIntegerField(default=1, editable=False)
    cancelled_at = models.DateTimeField(null=True, blank=True, editable=False)
    cancel_reason = models.TextField(blank=True, editable=False)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, editable=False)

    class Meta:
        ordering = ['equipment__plate_number', 'equipment_id', 'shift', '-start_date', '-pk']
        indexes = [models.Index(fields=['equipment', 'shift', 'start_date'], name='equipment_assignment_period')]
        constraints = [
            models.CheckConstraint(condition=Q(shift__gte=1, shift__lte=24), name='equipment_assignment_shift'),
            models.CheckConstraint(condition=Q(end_date__isnull=True) | Q(end_date__gte=F('start_date')), name='equipment_assignment_dates'),
        ]

    def clean(self):
        super().clean()
        if not self.company_id:
            return
        for name in ['equipment', 'construction_object', 'work', 'work_item']:
            value = getattr(self, name, None)
            if value and value.company_id != self.company_id:
                raise ValidationError({name: 'Запись другой компании.'})
        if self.end_date and self.start_date and self.end_date < self.start_date:
            raise ValidationError({'end_date': 'Последний день не может быть раньше начала.'})
        if not self.activity.strip():
            raise ValidationError({'activity': 'Опишите работу или основание назначения один раз для всего периода.'})
        if self.general and (self.work_id or self.work_item_id):
            raise ValidationError({'general': 'Для назначения на объект в целом уберите работу.'})
        if self.work_id:
            if self.work.section.construction_object_id != self.construction_object_id:
                raise ValidationError({'work': 'Работа другого объекта.'})
            if self.work.kind == 'COMPOSITE' and not self.work_item_id:
                raise ValidationError({'work_item': 'Выберите работу в составе комплекса.'})
        if self.work_item_id and (not self.work_id or self.work_item.project_work_id != self.work_id):
            raise ValidationError({'work_item': 'Работа другого комплекса.'})
        if self.equipment_id and self.start_date and self.shift and not self.cancelled_at:
            overlapping = type(self).objects.filter(equipment_id=self.equipment_id, shift=self.shift, cancelled_at__isnull=True).exclude(pk=self.pk)
            if self.end_date:
                overlapping = overlapping.filter(start_date__lte=self.end_date)
            if overlapping.filter(Q(end_date__isnull=True) | Q(end_date__gte=self.start_date)).exists():
                raise ValidationError('У техники уже есть назначение на этот период и смену. Сначала завершите прежнее назначение.')


class EquipmentStateChange(ActualHistoryMixin, BaseCompanyModel):
    STATES = [('WORKING', 'Работает'), ('IDLE', 'Исправна, не задействована'), ('REPAIR', 'В ремонте')]
    equipment = models.ForeignKey('resources.Equipment', on_delete=models.PROTECT, related_name='state_history')
    assignment = models.ForeignKey(EquipmentAssignment, on_delete=models.PROTECT, related_name='state_changes')
    effective_date = models.DateField('С какого дня')
    shift = models.PositiveSmallIntegerField('Начиная со смены', default=1)
    state = models.CharField('Состояние', max_length=12, choices=STATES)
    reason = models.TextField('Причина / пояснение', max_length=4000, blank=True)
    recorded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, editable=False)
    is_cancelled = models.BooleanField(default=False, editable=False)

    class Meta:
        ordering = ['-effective_date', '-shift', '-pk']
        constraints = [models.UniqueConstraint(fields=['equipment', 'effective_date', 'shift'], condition=Q(is_cancelled=False), name='one_equipment_state_boundary'),
            models.CheckConstraint(condition=Q(shift__gte=1, shift__lte=24), name='equipment_state_shift')]

    def clean(self):
        super().clean()
        if self.state != 'WORKING' and not self.reason.strip():
            raise ValidationError({'reason': 'Укажите причину простоя или ремонта.'})
        if self.assignment_id:
            assignment = self.assignment
            if (self.company_id, self.equipment_id, self.shift) != (assignment.company_id, assignment.equipment_id, assignment.shift):
                raise ValidationError('Состояние не соответствует назначению техники.')
            if self.effective_date and (self.effective_date < assignment.start_date or assignment.end_date and self.effective_date > assignment.end_date):
                raise ValidationError({'effective_date': 'Дата должна входить в период назначения.'})
