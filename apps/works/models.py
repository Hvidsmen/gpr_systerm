from django.db import models
from django.utils.translation import gettext_lazy as _

from core.models import BaseCompanyModel


class WorkTemplate(BaseCompanyModel):
    code = models.CharField(_('код'), max_length=50)
    name = models.CharField(_('название'), max_length=255)
    unit = models.CharField(_('единица измерения'), max_length=50)
    description = models.TextField(_('описание'), blank=True)
    is_active = models.BooleanField(_('активен'), default=True)

    class Meta:
        verbose_name = _('шаблон работы')
        verbose_name_plural = _('шаблоны работ')
        ordering = ['name']
        unique_together = [['company', 'code']]

    def __str__(self):
        return f"{self.code} - {self.name}"


class WorkTemplateVersion(BaseCompanyModel):
    template = models.ForeignKey(
        WorkTemplate, on_delete=models.CASCADE,
        related_name='versions', verbose_name=_('шаблон')
    )
    version_number = models.PositiveIntegerField(_('номер версии'))
    is_current = models.BooleanField(_('текущая версия'), default=False)
    created_by = models.ForeignKey(
        'accounts.User', on_delete=models.SET_NULL,
        null=True, related_name='created_template_versions',
        verbose_name=_('создано')
    )

    class Meta:
        verbose_name = _('версия шаблона')
        verbose_name_plural = _('версии шаблонов')
        ordering = ['-version_number']
        unique_together = [['template', 'version_number']]

    def __str__(self):
        return f"{self.template.name} v{self.version_number}"


class WorkTemplateItem(BaseCompanyModel):
    version = models.ForeignKey(
        WorkTemplateVersion, on_delete=models.CASCADE,
        related_name='items', verbose_name=_('версия')
    )
    parent = models.ForeignKey(
        'self', on_delete=models.CASCADE,
        null=True, blank=True, related_name='children',
        verbose_name=_('родитель')
    )
    sequence = models.PositiveIntegerField(_('порядок'), default=0)
    name = models.CharField(_('название'), max_length=255)
    unit = models.CharField(_('единица измерения'), max_length=50)
    weight = models.DecimalField(_('вес (%)'), max_digits=5, decimal_places=2, default=0)

    # Норматив подработы на единицу работы
    quantity_per_unit = models.DecimalField(
        _('норматив подработы'),
        max_digits=10,
        decimal_places=3,
        default=1,
        help_text=_('Сколько единиц подработы нужно на 1 единицу работы')
    )

    # Нормативы ресурсов (добавлено)
    labor_norm_per_unit = models.DecimalField(
        _('норматив трудозатрат, чел-час/ед'),
        max_digits=8, decimal_places=3, default=0,
        help_text=_('Сколько чел-часов нужно на 1 единицу объёма подработы')
    )
    equipment_norm_per_unit = models.DecimalField(
        _('норматив техники, маш-час/ед'),
        max_digits=8, decimal_places=3, default=0,
        help_text=_('Сколько машино-часов нужно на 1 единицу объёма подработы')
    )
    fuel_norm_per_unit = models.DecimalField(
        _('норматив ГСМ, л/ед'),
        max_digits=8, decimal_places=3, default=0,
        help_text=_('Сколько литров топлива нужно на 1 единицу объёма подработы')
    )

    # Ставки (добавлено)
    labor_hourly_rate = models.DecimalField(
        _('ставка рабочего, ₽/час'),
        max_digits=10, decimal_places=2, default=350
    )
    equipment_hourly_rate = models.DecimalField(
        _('ставка техники, ₽/маш-час'),
        max_digits=10, decimal_places=2, default=1500
    )
    fuel_price = models.DecimalField(
        _('цена топлива, ₽/л'),
        max_digits=8, decimal_places=2, default=65
    )

    load_profile = models.ForeignKey(
        'planning.LoadProfile', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='template_items',
        verbose_name=_('профиль нагрузки')
    )
    normative = models.JSONField(_('нормативы'), default=dict, blank=True)

    class Meta:
        verbose_name = _('элемент шаблона')
        verbose_name_plural = _('элементы шаблонов')
        ordering = ['sequence']

    def __str__(self):
        return self.name


class ProjectWork(BaseCompanyModel):
    section = models.ForeignKey(
        'projects.Section', on_delete=models.CASCADE,
        related_name='works', verbose_name=_('раздел')
    )
    template = models.ForeignKey(
        WorkTemplate, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='project_works',
        verbose_name=_('шаблон')
    )
    code = models.CharField(_('код'), max_length=50)
    name = models.CharField(_('название'), max_length=255)
    unit = models.CharField(_('единица измерения'), max_length=50)
    unit_price = models.DecimalField(_('цена за единицу'), max_digits=12, decimal_places=2, default=0)
    status = models.CharField(
        _('статус'), max_length=20,
        choices=[
            ('PLANNED', 'Запланировано'),
            ('IN_PROGRESS', 'В работе'),
            ('DONE', 'Выполнено'),
            ('CANCELLED', 'Отменено'),
        ],
        default='PLANNED'
    )

    class Meta:
        verbose_name = _('работа проекта')
        verbose_name_plural = _('работы проекта')
        ordering = ['code']
        unique_together = [['section', 'code']]

    def __str__(self):
        return f"{self.code} - {self.name}"


class ProjectWorkItem(BaseCompanyModel):
    project_work = models.ForeignKey(
        ProjectWork, on_delete=models.CASCADE,
        related_name='items', verbose_name=_('работа')
    )
    parent = models.ForeignKey(
        'self', on_delete=models.CASCADE,
        null=True, blank=True, related_name='children',
        verbose_name=_('родитель')
    )
    sequence = models.PositiveIntegerField(_('порядок'), default=0)
    name = models.CharField(_('название'), max_length=255)
    unit = models.CharField(_('единица измерения'), max_length=50)
    weight = models.DecimalField(_('вес (%)'), max_digits=5, decimal_places=2, default=0)

    # НОВОЕ: норматив подработы на единицу работы (копируется из шаблона)
    quantity_per_unit = models.DecimalField(
        _('норматив подработы'),
        max_digits=10,
        decimal_places=3,
        default=1,
        help_text=_('Сколько единиц подработы нужно на 1 единицу работы')
    )

    # Плановый объём (теперь не null, а с default=0)
    planned_quantity = models.DecimalField(
        _('плановый объем'), max_digits=15, decimal_places=3,
        default=0  # ← ИЗМЕНЕНО: было null=True, blank=True
    )

    load_profile = models.ForeignKey(
        'planning.LoadProfile', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='project_work_items',
        verbose_name=_('профиль нагрузки')
    )

    # Нормативы ресурсов (копируются из шаблона)
    labor_norm_per_unit = models.DecimalField(
        _('норматив трудозатрат, чел-час/ед'),
        max_digits=8, decimal_places=3, default=0,
        help_text=_('Сколько чел-часов нужно на 1 единицу объёма')
    )
    default_brigade = models.CharField(
        _('бригада по умолчанию'),
        max_length=150, blank=True,
        help_text=_('Например: Бригада изолировщиков №1')
    )
    labor_hourly_rate = models.DecimalField(
        _('ставка рабочего, ₽/час'),
        max_digits=10, decimal_places=2, default=350
    )

    equipment_norm_per_unit = models.DecimalField(
        _('норматив техники, маш-час/ед'),
        max_digits=8, decimal_places=3, default=0,
        help_text=_('Сколько машино-часов нужно на 1 единицу объёма')
    )
    default_equipment = models.CharField(
        _('техника по умолчанию'),
        max_length=150, blank=True,
        help_text=_('Например: Экскаватор JCB 3CX')
    )
    equipment_hourly_rate = models.DecimalField(
        _('ставка техники, ₽/маш-час'),
        max_digits=10, decimal_places=2, default=1500
    )

    fuel_norm_per_unit = models.DecimalField(
        _('норматив ГСМ, л/ед'),
        max_digits=8, decimal_places=3, default=0,
        help_text=_('Сколько литров топлива нужно на 1 единицу объёма')
    )
    fuel_price = models.DecimalField(
        _('цена топлива, ₽/л'),
        max_digits=8, decimal_places=2, default=65
    )

    class Meta:
        verbose_name = _('элемент работы')
        verbose_name_plural = _('элементы работ')
        ordering = ['sequence']

    def __str__(self):
        return self.name

    @property
    def planned_value(self):
        """Рассчитать плановую стоимость подработы."""
        return self.planned_quantity * self.project_work.unit_price