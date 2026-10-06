from django.db import models
from django.utils.translation import gettext_lazy as _

from core.models import BaseCompanyModel


class Position(BaseCompanyModel):
    code = models.CharField(_('код'), max_length=50, blank=True, editable=False)
    name = models.CharField(_('название'), max_length=100)
    default_hourly_rate = models.DecimalField(_('ставка по умолчанию'), max_digits=10, decimal_places=2, default=0)

    class Meta:
        verbose_name = _('должность')
        verbose_name_plural = _('должности')
        ordering = ['name']
        unique_together = [['company', 'code']]

    def __str__(self):
        return self.name


class Employee(BaseCompanyModel):
    first_name = models.CharField(_('имя'), max_length=100)
    last_name = models.CharField(_('фамилия'), max_length=100)
    middle_name = models.CharField(_('отчество'), max_length=100, blank=True)
    position = models.ForeignKey(
        Position, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='employees',
        verbose_name=_('должность')
    )
    is_active = models.BooleanField(_('активен'), default=True)

    class Meta:
        verbose_name = _('сотрудник')
        verbose_name_plural = _('сотрудники')
        ordering = ['last_name', 'first_name']

    def __str__(self):
        return f"{self.last_name} {self.first_name}"



class BrigadeGroup(BaseCompanyModel):
    name = models.CharField(_('название'), max_length=150)

    class Meta:
        verbose_name = _('группа бригад')
        verbose_name_plural = _('группы бригад')
        ordering = ['name']
        unique_together = [['company', 'name']]

    def __str__(self):
        return self.name


class BrigadeMacroGroup(BaseCompanyModel):
    name = models.CharField(_('название'), max_length=150)

    class Meta:
        verbose_name = _('макрогруппа бригад')
        verbose_name_plural = _('макрогруппы бригад')
        ordering = ['name']
        unique_together = [['company', 'name']]

    def __str__(self):
        return self.name


class Brigade(BaseCompanyModel):
    """Справочник бригад."""
    code = models.CharField(
        _('код бригады'),
        max_length=20, blank=True, editable=False,
        help_text=_('Уникальный код, например: БР-001')
    )
    name = models.CharField(_('название бригады'), max_length=150)

    group = models.ForeignKey(BrigadeGroup, on_delete=models.SET_NULL, null=True, blank=True, related_name='brigades', verbose_name=_('группа'))
    macro_group = models.ForeignKey(BrigadeMacroGroup, on_delete=models.SET_NULL, null=True, blank=True, related_name='brigades', verbose_name=_('макрогруппа'))

    description = models.TextField(_('описание'), blank=True)
    is_active = models.BooleanField(_('активна'), default=True)

    class Meta:
        verbose_name = _('бригада')
        verbose_name_plural = _('бригады')
        ordering = ['code']
        unique_together = [['company', 'code']]

    def __str__(self):
        return f"{self.code} — {self.name}"


class BrigadeMember(BaseCompanyModel):
    brigade = models.ForeignKey(
        Brigade, on_delete=models.CASCADE,
        related_name='members', verbose_name=_('бригада')
    )
    employee = models.ForeignKey(
        Employee, on_delete=models.CASCADE,
        related_name='brigade_memberships', verbose_name=_('сотрудник')
    )
    role_in_brigade = models.CharField(_('роль в бригаде'), max_length=100, blank=True)

    class Meta:
        verbose_name = _('член бригады')
        verbose_name_plural = _('члены бригад')
        unique_together = [['brigade', 'employee']]

    def __str__(self):
        return f"{self.brigade.name} - {self.employee}"


class EquipmentCategory(BaseCompanyModel):
    name = models.CharField(_('название'), max_length=100)

    class Meta:
        verbose_name = _('категория техники')
        verbose_name_plural = _('категории техники')
        ordering = ['name']
        constraints = [models.UniqueConstraint(fields=['company', 'name'], name='unique_equipment_category_name')]

    def __str__(self):
        return self.name


class EquipmentType(BaseCompanyModel):
    """Справочник видов техники."""
    name = models.CharField(_('название'), max_length=150)
    unit = models.CharField(_('единица измерения'), max_length=50, default='ед.')
    category = models.ForeignKey(
        EquipmentCategory, on_delete=models.PROTECT, null=True, blank=True,
        related_name='equipment_types', verbose_name=_('категория'),
    )
    is_active = models.BooleanField(_('активна'), default=True)

    class Meta:
        verbose_name = _('вид техники')
        verbose_name_plural = _('виды техники')
        ordering = ['name']

    def __str__(self):
        return self.name

class Equipment(BaseCompanyModel):
    type = models.ForeignKey(
        EquipmentType, on_delete=models.CASCADE,
        related_name='equipment', verbose_name=_('тип')
    )
    plate_number = models.CharField(_('гос. номер'), max_length=50)
    name = models.CharField(_('название'), max_length=100)
    is_active = models.BooleanField(_('активна'), default=True)

    class Meta:
        verbose_name = _('техника')
        verbose_name_plural = _('техника')
        ordering = ['plate_number']
        unique_together = [['company', 'plate_number']]

    def __str__(self):
        return f"{self.plate_number} - {self.name}"


class FuelType(BaseCompanyModel):
    code = models.CharField(_('код'), max_length=50, blank=True, editable=False)
    name = models.CharField(_('название'), max_length=100)
    unit = models.CharField(_('единица измерения'), max_length=20, default='л')
    default_price = models.DecimalField(_('цена по умолчанию'), max_digits=10, decimal_places=2, default=0)

    class Meta:
        verbose_name = _('тип топлива')
        verbose_name_plural = _('типы топлива')
        ordering = ['name']
        unique_together = [['company', 'code']]

    def __str__(self):
        return self.name