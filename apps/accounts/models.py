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
        return self.role and self.role.code == role_code

    def is_admin(self):
        return self.has_role('ADMIN')

    def is_planner(self):
        return self.has_role('PLANNER')

    def is_manager(self):
        return self.has_role('MANAGER')

    def is_foreman(self):
        return self.has_role('FOREMAN')