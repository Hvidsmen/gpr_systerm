from django.db import models
from django.utils.translation import gettext_lazy as _

from core.enums import PlanStatus, MismatchStrategy
from core.models import BaseCompanyModel


class LoadProfile(BaseCompanyModel):
    code = models.CharField(_("код"), max_length=50)
    name = models.CharField(_("название"), max_length=255)
    description = models.TextField(_("описание"), blank=True)

    class Meta:
        verbose_name = _("профиль нагрузки")
        verbose_name_plural = _("профили нагрузки")
        ordering = ["name"]
        unique_together = [["company", "code"]]

    def __str__(self):
        return f"{self.code} - {self.name}"


class LoadProfileItem(BaseCompanyModel):
    profile = models.ForeignKey(
        LoadProfile,
        on_delete=models.CASCADE,
        related_name="items",
        verbose_name=_("профиль"),
    )
    workday_number = models.PositiveIntegerField(_("номер рабочего дня"))
    percentage = models.DecimalField(_("процент"), max_digits=5, decimal_places=2)

    class Meta:
        verbose_name = _("элемент профиля")
        verbose_name_plural = _("элементы профиля")
        ordering = ["workday_number"]
        unique_together = [["profile", "workday_number"]]

    def __str__(self):
        return f"День {self.workday_number}: {self.percentage}%"


class ProductionCalendar(BaseCompanyModel):
    code = models.CharField(_("код"), max_length=50)
    name = models.CharField(_("название"), max_length=255)
    year = models.PositiveIntegerField(_("год"))
    is_default = models.BooleanField(_("по умолчанию"), default=False)

    class Meta:
        verbose_name = _("производственный календарь")
        verbose_name_plural = _("производственные календари")
        ordering = ["-year", "name"]
        unique_together = [["company", "code", "year"]]

    def __str__(self):
        return f"{self.name} ({self.year})"


class CalendarDay(BaseCompanyModel):
    calendar = models.ForeignKey(
        ProductionCalendar,
        on_delete=models.CASCADE,
        related_name="days",
        verbose_name=_("календарь"),
    )
    date = models.DateField(_("дата"))
    is_working = models.BooleanField(_("рабочий день"), default=True)
    is_holiday = models.BooleanField(_("праздник"), default=False)
    is_shortened = models.BooleanField(_("сокращенный"), default=False)
    note = models.CharField(_("примечание"), max_length=255, blank=True)

    class Meta:
        verbose_name = _("день календаря")
        verbose_name_plural = _("дни календаря")
        ordering = ["date"]
        unique_together = [["calendar", "date"]]

    def __str__(self):
        status = "Рабочий" if self.is_working else "Выходной"
        return f"{self.date} - {status}"


class MonthlyPlan(BaseCompanyModel):
    project_work = models.ForeignKey(
        "works.ProjectWork",
        on_delete=models.CASCADE,
        related_name="monthly_plans",
        verbose_name=_("работа"),
    )
    year = models.PositiveIntegerField(_("год"))
    month = models.PositiveIntegerField(_("месяц"))
    planned_quantity = models.DecimalField(
        _("плановый объем"), max_digits=15, decimal_places=3
    )
    planned_value = models.DecimalField(
        _("плановая стоимость"), max_digits=15, decimal_places=2, default=0
    )
    start_date = models.DateField(_("дата начала"))
    end_date = models.DateField(_("дата окончания"))
    mismatch_strategy = models.CharField(
        _("стратегия при несовпадении дней"),
        max_length=20,
        choices=MismatchStrategy.choices,
        default=MismatchStrategy.STRICT,
    )

    class Meta:
        verbose_name = _("месячный план")
        verbose_name_plural = _("месячные планы")
        ordering = ["-year", "-month"]
        unique_together = [["project_work", "year", "month"]]

    def clean(self):
        from django.core.exceptions import ValidationError

        if self.start_date and self.end_date and self.start_date > self.end_date:
            raise ValidationError({"end_date": "Конец периода раньше начала."})
        if self.planned_quantity is not None and self.planned_quantity < 0:
            raise ValidationError(
                {"planned_quantity": "Объём не может быть отрицательным."}
            )
        if (
            self.project_work_id
            and self.company_id
            and self.project_work.company_id != self.company_id
        ):
            raise ValidationError({"project_work": "Работа другой компании."})

    def __str__(self):
        return f"{self.project_work.name} - {self.month:02d}.{self.year}"


class PlanVersion(BaseCompanyModel):
    monthly_plan = models.ForeignKey(
        MonthlyPlan,
        on_delete=models.CASCADE,
        related_name="versions",
        verbose_name=_("месячный план"),
    )
    version_number = models.PositiveIntegerField(_("номер версии"))
    status = models.CharField(
        _("статус"), max_length=20, choices=PlanStatus.choices, default=PlanStatus.DRAFT
    )
    is_baseline = models.BooleanField(_("базовый план"), default=False)
    created_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        null=True,
        related_name="created_plan_versions",
        verbose_name=_("создано"),
    )
    approved_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="approved_plan_versions",
        verbose_name=_("утверждено"),
    )
    approved_at = models.DateTimeField(_("дата утверждения"), null=True, blank=True)
    comment = models.TextField(_("комментарий"), blank=True)

    class Meta:
        verbose_name = _("версия плана")
        verbose_name_plural = _("версии планов")
        ordering = ["-version_number"]
        unique_together = [["monthly_plan", "version_number"]]

    def __str__(self):
        return (
            f"{self.monthly_plan} v{self.version_number} ({self.get_status_display()})"
        )

    @property
    def is_immutable(self):
        return self.status in [PlanStatus.APPROVED, PlanStatus.COMPLETED]


class DailyPlan(BaseCompanyModel):
    plan_version = models.ForeignKey(
        PlanVersion,
        on_delete=models.CASCADE,
        related_name="daily_plans",
        verbose_name=_("версия плана"),
    )
    work_item = models.ForeignKey(
        "works.ProjectWorkItem",
        on_delete=models.CASCADE,
        related_name="daily_plans",
        null=True,
        blank=True,
        verbose_name=_("элемент работы"),
    )

    @property
    def project_work(self):
        return self.plan_version.monthly_plan.project_work

    date = models.DateField(_("дата"))
    workday_number = models.PositiveIntegerField(_("номер рабочего дня"))
    planned_quantity = models.DecimalField(
        _("плановый объем"), max_digits=15, decimal_places=3
    )
    planned_value = models.DecimalField(
        _("плановая стоимость"), max_digits=15, decimal_places=2, default=0
    )

    class Meta:
        verbose_name = _("дневной план")
        verbose_name_plural = _("дневные планы")
        ordering = ["date"]
        constraints = [
            models.UniqueConstraint(
                fields=["plan_version", "work_item", "date"],
                condition=models.Q(work_item__isnull=False),
                name="unique_item_daily_plan",
            ),
            models.UniqueConstraint(
                fields=["plan_version", "date"],
                condition=models.Q(work_item__isnull=True),
                name="unique_simple_daily_plan",
            ),
        ]

    def clean(self):
        from django.core.exceptions import ValidationError

        work = self.project_work
        if (
            self.company_id != work.company_id
            or self.plan_version.company_id != self.company_id
        ):
            raise ValidationError("План и работа должны принадлежать одной компании.")
        if (
            work.kind == "SIMPLE"
            and self.work_item_id
            or work.kind == "COMPOSITE"
            and not self.work_item_id
        ):
            raise ValidationError("Подработа не соответствует виду работы.")
        if self.work_item_id and self.work_item.project_work_id != work.pk:
            raise ValidationError("Подработа другой работы.")
        if self.planned_quantity < 0 or self.planned_value < 0:
            raise ValidationError("План не может быть отрицательным.")

    def __str__(self):
        return f"{self.date} - {self.planned_quantity}"


class DailyBaseline(BaseCompanyModel):
    project_work = models.ForeignKey(
        "works.ProjectWork",
        on_delete=models.CASCADE,
        related_name="baselines",
        verbose_name=_("работа"),
    )
    work_item = models.ForeignKey(
        "works.ProjectWorkItem",
        on_delete=models.CASCADE,
        related_name="baselines",
        null=True,
        blank=True,
        verbose_name=_("элемент работы"),
    )
    date = models.DateField(_("дата"))
    baseline_quantity = models.DecimalField(
        _("базовый объем"), max_digits=15, decimal_places=3
    )
    baseline_value = models.DecimalField(
        _("базовая стоимость"), max_digits=15, decimal_places=2, default=0
    )
    source_version = models.ForeignKey(
        PlanVersion,
        on_delete=models.SET_NULL,
        null=True,
        related_name="baseline_records",
        verbose_name=_("источник"),
    )

    class Meta:
        verbose_name = _("базовый план")
        verbose_name_plural = _("базовые планы")
        ordering = ["date"]
        unique_together = [["project_work", "work_item", "date"]]

    def __str__(self):
        return f"{self.date} - {self.baseline_quantity}"


class GlobalPlanVersion(BaseCompanyModel):
    construction_object = models.ForeignKey(
        "projects.ConstructionObject",
        on_delete=models.PROTECT,
        verbose_name="Строительный объект",
    )
    start_date = models.DateField("Начало периода")
    end_date = models.DateField("Конец периода")
    version_number = models.PositiveIntegerField("Номер версии")
    title = models.CharField("Название", max_length=255, blank=True)
    status = models.CharField(
        "Статус", max_length=20, choices=PlanStatus.choices, default=PlanStatus.DRAFT
    )
    source_versions = models.ManyToManyField(
        PlanVersion, blank=True, related_name="global_versions"
    )
    snapshot = models.JSONField("Зафиксированный состав", default=dict, editable=False)
    created_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        null=True,
        related_name="created_global_versions",
    )
    approved_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="approved_global_versions",
    )
    approved_at = models.DateTimeField(null=True, blank=True)
    comment = models.TextField("Комментарий", blank=True)
    previous_version = models.ForeignKey(
        "self",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="revisions",
    )

    class Meta:
        ordering = ["-created_at"]
        unique_together = [["construction_object", "version_number"]]
        verbose_name = "Глобальная версия объекта"
        verbose_name_plural = "Глобальные версии объектов"

    @property
    def is_immutable(self):
        return self.status in [PlanStatus.APPROVED, PlanStatus.COMPLETED]

    def clean(self):
        from django.core.exceptions import ValidationError

        if self.start_date and self.end_date and self.start_date > self.end_date:
            raise ValidationError(
                {"end_date": "Конец периода не может быть раньше начала."}
            )
        if (
            self.company_id
            and self.construction_object_id
            and self.construction_object.company_id != self.company_id
        ):
            raise ValidationError({"construction_object": "Объект другой компании."})

    def save(self, *args, **kwargs):
        from django.core.exceptions import ValidationError

        if self.pk:
            old = type(self).objects.get(pk=self.pk)
            allowed = {
                "DRAFT": ["DRAFT", "SUBMITTED"],
                "REJECTED": ["REJECTED", "SUBMITTED"],
                "SUBMITTED": ["SUBMITTED", "APPROVED", "REJECTED"],
                "APPROVED": ["APPROVED", "COMPLETED"],
                "COMPLETED": ["COMPLETED"],
            }
            if self.status not in allowed[old.status]:
                raise ValidationError("Недопустимый переход статуса глобальной версии.")
            if old.is_immutable or old.status == PlanStatus.SUBMITTED:
                protected = (
                    "construction_object_id",
                    "company_id",
                    "start_date",
                    "end_date",
                    "version_number",
                    "snapshot",
                    "title",
                    "previous_version_id",
                )
                if any(getattr(old, key) != getattr(self, key) for key in protected):
                    raise ValidationError(
                        "Состав отправленной или утверждённой глобальной версии менять нельзя."
                    )
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.construction_object} — глобальная v{self.version_number} ({self.get_status_display()})"
