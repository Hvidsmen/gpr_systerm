"""
Модели модуля производства (Production).
Хранят факты выполнения работ: объёмы, люди, техника, топливо.
"""

from django.db import models
from django.conf import settings
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

from core.models import BaseCompanyModel


class DeviationReason(BaseCompanyModel):
    """Справочник причин отклонений факта от плана."""

    code = models.CharField(_("код"), max_length=20, blank=True, editable=False, unique=True)
    name = models.CharField(_("название"), max_length=150)
    description = models.TextField(_("описание"), blank=True)
    is_active = models.BooleanField(_("активна"), default=True)

    class Meta:
        verbose_name = _("причина отклонения")
        verbose_name_plural = _("причины отклонений")
        ordering = ["code"]

    def __str__(self):
        return f"{self.code} — {self.name}"


class DailyFact(BaseCompanyModel):
    """Дневной факт выполнения работы (объём)."""

    project_work = models.ForeignKey(
        "works.ProjectWork",
        on_delete=models.CASCADE,
        related_name="daily_facts",
        verbose_name=_("работа"),
    )
    work_item = models.ForeignKey(
        "works.ProjectWorkItem",
        on_delete=models.CASCADE,
        related_name="daily_facts",
        verbose_name=_("подработа"),
        blank=True,
        null=True,
    )
    date = models.DateField(_("дата"))
    actual_quantity = models.DecimalField(
        _("фактический объём"), max_digits=12, decimal_places=3, default=0
    )
    actual_value = models.DecimalField(
        _("фактическая стоимость"), max_digits=15, decimal_places=2, default=0
    )
    reported_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reported_facts",
        verbose_name=_("внёс"),
    )
    deviation_reason = models.ForeignKey(
        DeviationReason,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="facts",
        verbose_name=_("причина отклонения"),
    )
    comment = models.TextField(_("комментарий"), blank=True)

    class Meta:
        verbose_name = _("дневной факт")
        verbose_name_plural = _("дневные факты")
        ordering = ["-date", "project_work"]
        constraints = [
            models.UniqueConstraint(
                fields=["project_work", "work_item", "date"],
                condition=models.Q(work_item__isnull=False),
                name="unique_item_fact",
            ),
            models.UniqueConstraint(
                fields=["project_work", "date"],
                condition=models.Q(work_item__isnull=True),
                name="unique_simple_fact",
            ),
        ]

    def clean(self):
        super().clean()
        if self.project_work_id:
            work = self.project_work
            if self.company_id != work.company_id:
                raise ValidationError({"project_work": "Работа другой компании."})
            if work.kind == "SIMPLE" and self.work_item_id:
                raise ValidationError(
                    {"work_item": "Простая работа не имеет подработ."}
                )
            if work.kind == "COMPOSITE" and not self.work_item_id:
                raise ValidationError(
                    {"work_item": "Выберите подработу составной работы."}
                )
            if self.work_item_id and (
                self.work_item.project_work_id != work.pk
                or self.work_item.company_id != self.company_id
            ):
                raise ValidationError(
                    {"work_item": "Подработа другой работы или компании."}
                )
        if (
            self.deviation_reason_id
            and self.deviation_reason.company_id != self.company_id
        ):
            raise ValidationError({"deviation_reason": "Причина другой компании."})
        if self.actual_quantity is not None and self.actual_quantity < 0:
            raise ValidationError(
                {"actual_quantity": "Объём не может быть отрицательным."}
            )

    def save(self, *args, **kwargs):
        from decimal import Decimal

        # Composite value is a cost allocation; completion is calculated separately.
        factor = Decimal("1")
        self.full_clean(exclude=["actual_value"])
        if self.work_item_id and self.work_item.quantity_per_unit <= 0:
            raise ValidationError("Норматив должен быть больше нуля.")
        if self.work_item_id:
            factor = (
                self.work_item.weight
                / Decimal("100")
                / self.work_item.quantity_per_unit
            )
        self.actual_value = (
            Decimal(self.actual_quantity) * self.project_work.unit_price * factor
        ).quantize(Decimal("0.01"))
        if kwargs.get("update_fields") is not None:
            kwargs["update_fields"] = set(kwargs["update_fields"]) | {"actual_value"}
        return super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.project_work} — {self.date}: {self.actual_quantity}"


class ObjectResourceRecord(BaseCompanyModel):
    construction_object = models.ForeignKey(
        "projects.ConstructionObject",
        on_delete=models.PROTECT,
        verbose_name="Строительный объект",
        related_name="%(class)s_records",
    )

    class Meta:
        abstract = True

    def clean(self):
        super().clean()
        if (
            self.construction_object_id
            and self.company_id
            and self.construction_object.company_id != self.company_id
        ):
            raise ValidationError({"construction_object": "Объект другой компании."})
        for field in ("brigade", "equipment_type"):
            value = getattr(self, field, None)
            if value and self.company_id and value.company_id != self.company_id:
                raise ValidationError({field: "Ресурс другой компании."})
        for field in self._meta.fields:
            value = getattr(self, field.attname)
            if (
                isinstance(field, (models.DecimalField, models.IntegerField))
                and field.name not in ("id",)
                and value is not None
                and value < 0
            ):
                raise ValidationError(
                    {field.name: "Значение не может быть отрицательным."}
                )


class LaborFact(ObjectResourceRecord):
    """Факт по людским ресурсам (бригадам)."""

    date = models.DateField(_("дата"))
    brigade = models.ForeignKey(
        "resources.Brigade",
        on_delete=models.PROTECT,
        related_name="labor_facts",
        verbose_name=_("бригада"),
    )
    planned_workers = models.IntegerField(
        _("план, чел"), default=0, null=True, blank=True
    )
    actual_workers = models.IntegerField(
        _("факт, чел"), default=0, null=True, blank=True
    )
    planned_hours = models.DecimalField(
        _("план, чел-час"), max_digits=8, decimal_places=2, null=True, blank=True
    )
    actual_hours = models.DecimalField(
        _("факт, чел-час"), max_digits=8, decimal_places=2, null=True, blank=True
    )
    hourly_rate = models.DecimalField(
        _("ставка, ₽/час"), max_digits=10, decimal_places=2, null=True, blank=True
    )
    comment = models.TextField(_("комментарий"), blank=True)

    class Meta:
        unique_together = [["construction_object", "date", "brigade"]]
        verbose_name = _("факт по людям")
        verbose_name_plural = _("факты по людям")
        ordering = ["-date", "brigade"]

    def __str__(self):
        work_name = self.construction_object.name
        return f"{self.brigade} — {self.date}: {self.actual_workers} чел. ({work_name})"


class LaborPlan(ObjectResourceRecord):
    """План по людским ресурсам на день."""

    date = models.DateField(_("дата"))
    brigade = models.ForeignKey(
        "resources.Brigade",
        on_delete=models.PROTECT,
        related_name="labor_plans",
        verbose_name=_("бригада"),
    )
    planned_workers = models.IntegerField(_("план, чел"), default=0)
    planned_hours = models.DecimalField(
        _("план, чел-час"), max_digits=8, decimal_places=2, null=True, blank=True
    )
    hourly_rate = models.DecimalField(
        _("ставка, ₽/час"), max_digits=10, decimal_places=2, null=True, blank=True
    )
    comment = models.TextField(_("комментарий"), blank=True)

    class Meta:
        verbose_name = _("план по людям")
        verbose_name_plural = _("планы по людям")
        ordering = ["date", "brigade"]
        unique_together = [["construction_object", "date", "brigade"]]

    def __str__(self):
        return f"{self.brigade} — {self.date}: {self.planned_workers} чел."


class EquipmentPlan(ObjectResourceRecord):
    """План по технике на день."""

    date = models.DateField(_("дата"))
    equipment_type = models.ForeignKey(  # ← ИЗМЕНИЛИ на ForeignKey
        "resources.EquipmentType",
        on_delete=models.PROTECT,
        related_name="equipment_plans",
        verbose_name=_("вид техники"),
    )
    equipment_number = models.CharField(
        _("гос. номер / инв. №"), max_length=50, blank=True
    )
    planned_count = models.IntegerField(_("план, ед"), default=0)
    planned_machine_hours = models.DecimalField(
        _("план, маш-час"), max_digits=8, decimal_places=2, null=True, blank=True
    )
    hourly_rate = models.DecimalField(
        _("ставка, ₽/маш-час"), max_digits=10, decimal_places=2, null=True, blank=True
    )
    comment = models.TextField(_("комментарий"), blank=True)

    class Meta:
        unique_together = [
            ["construction_object", "date", "equipment_type", "equipment_number"]
        ]
        verbose_name = _("план по технике")
        verbose_name_plural = _("планы по технике")
        ordering = ["date", "equipment_type__name"]

    def __str__(self):
        work_name = self.construction_object.name
        return f"{self.equipment_type.name} — {self.date}: {self.planned_count} ед. ({work_name})"


class EquipmentFact(ObjectResourceRecord):
    """Факт по механизмам и технике."""

    date = models.DateField(_("дата"))
    equipment_type = models.ForeignKey(  # ← ИЗМЕНИЛИ на ForeignKey
        "resources.EquipmentType",
        on_delete=models.PROTECT,
        related_name="equipment_facts",
        verbose_name=_("вид техники"),
    )
    equipment_number = models.CharField(
        _("гос. номер / инв. №"),
        max_length=50,
        blank=True,
        help_text=_("Государственный регистрационный номер или инвентарный"),
    )
    planned_count = models.IntegerField(_("план, ед"), default=0)
    actual_count = models.IntegerField(_("факт, ед"), default=0)
    machine_hours = models.DecimalField(
        _("машино-часы"), max_digits=8, decimal_places=2, null=True, blank=True
    )
    hourly_rate = models.DecimalField(
        _("ставка, ₽/маш-час"), max_digits=10, decimal_places=2, null=True, blank=True
    )
    comment = models.TextField(_("комментарий"), blank=True)

    class Meta:
        unique_together = [
            ["construction_object", "date", "equipment_type", "equipment_number"]
        ]
        verbose_name = _("факт по технике")
        verbose_name_plural = _("факты по технике")
        ordering = ["-date", "equipment_type__name"]

    def __str__(self):
        work_name = self.construction_object.name
        return f"{self.equipment_type.name} — {self.date}: {self.machine_hours} м/ч ({work_name})"


class FuelFact(ObjectResourceRecord):
    """Факт по ГСМ."""

    FUEL_TYPE_CHOICES = [
        ("DIESEL", _("Дизельное топливо")),
        ("PETROL_92", _("Бензин АИ-92")),
        ("PETROL_95", _("Бензин АИ-95")),
        ("PETROL_98", _("Бензин АИ-98")),
        ("GAS", _("Газ (пропан/метан)")),
        ("OIL", _("Масло моторное")),
        ("OTHER", _("Другое")),
    ]

    date = models.DateField(_("дата"))
    fuel_type = models.CharField(
        _("вид ГСМ"), max_length=20, choices=FUEL_TYPE_CHOICES, default="DIESEL"
    )
    actual_liters = models.DecimalField(
        _("факт, л"), max_digits=10, decimal_places=2, default=0
    )
    price_per_liter = models.DecimalField(
        _("цена за литр, ₽"), max_digits=8, decimal_places=2, null=True, blank=True
    )
    equipment_ref = models.CharField(
        _("привязка к технике"), max_length=150, blank=True
    )
    comment = models.TextField(_("комментарий"), blank=True)

    class Meta:
        unique_together = [
            ["construction_object", "date", "fuel_type", "equipment_ref"]
        ]
        verbose_name = _("факт по ГСМ")
        verbose_name_plural = _("факты по ГСМ")
        ordering = ["-date", "fuel_type"]

    def __str__(self):
        work_name = self.construction_object.name
        return f"{self.get_fuel_type_display()} — {self.date}: {self.actual_liters} л ({work_name})"


class FuelPlan(ObjectResourceRecord):
    """План по ГСМ на день."""

    FUEL_TYPE_CHOICES = [
        ("DIESEL", _("Дизельное топливо")),
        ("PETROL_92", _("Бензин АИ-92")),
        ("PETROL_95", _("Бензин АИ-95")),
        ("PETROL_98", _("Бензин АИ-98")),
        ("GAS", _("Газ (пропан/метан)")),
        ("OIL", _("Масло моторное")),
        ("OTHER", _("Другое")),
    ]

    date = models.DateField(_("дата"))
    fuel_type = models.CharField(
        _("вид ГСМ"), max_length=20, choices=FUEL_TYPE_CHOICES, default="DIESEL"
    )
    planned_liters = models.DecimalField(
        _("план, л"), max_digits=10, decimal_places=2, default=0
    )
    price_per_liter = models.DecimalField(
        _("цена за литр, ₽"), max_digits=8, decimal_places=2, null=True, blank=True
    )
    equipment_ref = models.CharField(
        _("привязка к технике"), max_length=150, blank=True
    )
    comment = models.TextField(_("комментарий"), blank=True)

    class Meta:
        unique_together = [
            ["construction_object", "date", "fuel_type", "equipment_ref"]
        ]
        verbose_name = _("план по ГСМ")
        verbose_name_plural = _("планы по ГСМ")
        ordering = ["date", "fuel_type"]

    def __str__(self):
        work_name = self.construction_object.name
        return f"{self.get_fuel_type_display()} — {self.date}: {self.planned_liters} л ({work_name})"


class LegacyResourceRecord(BaseCompanyModel):
    source_model = models.CharField("Тип записи", max_length=50)
    source_pk = models.PositiveIntegerField("Исходный ID")
    payload = models.JSONField("Исходные данные", default=dict)
    reason = models.TextField("Причина ручного назначения")
    resolved = models.BooleanField("Назначен объект", default=False)
    construction_object = models.ForeignKey(
        "projects.ConstructionObject", on_delete=models.PROTECT, null=True, blank=True
    )
    restored_pk = models.PositiveIntegerField(null=True, blank=True)

    class Meta:
        unique_together = [["source_model", "source_pk"]]
        ordering = ["source_model", "source_pk"]
