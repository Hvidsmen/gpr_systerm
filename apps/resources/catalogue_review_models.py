from django.conf import settings
from django.db import models
from core.models import BaseCompanyModel


class CatalogueReviewRequest(BaseCompanyModel):
    kind = models.CharField(max_length=12, choices=[('labor', 'Должность'), ('equipment', 'Техника для планирования')])
    status = models.CharField(max_length=12, default='PENDING', choices=[('PENDING', 'На проверке'),
        ('CONFIRMED', 'Подтверждено'), ('REPLACED', 'Заменено существующей'), ('REJECTED', 'Отклонено')])
    brigade = models.OneToOneField('resources.Brigade', null=True, blank=True, on_delete=models.PROTECT, related_name='catalogue_review')
    equipment_type = models.OneToOneField('resources.EquipmentType', null=True, blank=True, on_delete=models.PROTECT, related_name='catalogue_review')
    target_brigade = models.ForeignKey('resources.Brigade', null=True, blank=True, on_delete=models.PROTECT, related_name='resolved_catalogue_requests')
    target_equipment = models.ForeignKey('resources.EquipmentType', null=True, blank=True, on_delete=models.PROTECT, related_name='resolved_catalogue_requests')
    version = models.ForeignKey('planning.GlobalPlanVersion', null=True, blank=True, on_delete=models.SET_NULL, related_name='catalogue_requests')
    schedule = models.ForeignKey('planning.DailySchedule', null=True, blank=True, on_delete=models.SET_NULL, related_name='catalogue_requests')
    origin_label = models.CharField(max_length=500)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='proposed_catalogue_requests')
    reviewed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name='reviewed_catalogue_requests')
    reviewed_at = models.DateTimeField(null=True, blank=True)
    comment = models.TextField(blank=True, max_length=4000)
    revision = models.PositiveIntegerField(default=0)
    history = models.JSONField(default=list, blank=True)

    @property
    def resource(self):
        return self.brigade if self.kind == 'labor' else self.equipment_type

    @property
    def target(self):
        return self.target_brigade if self.kind == 'labor' else self.target_equipment

    class Meta:
        ordering = ['-created_at', '-pk']
        indexes = [models.Index(fields=['company', 'kind', 'status'], name='catalogue_review_queue')]
        constraints = [models.CheckConstraint(condition=(
            models.Q(kind='labor', brigade__isnull=False, equipment_type__isnull=True) |
            models.Q(kind='equipment', brigade__isnull=True, equipment_type__isnull=False)),
            name='one_proposed_catalogue_resource')]
