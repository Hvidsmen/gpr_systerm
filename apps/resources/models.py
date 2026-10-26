from core.excel_mapping import validate_excel_aliases
from django.db import models
from django.utils.translation import gettext_lazy as _

from core.models import BaseCompanyModel
from .employee_staffing import staffing_parts


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
    first_name = models.CharField(_('имя'), max_length=100, blank=True)
    last_name = models.CharField(_('фамилия'), max_length=100, blank=True)
    middle_name = models.CharField(_('отчество'), max_length=100, blank=True)
    position = models.ForeignKey(
        Position, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='employees',
        verbose_name=_('должность')
    )
    is_active = models.BooleanField(_('активен'), default=True)

    employee_number = models.CharField('Табельный номер ЗУП', max_length=60, blank=True)
    zup_full_name = models.CharField('Полное ФИО ЗУП', max_length=250, blank=True, editable=False)
    zup_ref = models.CharField(max_length=32, blank=True, editable=False)
    zup_server = models.CharField(max_length=255, blank=True, editable=False)
    zup_database = models.CharField(max_length=128, blank=True, editable=False)
    zup_organisation = models.CharField('Организация ЗУП', max_length=250, blank=True, editable=False)
    zup_department = models.CharField('Подразделение ЗУП', max_length=250, blank=True, editable=False)
    zup_position_name = models.CharField('Должность ЗУП', max_length=250, blank=True, editable=False)
    zup_synced_at = models.DateTimeField('Обновлено из ЗУП', null=True, blank=True, editable=False)
    zup_card = models.JSONField('Данные карточки ЗУП', default=dict, blank=True, editable=False)

    @property
    def display_name(self):
        return self.zup_full_name or ' '.join(part for part in [self.last_name,self.first_name,self.middle_name] if part) or ('Сотрудник ЗУП · '+(self.employee_number or self.zup_ref[:8]) if self.zup_ref else 'ФИО не заполнено')

    @property
    def name_note(self):
        if not self.zup_ref:return ''
        if not any([self.zup_full_name,self.last_name,self.first_name,self.middle_name]):return 'ФИО не заполнено в ЗУП'
        if not self.last_name or not self.first_name:return 'ФИО без разбивки' if self.zup_full_name else 'Неполные данные ФИО'
        return ''

    def clean(self):
        super().clean()
        if not self.zup_ref:
            from django.core.exceptions import ValidationError
            errors={field:'Обязательное поле.' for field in ['first_name','last_name'] if not getattr(self,field)}
            if errors:raise ValidationError(errors)

    @property
    def display_position(self):
        return self.zup_position_name or staffing_parts(self.zup_card.get('staffing_position', ''))[0] or str(self.position or '')

    @property
    def display_department(self):
        return self.zup_department or (staffing_parts(self.zup_card.get('staffing_position', ''))[1] if self.position_from_staffing else '')

    @property
    def position_from_staffing(self):
        return not self.zup_position_name and bool(self.zup_card.get('staffing_position'))

    class Meta:
        verbose_name = _('сотрудник')
        verbose_name_plural = _('сотрудники')
        ordering = ['last_name', 'first_name']
        constraints = [models.UniqueConstraint(fields=['company', 'zup_server', 'zup_database', 'zup_ref'], condition=~models.Q(zup_ref=''), name='unique_employee_zup_source')]

    def __str__(self):
        return self.display_name



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
    excel_aliases = models.JSONField('Названия в Excel', default=list, blank=True, validators=[validate_excel_aliases])
    """Справочник бригад."""
    code = models.CharField(
        _('код бригады'),
        max_length=20, blank=True, editable=False,
        help_text=_('Уникальный код, например: БР-001')
    )
    name = models.CharField(_('название бригады'), max_length=150)

    unit = models.CharField(_('единица измерения'), max_length=50, default='чел.')
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
        return self.name


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


from .driver_categories import validate_driver_categories, validate_driver_classes, DRIVER_CATEGORY_SHORT


class EquipmentType(BaseCompanyModel):
    parent = models.ForeignKey('self',null=True,blank=True,on_delete=models.SET_NULL,related_name='model_variants',verbose_name='Категория для ГПР')
    brand_model = models.CharField('Марка / модель',max_length=150,blank=True)
    excel_aliases = models.JSONField('Названия в Excel', default=list, blank=True, validators=[validate_excel_aliases])
    """Справочник видов техники."""
    name = models.CharField(_('название'), max_length=150)
    unit = models.CharField(_('единица измерения'), max_length=50, default='ед.')
    category = models.ForeignKey(
        EquipmentCategory, on_delete=models.PROTECT, null=True, blank=True,
        related_name='equipment_types', verbose_name=_('категория'),
    )
    driver_categories = models.JSONField(_('допустимые категории водителя'), default=list, blank=True, validators=[validate_driver_categories])
    driver_classes = models.JSONField(_('допустимые классы водителя'), default=list, blank=True, validators=[validate_driver_classes])

    @property
    def driver_class_labels(self):
        return [code+'-й класс' for code in self.driver_classes]

    @property
    def driver_category_labels(self):
        return [DRIVER_CATEGORY_SHORT[code] for code in self.driver_categories if code in DRIVER_CATEGORY_SHORT]

    is_active = models.BooleanField(_('активна'), default=True)

    class Meta:
        verbose_name = _('вид техники')
        verbose_name_plural = _('виды техники')
        ordering = ['name']

    def __str__(self):
        return self.name

class Equipment(BaseCompanyModel):
    manufacture_year = models.PositiveSmallIntegerField('Год выпуска',null=True,blank=True)
    brand_model = models.CharField('Марка / модель',max_length=150,blank=True)
    inventory_number = models.CharField('Инвентарный номер',max_length=50,blank=True)
    vin = models.CharField('VIN / заводской номер',max_length=100,blank=True)
    site_name = models.CharField('Участок',max_length=150,blank=True)
    operator_position = models.ForeignKey('Position',null=True,blank=True,on_delete=models.SET_NULL,related_name='equipment_units',verbose_name='Должность водителя / машиниста')
    import_key = models.CharField(max_length=80,blank=True,editable=False)
    type = models.ForeignKey(
        EquipmentType, on_delete=models.CASCADE,
        related_name='equipment', verbose_name=_('тип')
    )
    plate_number = models.CharField(_('гос. номер'), max_length=50,blank=True)
    name = models.CharField(_('название'), max_length=150)
    is_active = models.BooleanField(_('активна'), default=True)

    class Meta:
        verbose_name = _('техника')
        verbose_name_plural = _('техника')
        ordering = ['plate_number']
        constraints = [models.UniqueConstraint(fields=['company','plate_number'],condition=~models.Q(plate_number=''),name='unique_nonempty_equipment_plate'),models.UniqueConstraint(fields=['company','import_key'],condition=~models.Q(import_key=''),name='unique_equipment_import_key')]

    def __str__(self):
        return f"{self.plate_number or self.inventory_number or self.vin or 'Без номера'} - {self.name}"


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

class EquipmentTypeMerge(BaseCompanyModel):
    """Alias and audit trail; frozen planning snapshots keep their original IDs."""
    source = models.OneToOneField(EquipmentType, on_delete=models.PROTECT, related_name="merge_source")
    target = models.ForeignKey(EquipmentType, on_delete=models.PROTECT, related_name="merged_types")
    created_by = models.ForeignKey("accounts.User", on_delete=models.PROTECT)
    audit = models.JSONField(default=dict)

    def clean(self):
        from django.core.exceptions import ValidationError
        if self.source_id == self.target_id:
            raise ValidationError("Нельзя объединить запись с самой собой.")
        for item in [self.source, self.target, self.created_by]:
            if item.company_id != self.company_id:
                raise ValidationError("Объединение должно принадлежать одной компании.")

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)

    class Meta:
        ordering = ["pk"]


class BrigadeMerge(BaseCompanyModel):
    """Alias and audit trail; frozen planning snapshots keep their original IDs."""
    source = models.OneToOneField(Brigade, on_delete=models.PROTECT, related_name="merge_source")
    target = models.ForeignKey(Brigade, on_delete=models.PROTECT, related_name="merged_types")
    created_by = models.ForeignKey("accounts.User", on_delete=models.PROTECT)
    audit = models.JSONField(default=dict)

    def clean(self):
        from django.core.exceptions import ValidationError
        if self.source_id == self.target_id:
            raise ValidationError("Нельзя объединить запись с самой собой.")
        for item in [self.source, self.target, self.created_by]:
            if item.company_id != self.company_id:
                raise ValidationError("Объединение должно принадлежать одной компании.")

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)

    class Meta:
        ordering = ["pk"]


class EquipmentCrewPosition(BaseCompanyModel):
    equipment_type = models.ForeignKey(EquipmentType,on_delete=models.CASCADE,related_name='crew_positions',verbose_name='Вид техники')
    position = models.ForeignKey(Position,on_delete=models.PROTECT,related_name='equipment_crews',verbose_name='Должность')
    count = models.PositiveIntegerField('Человек на единицу техники за смену',default=1)
    is_replacement = models.BooleanField('Со звёздочкой — замещение',default=False,help_text='Информационный маркер. Не влияет на численность и расчёты.')

    def clean(self):
        from django.core.exceptions import ValidationError
        if self.count is not None and self.count<1:raise ValidationError({'count':'Укажите не менее одного человека.'})
        if self.equipment_type_id and self.equipment_type.company_id!=self.company_id:raise ValidationError('Техника другой компании.')
        if self.position_id and self.position.company_id!=self.company_id:raise ValidationError('Должность другой компании.')

    def __str__(self):
        return str(self.position)+(' *' if self.is_replacement else '')

    class Meta:
        ordering=['position__name','pk']
        constraints=[models.UniqueConstraint(fields=['equipment_type','position'],name='unique_equipment_crew_position'),models.CheckConstraint(condition=models.Q(count__gte=1),name='positive_equipment_crew_count')]


class PositionPlanningBrigade(BaseCompanyModel):
    position = models.OneToOneField(Position,on_delete=models.PROTECT,related_name='planning_mapping')
    brigade = models.OneToOneField(Brigade,on_delete=models.PROTECT,related_name='position_mapping')
