from django.contrib.auth.models import AbstractUser
from django.db import models
from django.utils.translation import gettext_lazy as _

from core.models import BaseTimestampedModel


class Company(BaseTimestampedModel):
    name = models.CharField(_('название'), max_length=255)
    inn = models.CharField(_('ИНН'), max_length=12, unique=True, blank=True, null=True)
    is_active = models.BooleanField(_('активна'), default=True)

    class Meta:
        verbose_name = _('компания')
        verbose_name_plural = _('компании')
        ordering = ['name']

    def __str__(self):
        return self.name


class Role(BaseTimestampedModel):
    code = models.CharField(_('код роли'), max_length=50, unique=True)
    name = models.CharField(_('название'), max_length=100)
    description = models.TextField(_('описание'), blank=True)

    class Meta:
        verbose_name = _('роль')
        verbose_name_plural = _('роли')
        ordering = ['name']

    def __str__(self):
        return self.name


class User(AbstractUser):
    company = models.ForeignKey(
        Company, on_delete=models.CASCADE,
        related_name='users', verbose_name=_('компания'),
        null=True, blank=True
    )
    role = models.ForeignKey(
        Role, on_delete=models.SET_NULL,
        related_name='users', verbose_name=_('роль'),
        null=True, blank=True
    )
    assigned_objects = models.ManyToManyField(
        'projects.ConstructionObject', blank=True, related_name='assigned_users',
        verbose_name=_('назначенные строительные объекты'),
    )
    position = models.CharField(_('должность'), max_length=100, blank=True)
    phone = models.CharField(_('телефон'), max_length=20, blank=True)

    class Meta:
        verbose_name = _('пользователь')
        verbose_name_plural = _('пользователи')
        ordering = ['last_name', 'first_name']

    def __str__(self):
        return f"{self.last_name} {self.first_name}" if self.last_name else self.username

    @property
    def full_name(self):
        return f"{self.last_name} {self.first_name}".strip() or self.username

    def has_role(self, role_code):
        return (role_code == 'ADMIN' and self.is_superuser) or (self.role and self.role.code == role_code)

    def is_admin(self):
        return self.has_role('ADMIN')

    def is_planner(self):
        return self.has_role('PLANNER')

    def is_manager(self):
        return self.has_role('MANAGER')

    def is_foreman(self):
        return self.has_role('FOREMAN')

from django.core.exceptions import ValidationError
from django.db.models.signals import m2m_changed, pre_save
from django.dispatch import receiver


@receiver(m2m_changed, sender=User.assigned_objects.through)
def validate_object_assignments(sender, instance, action, reverse, pk_set, **kwargs):
    if action != 'pre_add':
        return
    if reverse:
        invalid = User.objects.filter(pk__in=pk_set).exclude(company_id=instance.company_id).exists()
    else:
        from apps.projects.models import ConstructionObject
        invalid = ConstructionObject.objects.filter(pk__in=pk_set).exclude(company_id=instance.company_id).exists()
    if invalid:
        raise ValidationError('Нельзя назначить объект другой компании.')


@receiver(pre_save, sender=User)
def protect_assignment_company(sender, instance, **kwargs):
    if instance.pk and instance.assigned_objects.exclude(company_id=instance.company_id).exists():
        raise ValidationError('Перед сменой компании удалите назначения строительных объектов.')
