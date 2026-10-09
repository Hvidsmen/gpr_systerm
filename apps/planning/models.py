from django.db import models
from django.utils.translation import gettext_lazy as _

from core.enums import PlanStatus, MismatchStrategy
from core.models import BaseCompanyModel


class LoadProfile(BaseCompanyModel):
    code = models.CharField(_("код"), max_length=50, blank=True, editable=False)
    name = models.CharField(_("название"), max_length=255)
    description = models.TextField(_("описание"), blank=True)

    class Meta:
        verbose_name = _("профиль нагрузки")
        verbose_name_plural = _("профили нагрузки")
        ordering = ["name"]
        unique_together = [["company", "code"]]

    def __str__(self):
        return self.name


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
    code = models.CharField(_("код"), max_length=50, blank=True, editable=False)
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
    workspace = models.ForeignKey(
        "PlanningWorkspace",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="versions",
        verbose_name="Рабочее пространство",
    )
    version_kind = models.CharField(
        "Назначение",
        max_length=12,
        choices=[
            ("LEGACY", "Сводная версия"),
            ("BASELINE", "Базовый план периода"),
            ("FORECAST", "Месячное уточнение периода"),
        ],
        default="LEGACY",
    )
    planning_month = models.DateField("Планируемый месяц", null=True, blank=True)
    scenario = models.CharField(
        "Сценарий",
        max_length=12,
        choices=[
            ("REMAINING", "Факт и распределение остатка"),
            ("BASELINE", "Сохранение базового плана"),
        ],
        blank=True,
    )

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
    approval_round = models.PositiveIntegerField("Раунд согласования", default=0, editable=False)
    baseline_review = models.ForeignKey("GlobalPlanReview", on_delete=models.PROTECT, null=True, blank=True, editable=False, related_name="dependent_versions", verbose_name="Зафиксированная база")
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

    def get_status_display(self):
        return "На доработке" if self.status == "REJECTED" else dict(PlanStatus.choices).get(self.status, self.status)

    @property
    def can_delete(self):
        from .deletion import version_reason
        return not version_reason(self)

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

        if self.workspace_id:
            workspace = self.workspace
            if (
                self.company_id,
                self.construction_object_id,
                self.start_date,
                self.end_date,
            ) != (
                workspace.company_id,
                workspace.construction_object_id,
                workspace.start_date,
                workspace.end_date,
            ):
                raise ValidationError(
                    "Версия должна совпадать с объектом и периодом рабочего пространства."
                )
            if self.version_kind == "BASELINE" and (
                self.planning_month or self.scenario
            ):
                raise ValidationError(
                    "Базовая версия не имеет планируемого месяца или сценария."
                )
            if self.version_kind == "FORECAST":
                from .workspace_services import months_between

                if (
                    not self.planning_month
                    or self.planning_month.day != 1
                    or self.planning_month
                    not in months_between(self.start_date, self.end_date)
                    or self.scenario not in ["REMAINING", "BASELINE"]
                ):
                    raise ValidationError("Укажите месяц периода и сценарий уточнения.")
            if self.baseline_review_id and (self.version_kind != "FORECAST" or self.baseline_review.version_id != workspace.baseline_version_id or not self.baseline_review.was_approved):
                raise ValidationError({"baseline_review": "Используйте согласованный раунд базы этого рабочего пространства."})
        elif self.version_kind != "LEGACY":
            raise ValidationError(
                "Базовый план и уточнение требуют рабочего пространства."
            )

    def save(self, *args, **kwargs):
        from django.core.exceptions import ValidationError

        if self.pk:
            old = type(self).objects.get(pk=self.pk)
            from .approval_context import transition_authorized
            metadata = ("status", "approved_by_id", "approved_at", "approval_round")
            if any(getattr(old,key)!=getattr(self,key) for key in metadata) and not transition_authorized(self.pk):
                raise ValidationError("Статус и согласование изменяются только через маршрут согласования.")
            if old.status == PlanStatus.COMPLETED and any(getattr(old,field.attname)!=getattr(self,field.attname) for field in self._meta.fields if field.name != "updated_at"):
                raise ValidationError("Завершённый план нельзя изменять.")
            allowed = {
                "DRAFT": ["DRAFT", "SUBMITTED"],
                "REJECTED": ["REJECTED", "SUBMITTED"],
                "SUBMITTED": ["SUBMITTED", "APPROVED", "REJECTED"],
                "APPROVED": ["APPROVED", "COMPLETED", "REJECTED"],
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
                    "workspace_id",
                    "version_kind",
                    "planning_month",
                    "scenario",
                    "baseline_review_id",
                )
                if any(getattr(old, key) != getattr(self, key) for key in protected):
                    raise ValidationError(
                        "Состав отправленной или утверждённой глобальной версии менять нельзя."
                    )
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.construction_object} — глобальная v{self.version_number} ({self.get_status_display()})"


class PlanningWorkspace(BaseCompanyModel):
    name = models.CharField("Название плана", max_length=255)
    construction_object = models.ForeignKey(
        "projects.ConstructionObject",
        on_delete=models.PROTECT,
        verbose_name="Строительный объект",
    )
    start_date = models.DateField("Начало периода")
    end_date = models.DateField("Конец периода")
    baseline_version = models.OneToOneField(
        GlobalPlanVersion,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="baseline_workspace",
        verbose_name="Базовая версия",
    )

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Рабочее пространство планирования"
        verbose_name_plural = "Рабочие пространства планирования"

    @property
    def can_delete(self):
        from .deletion import workspace_reason
        return not workspace_reason(self)

    def clean(self):
        from django.core.exceptions import ValidationError

        if self.start_date and self.end_date and self.start_date > self.end_date:
            raise ValidationError({"end_date": "Конец периода раньше начала."})
        if (
            self.company_id
            and self.construction_object_id
            and self.construction_object.company_id != self.company_id
        ):
            raise ValidationError({"construction_object": "Объект другой компании."})
        if self.baseline_version_id and (
            self.baseline_version.workspace_id != self.pk
            or self.baseline_version.version_kind != "BASELINE"
        ):
            raise ValidationError("Базовая версия другого пространства.")

    def save(self, *args, **kwargs):
        from django.core.exceptions import ValidationError

        if self.pk:
            old = type(self).objects.select_related("baseline_version").get(pk=self.pk)
            if old.baseline_version and old.baseline_version.status not in [
                "DRAFT",
                "REJECTED",
            ]:
                for name in [
                    "company_id",
                    "construction_object_id",
                    "start_date",
                    "end_date",
                    "baseline_version_id",
                ]:
                    if getattr(old, name) != getattr(self, name):
                        raise ValidationError(
                            "Объект, период и базу зафиксированного рабочего пространства менять нельзя."
                        )
        self.full_clean()
        return super().save(*args, **kwargs)

    def __str__(self):
        return self.name


class WorkMonthAllocation(BaseCompanyModel):
    version = models.ForeignKey(
        GlobalPlanVersion, on_delete=models.CASCADE, related_name="work_allocations"
    )
    work = models.ForeignKey(
        "works.ProjectWork", on_delete=models.PROTECT, verbose_name="Работа"
    )
    month = models.DateField("Месяц", help_text="Первое число месяца")
    quantity = models.DecimalField(
        "Объём основной работы", max_digits=15, decimal_places=3, default=0
    )
    item_quantities = models.JSONField("Месячные объёмы подработ", default=dict, blank=True)
    daily_override = models.JSONField("Сохранённый дневной план объединения", null=True, blank=True, default=None)
    load_profile = models.ForeignKey(
        LoadProfile,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        verbose_name="Профиль на месяц",
        help_text="Пустой выбор использует профили работы и подработ.",
    )

    class Meta:
        ordering = ["month", "work__code", "pk"]
        constraints = [
            models.UniqueConstraint(
                fields=["version", "work", "month"], name="unique_workspace_work_month"
            )
        ]

    def clean(self):
        validate_workspace_allocation(self)
        from django.core.exceptions import ValidationError

        if self.work_id and (
            self.work.company_id != self.company_id
            or self.work.section.construction_object_id
            != self.version.construction_object_id
        ):
            raise ValidationError(
                {"work": "Выберите работу этого строительного объекта."}
            )
        if self.quantity is not None and self.quantity < 0:
            raise ValidationError({"quantity": "Объём не может быть отрицательным."})
        if self.load_profile_id and self.load_profile.company_id != self.company_id:
            raise ValidationError({"load_profile": "Профиль другой компании."})

        if not isinstance(self.item_quantities, dict):
            raise ValidationError({"item_quantities": "Объёмы подработ должны быть словарём."})
        if self.item_quantities:
            from decimal import Decimal, InvalidOperation
            from apps.works.progress import work_specification, quantity_from_totals
            if self.work.kind != "COMPOSITE" or not isinstance(self.item_quantities, dict):
                raise ValidationError({"item_quantities": "Отдельные объёмы допустимы только для составной работы."})
            items = list(self.work.items.filter(company=self.company))
            if set(self.item_quantities) != {str(item.pk) for item in items}:
                raise ValidationError({"item_quantities": "Укажите объёмы всех подработ этой работы."})
            if self.version.version_kind == "FORECAST":
                baseline = self.version.workspace.baseline_version
                base_snapshot = self.version.baseline_review.snapshot if self.version.baseline_review_id else (baseline.snapshot if baseline else {})
                frozen = next((spec for spec in base_snapshot.get("works", []) if spec["id"] == self.work_id), None) if baseline else None
                if frozen and {item.pk: item.quantity_per_unit for item in items} != {item["id"]: Decimal(item["norm"]) for item in frozen["items"]}:
                    raise ValidationError({"item_quantities": "Состав подработ или нормативы отличаются от утверждённой базы. Создайте новый базовый план."})
            try:
                values = {int(key): Decimal(str(value)) for key, value in self.item_quantities.items()}
                if any(not value.is_finite() or value < 0 or value > Decimal("999999999999.999999") or value != value.quantize(Decimal(".000001")) for value in values.values()):
                    raise ValueError
            except (InvalidOperation, ValueError, TypeError):
                raise ValidationError({"item_quantities": "Объёмы подработ должны быть неотрицательными числами, до 6 знаков после запятой."})
            self.quantity = quantity_from_totals(work_specification(self.work), values)
            self._meta.get_field("quantity").clean(self.quantity, self)

        if self.daily_override is not None and self.work_id and self.month and self.version_id:
            from decimal import Decimal, InvalidOperation
            from datetime import date
            try:
                allowed=set(self.work.items.values_list('pk',flat=True)) if self.work.kind == 'COMPOSITE' else {None}
                if not isinstance(self.daily_override,list):
                    raise ValueError
                for row in self.daily_override:
                    day=date.fromisoformat(row['date'])
                    value=Decimal(str(row['quantity']))
                    if row['item_id'] not in allowed or day.replace(day=1)!=self.month or day < self.version.start_date or day > self.version.end_date or not value.is_finite() or value<0:
                        raise ValueError
            except (KeyError,TypeError,ValueError,InvalidOperation):
                raise ValidationError({'daily_override':'Некорректный сохранённый дневной план.'})

    def save(self, *args, **kwargs):
        from django.core.exceptions import ValidationError

        if self.pk:
            old = type(self).objects.select_related("version").get(pk=self.pk)
            if old.version.status not in ["DRAFT", "REJECTED"]:
                raise ValidationError(
                    "Месячные данные отправленной или утверждённой версии менять нельзя."
                )
        if self.pk and self.daily_override is not None:
            previous = type(self).objects.get(pk=self.pk)
            if (previous.quantity, previous.load_profile_id, previous.item_quantities) != (self.quantity, self.load_profile_id, self.item_quantities):
                self.daily_override = None
                if kwargs.get('update_fields') is not None:
                    kwargs['update_fields']=set(kwargs['update_fields']) | {'daily_override'}
        self.full_clean()
        return super().save(*args, **kwargs)


class ResourceMonthAllocation(BaseCompanyModel):
    class Kind(models.TextChoices):
        LABOR = "labor", "Люди"
        EQUIPMENT = "equipment", "Техника"
        FUEL = "fuel", "ГСМ"

    version = models.ForeignKey(
        GlobalPlanVersion, on_delete=models.CASCADE, related_name="resource_allocations"
    )
    load_profile = models.ForeignKey(
        LoadProfile,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        verbose_name="Профиль ресурса на месяц",
    )
    month = models.DateField("Месяц", help_text="Первое число месяца")
    kind = models.CharField("Ресурс", max_length=12, choices=Kind.choices)
    brigade = models.ForeignKey(
        "resources.Brigade",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        verbose_name="Бригада",
    )
    equipment_type = models.ForeignKey(
        "resources.EquipmentType",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        verbose_name="Тип техники",
    )
    equipment_number = models.CharField("Номер машины", max_length=50, blank=True)
    fuel_type = models.CharField(
        "Вид ГСМ",
        max_length=20,
        choices=[
            ("DIESEL", "Дизельное топливо"),
            ("PETROL_92", "Бензин АИ-92"),
            ("PETROL_95", "Бензин АИ-95"),
            ("PETROL_98", "Бензин АИ-98"),
            ("GAS", "Газ"),
            ("OIL", "Масло моторное"),
            ("OTHER", "Другое"),
        ],
        blank=True,
    )
    equipment_ref = models.CharField("Техника для ГСМ", max_length=150, blank=True, editable=False)
    count = models.PositiveIntegerField(
        "Численность / количество на рабочий день", default=0
    )
    hours = models.DecimalField(
        "Часы за месяц", max_digits=15, decimal_places=2, default=0
    )
    liters = models.DecimalField(
        "Расход за месяц, л", max_digits=15, decimal_places=2, default=0
    )
    balance = models.DecimalField("Остаток на каждый день, л", max_digits=15, decimal_places=2, default=0, blank=True)
    rate = models.DecimalField(
        "Ставка за час / цена литра", max_digits=12, decimal_places=2, default=0
    )

    class Meta:
        ordering = ["month", "kind", "pk"]
        constraints = [
            models.UniqueConstraint(
                fields=["version", "month", "kind", "brigade"],
                condition=models.Q(kind="labor"),
                name="unique_workspace_labor_month",
            ),
            models.UniqueConstraint(
                fields=[
                    "version",
                    "month",
                    "kind",
                    "equipment_type",
                    "equipment_number",
                ],
                condition=models.Q(kind="equipment"),
                name="unique_workspace_equipment_month",
            ),
            models.UniqueConstraint(
                fields=["version", "month", "kind", "fuel_type", "equipment_ref"],
                condition=models.Q(kind="fuel"),
                name="unique_workspace_fuel_month",
            ),
        ]

    def clean(self):
        validate_workspace_allocation(self)
        from django.core.exceptions import ValidationError

        errors = {}
        for name in ("count", "hours", "liters", "rate", "balance"):
            value = getattr(self, name)
            if value is not None and value < 0:
                errors[name] = "Значение не может быть отрицательным."
        if self.kind == "labor":
            if not self.brigade_id:
                errors["brigade"] = "Выберите бригаду."
            if (
                self.equipment_type_id
                or self.fuel_type
                or self.equipment_number
                or self.equipment_ref
                or self.liters
                or self.balance
            ):
                errors["kind"] = (
                    "Для людей заполняются только бригада, численность, часы и ставка."
                )
        elif self.kind == "equipment":
            if not self.equipment_type_id:
                errors["equipment_type"] = "Выберите тип техники."
            if self.brigade_id or self.fuel_type or self.equipment_ref or self.liters or self.balance:
                errors["kind"] = (
                    "Для техники заполняются тип, номер, количество, часы и ставка."
                )
        elif self.kind == "fuel":
            if not self.fuel_type:
                errors["fuel_type"] = "Выберите вид ГСМ."
            if (
                self.brigade_id
                or self.equipment_type_id
                or self.equipment_number
                or self.hours
                or self.count
            ):
                errors["kind"] = (
                    "Для ГСМ заполняются вид, обозначение техники, литры и цена."
                )
        for name in ("brigade", "equipment_type", "load_profile"):
            if (
                getattr(self, name + "_id")
                and getattr(self, name).company_id != self.company_id
            ):
                errors[name] = "Ресурс другой компании."
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        from django.core.exceptions import ValidationError

        if self.pk:
            old = type(self).objects.select_related("version").get(pk=self.pk)
            if old.version.status not in ["DRAFT", "REJECTED"]:
                raise ValidationError(
                    "Месячные данные отправленной или утверждённой версии менять нельзя."
                )
        self.full_clean()
        return super().save(*args, **kwargs)


def validate_workspace_allocation(row):
    from calendar import monthrange
    from datetime import date
    from django.core.exceptions import ValidationError

    if not row.version_id:
        return
    version = row.version
    if not version.workspace_id or version.company_id != row.company_id:
        raise ValidationError("Версия другого рабочего пространства или компании.")
    if GlobalPlanVersion.objects.get(pk=version.pk).status not in ["DRAFT", "REJECTED"]:
        raise ValidationError("Отправленную или утверждённую версию менять нельзя.")
    if row.month:
        if row.month.day != 1:
            raise ValidationError({"month": "Укажите первое число месяца."})
        last = date(
            row.month.year,
            row.month.month,
            monthrange(row.month.year, row.month.month)[1],
        )
        if row.month > version.end_date or last < version.start_date:
            raise ValidationError({"month": "Месяц за пределами периода плана."})
        if version.planning_month and row.month < version.planning_month:
            raise ValidationError(
                {"month": "Прошлые месяцы формируются автоматически."}
            )


class GlobalPlanReview(BaseCompanyModel):
    version = models.ForeignKey(GlobalPlanVersion, on_delete=models.CASCADE, related_name="review_rounds")
    number = models.PositiveIntegerField("Раунд")
    snapshot = models.JSONField("Согласуемый состав", default=dict, editable=False)
    submitted_by = models.ForeignKey("accounts.User", on_delete=models.SET_NULL, null=True, related_name="submitted_plan_reviews")
    legacy_approved = models.BooleanField("Историческое утверждение", default=False, editable=False)

    class Meta:
        ordering = ["number"]
        constraints = [models.UniqueConstraint(fields=["version", "number"], name="unique_global_review_round")]
        verbose_name = "Раунд согласования плана"
        verbose_name_plural = "Раунды согласования планов"

    @property
    def was_approved(self):
        return self.legacy_approved or self.decisions.filter(section="CEO", action="APPROVE").exists()

    def clean(self):
        if self.version_id and self.version.company_id != self.company_id:
            from django.core.exceptions import ValidationError
            raise ValidationError("Раунд другой компании.")

    def save(self, *args, **kwargs):
        from django.core.exceptions import ValidationError
        if self.pk:
            raise ValidationError("Историю раунда согласования изменять нельзя.")
        self.full_clean()
        return super().save(*args, **kwargs)


class GlobalPlanDecision(BaseCompanyModel):
    review = models.ForeignKey(GlobalPlanReview, on_delete=models.CASCADE, related_name="decisions")
    section = models.CharField("Раздел", max_length=20, choices=[("PRODUCTION","Работы"),("HR","Люди"),("TECH","Техника и ГСМ"),("CEO","Генеральный директор"),("PLANNER","Подготовка")])
    action = models.CharField("Решение", max_length=20, choices=[("SUBMIT","Отправлен"),("APPROVE","Согласовано"),("REJECT","Возвращён на доработку"),("COMPLETE","Завершён")])
    actor = models.ForeignKey("accounts.User", on_delete=models.SET_NULL, null=True, related_name="global_plan_decisions")
    actor_name = models.CharField("Пользователь", max_length=300)
    actor_role = models.CharField("Роль на момент решения", max_length=50)
    comment = models.TextField("Комментарий", blank=True, max_length=4000)

    class Meta:
        ordering = ["created_at", "pk"]
        constraints = [models.UniqueConstraint(fields=["review", "section"], condition=models.Q(action="APPROVE"), name="unique_section_review_approval")]
        verbose_name = "Решение по плану"
        verbose_name_plural = "Решения по планам"

    def clean(self):
        from django.core.exceptions import ValidationError
        if self.review_id and self.review.company_id != self.company_id:
            raise ValidationError("Решение другого раунда или компании.")
        if self.actor_id and self.actor.company_id != self.company_id:
            raise ValidationError("Пользователь другой компании.")

    def save(self, *args, **kwargs):
        from django.core.exceptions import ValidationError
        if self.pk:
            raise ValidationError("Решение в истории изменять нельзя.")
        self.full_clean()
        return super().save(*args, **kwargs)


class ProjectPlanVersion(BaseCompanyModel):
    """A project-wide selection of approved object plans, frozen without approval."""

    project = models.ForeignKey(
        "projects.Project",
        on_delete=models.PROTECT,
        related_name="consolidated_plans",
        verbose_name="Проект",
    )
    title = models.CharField("Название", max_length=255)
    version_number = models.PositiveIntegerField("Номер версии")
    start_date = models.DateField("Начало периода")
    end_date = models.DateField("Конец периода")
    status = models.CharField(
        "Статус",
        max_length=10,
        choices=[("DRAFT", "Черновик"), ("FIXED", "Зафиксирована")],
        default="DRAFT",
    )
    previous_version = models.ForeignKey(
        "self",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="revisions",
        verbose_name="Предыдущая сводная версия",
    )
    created_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_project_plans",
    )
    fixed_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="fixed_project_plans",
    )
    fixed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at", "-pk"]
        constraints = [
            models.UniqueConstraint(
                fields=["project", "version_number"], name="unique_project_plan_version"
            )
        ]
        verbose_name = "Сводная версия плана проекта"
        verbose_name_plural = "Сводные версии планов проектов"

    def __str__(self):
        return f"{self.project.name} · {self.title} · v{self.version_number} ({self.get_status_display()})"

    def clean(self):
        from django.core.exceptions import ValidationError

        if self.project_id and self.project.company_id != self.company_id:
            raise ValidationError({"project": "Проект другой компании."})
        if self.start_date and self.end_date:
            from .workspace_services import months_between

            if self.start_date > self.end_date:
                raise ValidationError({"end_date": "Конец периода раньше начала."})
            if len(months_between(self.start_date, self.end_date)) > 120:
                raise ValidationError(
                    {"end_date": "Период не должен превышать 120 месяцев."}
                )
        if self.previous_version_id and (
            self.previous_version.project_id != self.project_id
            or self.previous_version.company_id != self.company_id
        ):
            raise ValidationError("Исходная сводная версия другого проекта.")
        if self.pk:
            for member in self.members.select_related("version", "construction_object"):
                if member.construction_object.project_id != self.project_id:
                    raise ValidationError("В составе есть объект другого проекта.")
                if (
                    member.version.start_date > self.start_date
                    or member.version.end_date < self.end_date
                ):
                    raise ValidationError(
                        "Планы объектов должны покрывать весь период сводной версии."
                    )

    def save(self, *args, **kwargs):
        from django.core.exceptions import ValidationError
        from .project_plan_services import fixation_authorized

        if self.pk:
            old = type(self).objects.get(pk=self.pk)
            reopening = (
                fixation_authorized(self.pk) and self.status == "DRAFT"
                and self.fixed_by_id is None and self.fixed_at is None
                and all(
                    getattr(old, f.attname) == getattr(self, f.attname)
                    for f in self._meta.fields
                    if f.name not in {"status", "fixed_by", "fixed_at", "updated_at"}
                )
            )
            if old.status == "FIXED" and not reopening and any(
                getattr(old, f.attname) != getattr(self, f.attname)
                for f in self._meta.fields
                if f.name != "updated_at"
            ):
                raise ValidationError("Зафиксированную сводную версию изменять нельзя.")
            if any(
                getattr(old, f) != getattr(self, f)
                for f in ["status", "fixed_by_id", "fixed_at"]
            ) and not fixation_authorized(self.pk):
                raise ValidationError(
                    "Зафиксируйте состав через действие «Зафиксировать состав»."
                )
        elif self.status != "DRAFT":
            raise ValidationError("Новая сводная версия должна быть черновиком.")
        self.full_clean()
        return super().save(*args, **kwargs)


class ProjectPlanMember(BaseCompanyModel):
    consolidated_version = models.ForeignKey(
        ProjectPlanVersion, on_delete=models.CASCADE, related_name="members"
    )
    construction_object = models.ForeignKey(
        "projects.ConstructionObject",
        on_delete=models.PROTECT,
        related_name="project_plan_members",
    )
    version = models.ForeignKey(
        GlobalPlanVersion,
        on_delete=models.PROTECT,
        related_name="project_plan_members",
        verbose_name="Версия плана объекта",
    )
    review = models.ForeignKey(
        GlobalPlanReview,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        editable=False,
        related_name="project_plan_members",
    )
    snapshot = models.JSONField(default=dict, blank=True, editable=False)

    class Meta:
        ordering = ["construction_object__name", "pk"]
        constraints = [
            models.UniqueConstraint(
                fields=["consolidated_version", "construction_object"],
                name="unique_object_in_project_plan",
            )
        ]
        verbose_name = "Объект сводной версии проекта"
        verbose_name_plural = "Объекты сводной версии проекта"

    def clean(self):
        from django.core.exceptions import ValidationError

        if (
            not self.consolidated_version_id
            or not self.version_id
            or not self.construction_object_id
        ):
            return
        parent = self.consolidated_version
        version = self.version
        if any(
            row.company_id != self.company_id
            for row in [parent, version, self.construction_object]
        ):
            raise ValidationError("План или объект другой компании.")
        if self.construction_object.project_id != parent.project_id:
            raise ValidationError(
                "Строительный объект не принадлежит выбранному проекту."
            )
        if version.construction_object_id != self.construction_object_id:
            raise ValidationError("Версия плана относится к другому объекту.")
        if not version.is_immutable:
            raise ValidationError(
                "Включать можно только полностью согласованные планы объектов."
            )
        if version.start_date > parent.start_date or version.end_date < parent.end_date:
            raise ValidationError(
                "План объекта должен покрывать весь период сводной версии."
            )
        if self.review_id and (
            self.review.version_id != self.version_id or not self.review.was_approved
        ):
            raise ValidationError(
                "Снимок не соответствует утверждённому раунду плана объекта."
            )

    def save(self, *args, **kwargs):
        from django.core.exceptions import ValidationError
        from .project_plan_services import fixation_authorized

        if (
            type(self.consolidated_version)
            .objects.get(pk=self.consolidated_version_id)
            .status
            == "FIXED"
        ):
            raise ValidationError(
                "Состав зафиксированной сводной версии изменять нельзя."
            )
        if self.pk:
            old = type(self).objects.get(pk=self.pk)
            if (
                old.consolidated_version_id != self.consolidated_version_id
                or old.construction_object_id != self.construction_object_id
            ):
                raise ValidationError(
                    "Объект и сводную версию строки состава менять нельзя."
                )
        if (self.review_id or self.snapshot) and not fixation_authorized(
            self.consolidated_version_id
        ):
            raise ValidationError("Снимки заполняются при фиксации состава.")
        self.full_clean()
        return super().save(*args, **kwargs)
