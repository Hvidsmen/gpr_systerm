"""Physical equipment movements, technical condition and daily use in camps."""

from core.fact_history import ActualHistoryMixin
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from core.models import BaseCompanyModel

CONDITIONS = [('READY', 'Исправна'), ('REPAIR', 'В ремонте'), ('SCRAP', 'Под списание / на запчасти')]
USES = [('WORKED', 'Работала'), ('IDLE', 'Простой')]


class EquipmentCampStay(ActualHistoryMixin, BaseCompanyModel):
    equipment = models.ForeignKey('resources.Equipment', on_delete=models.PROTECT, related_name='camp_stays')
    camp = models.ForeignKey('personnel.Camp', on_delete=models.PROTECT, related_name='equipment_stays')
    arrived_on = models.DateField()
    departed_on = models.DateField(null=True, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [models.Index(fields=['camp', 'arrived_on', 'departed_on'], name='transport_camp_presence')]
        constraints = [models.CheckConstraint(condition=models.Q(departed_on=None) | models.Q(departed_on__gte=models.F('arrived_on')), name='transport_stay_dates')]

    def clean(self):
        super().clean()
        if self.equipment.company_id != self.company_id or self.camp.company_id != self.company_id:
            raise ValidationError('Техника или ВЖГ другой компании.')
        if self.departed_on and self.departed_on < self.arrived_on:
            raise ValidationError('Выезд не может быть раньше приезда.')
        if not self.cancelled_at and self.arrived_on != self.departed_on:
            overlapping = type(self).objects.filter(equipment=self.equipment, cancelled_at=None).exclude(pk=self.pk).exclude(departed_on=models.F('arrived_on')).filter(models.Q(departed_on=None) | models.Q(departed_on__gt=self.arrived_on))
            if self.departed_on:
                overlapping = overlapping.filter(arrived_on__lt=self.departed_on)
            if overlapping.exists():
                raise ValidationError('У машины уже есть размещение на этот период.')


class EquipmentTransportEvent(ActualHistoryMixin, BaseCompanyModel):
    KINDS = [('ARRIVAL', 'Приезд'), ('DEPARTURE', 'Выезд'), ('TRANSFER', 'Перевод'), ('WRITEOFF', 'Списание'), ('CONDITION', 'Техническое состояние')]
    equipment = models.ForeignKey('resources.Equipment', on_delete=models.PROTECT, related_name='transport_events')
    date = models.DateField()
    kind = models.CharField(max_length=12, choices=KINDS)
    condition = models.CharField(max_length=12, choices=CONDITIONS, blank=True)
    from_camp = models.ForeignKey('personnel.Camp', on_delete=models.PROTECT, null=True, blank=True, related_name='+')
    to_camp = models.ForeignKey('personnel.Camp', on_delete=models.PROTECT, null=True, blank=True, related_name='+')
    previous_stay = models.ForeignKey(EquipmentCampStay, on_delete=models.PROTECT, null=True, blank=True, related_name='+')
    resulting_stay = models.ForeignKey(EquipmentCampStay, on_delete=models.PROTECT, null=True, blank=True, related_name='+')
    previous_active = models.BooleanField(null=True, editable=False)
    comment = models.TextField(blank=True, max_length=4000)
    recorded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name='+')
    cancelled_at = models.DateTimeField(null=True, blank=True)
    cancelled_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    cancellation_reason = models.TextField(blank=True, max_length=4000)

    class Meta:
        ordering = ['-date', '-pk']
        indexes = [models.Index(fields=['equipment', 'date'], name='transport_event_timeline')]

    def clean(self):
        super().clean()
        for field in ('equipment', 'from_camp', 'to_camp', 'previous_stay', 'resulting_stay'):
            value = getattr(self, field, None)
            if value and value.company_id != self.company_id:
                raise ValidationError('Событие другой компании.')
        if self.kind == 'CONDITION' and self.condition not in dict(CONDITIONS):
            raise ValidationError('Выберите техническое состояние.')


class EquipmentDayRecord(ActualHistoryMixin, BaseCompanyModel):
    equipment = models.ForeignKey('resources.Equipment', on_delete=models.PROTECT, related_name='transport_days')
    camp = models.ForeignKey('personnel.Camp', on_delete=models.PROTECT, related_name='equipment_days')
    date = models.DateField()
    status = models.CharField(max_length=12, choices=USES)
    construction_object = models.ForeignKey('projects.ConstructionObject', on_delete=models.PROTECT, null=True, blank=True)
    work = models.ForeignKey('works.ProjectWork', on_delete=models.PROTECT, null=True, blank=True)
    work_item = models.ForeignKey('works.ProjectWorkItem', on_delete=models.PROTECT, null=True, blank=True)
    comment = models.TextField(blank=True, max_length=4000)
    activity = models.OneToOneField('production.EquipmentActivity', on_delete=models.PROTECT, null=True, blank=True, related_name='transport_record', editable=False)
    recorded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name='+')

    class Meta:
        constraints = [models.UniqueConstraint(fields=['equipment', 'date'], name='one_transport_equipment_day')]
        ordering = ['date', 'equipment_id']

    def clean(self):
        super().clean()
        for field in ('equipment', 'camp', 'construction_object', 'work', 'work_item'):
            value = getattr(self, field, None)
            if value and value.company_id != self.company_id:
                raise ValidationError('Дневная запись другой компании.')
        if self.work_id and (not self.construction_object_id or self.work.section.construction_object_id != self.construction_object_id):
            raise ValidationError('Работа другого объекта.')
        if self.work_item_id and (not self.work_id or self.work_item.project_work_id != self.work_id):
            raise ValidationError('Работа другого комплекса.')
        if self.work_id and self.work.kind == 'COMPOSITE' and not self.work_item_id:
            raise ValidationError('Выберите работу в составе комплекса.')
        if self.status != 'WORKED' and (self.work_id or self.work_item_id):
            raise ValidationError('При простое выполненная работа не указывается.')


class EquipmentTransportControl(BaseCompanyModel):
    equipment = models.OneToOneField('resources.Equipment', on_delete=models.CASCADE, related_name='transport_control')
    revision = models.PositiveIntegerField(default=0)
    locked_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name='+')
    session_key = models.UUIDField(null=True)
    expires_at = models.DateTimeField(null=True)
