from django.db import models
from django.utils.translation import gettext_lazy as _

from core.enums import ProjectStatus
from core.models import BaseCompanyModel


class Project(BaseCompanyModel):
    code = models.CharField(_('код проекта'), max_length=50, blank=True, editable=False)
    name = models.CharField(_('название'), max_length=255)
    description = models.TextField(_('описание'), blank=True)
    status = models.CharField(
        _('статус'), max_length=20,
        choices=ProjectStatus.choices, default=ProjectStatus.ACTIVE
    )
    start_date = models.DateField(_('дата начала'), null=True, blank=True)
    end_date = models.DateField(_('дата окончания'), null=True, blank=True)

    class Meta:
        verbose_name = _('проект')
        verbose_name_plural = _('проекты')
        ordering = ['-created_at']
        unique_together = [['company', 'code']]

    def __str__(self):
        return f"{self.code} - {self.name}"


class ConstructionObject(BaseCompanyModel):
    project = models.ForeignKey(
        Project, on_delete=models.CASCADE,
        related_name='construction_objects',  # ← ИСПРАВЛЕНО
        verbose_name=_('проект')
    )
    parent = models.ForeignKey(
        'self', on_delete=models.CASCADE,
        null=True, blank=True, related_name='children',
        verbose_name=_('родительский объект')
    )
    code = models.CharField(_('код'), max_length=50, blank=True, editable=False)
    name = models.CharField(_('название'), max_length=255)

    class Meta:
        verbose_name = _('строительный объект')
        verbose_name_plural = _('строительные объекты')
        ordering = ['name']
        unique_together = [['project', 'code']]

    def __str__(self):
        return f"{self.code} - {self.name}"


class Section(BaseCompanyModel):
    construction_object = models.ForeignKey(  # ← ИСПРАВЛЕНО (было 'object')
        ConstructionObject, on_delete=models.CASCADE,
        related_name='sections', verbose_name=_('строительный объект')
    )
    code = models.CharField(_('код'), max_length=50, blank=True, editable=False)
    name = models.CharField(_('название'), max_length=255)

    class Meta:
        verbose_name = _('раздел')
        verbose_name_plural = _('разделы')
        ordering = ['name']
        unique_together = [['construction_object', 'code']]

    def __str__(self):
        return f"{self.code} - {self.name}"