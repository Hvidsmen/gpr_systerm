import uuid
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
            ("ENTERED", "Факт и только введенные объемы, без переноса остатков"),
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
                    or self.scenario not in ["REMAINING", "BASELINE", "ENTERED"]
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
            old = type(self).objects.defer("snapshot").get(pk=self.pk)
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
    is_archived = models.BooleanField('В архиве', default=False, db_index=True)
    archived_at = models.DateTimeField('Дата архивирования', null=True, blank=True)
    archived_by = models.ForeignKey('accounts.User', on_delete=models.SET_NULL, null=True, blank=True, related_name='archived_plan_workspaces')
    is_macro_consolidation = models.BooleanField(default=False)
    continued_from_review = models.ForeignKey('GlobalPlanReview', on_delete=models.PROTECT, null=True, blank=True, related_name='continued_workspaces', verbose_name='Предыдущий утвержденный ГПР')
    continuation_start = models.DateField(null=True, blank=True, verbose_name='Начало нового периода')
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

    parent_gpr = models.ForeignKey(GlobalPlanVersion, on_delete=models.PROTECT, null=True, blank=True,
                                   related_name='subobject_smg_workspaces', verbose_name='Версия ГПР макрообъекта')

    class Meta:
        constraints = [models.UniqueConstraint(fields=['parent_gpr', 'construction_object', 'start_date'], name='unique_gpr_subobject_smg_month')]
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
        if not self.is_macro_consolidation and bool(self.continued_from_review_id) != bool(self.continuation_start):
            raise ValidationError('Укажите исходный согласованный план и начало продолжения.')
        if self.is_macro_consolidation:
            from calendar import monthrange
            if self.continued_from_review_id or self.parent_gpr_id or not self.continuation_start or self.continuation_start.day != 1 or not self.start_date or not self.end_date or not self.start_date < self.continuation_start <= self.end_date or self.end_date.day != monthrange(self.end_date.year,self.end_date.month)[1]:
                raise ValidationError('Укажите исторический период и новые полные месяцы ГПР макрообъекта.')
        if self.continued_from_review_id:
            source = self.continued_from_review.version
            if source.company_id != self.company_id or source.construction_object_id != self.construction_object_id or not self.continued_from_review.was_approved:
                raise ValidationError('Выберите согласованный ГПР этого объекта своей компании.')
            from datetime import timedelta
            if self.start_date != source.start_date or self.continuation_start != source.end_date + timedelta(days=1) or self.end_date < self.continuation_start or self.parent_gpr_id:
                raise ValidationError('Продолжение должно следовать сразу за предыдущим ГПР.')
        if self.parent_gpr_id:
            from calendar import monthrange
            parent = GlobalPlanVersion.objects.defer("snapshot").get(pk=self.parent_gpr_id)
            if parent.company_id != self.company_id or self.construction_object.parent_id != parent.construction_object_id:
                raise ValidationError({'parent_gpr': 'СМГ должен относиться к субобъекту выбранного ГПР своей компании.'})
            if self.start_date and self.end_date and (self.start_date.day != 1 or self.end_date != self.start_date.replace(day=monthrange(self.start_date.year, self.start_date.month)[1]) or self.start_date < parent.start_date or self.end_date > parent.end_date):
                raise ValidationError('СМГ должен охватывать один полный месяц внутри периода ГПР.')
        if self.baseline_version_id and (
            self.baseline_version.workspace_id != self.pk
            or self.baseline_version.version_kind != "BASELINE"
        ):
            raise ValidationError("Базовая версия другого пространства.")

    def save(self, *args, **kwargs):
        from django.core.exceptions import ValidationError

        if self.pk:
            old = type(self).objects.select_related("baseline_version").defer("baseline_version__snapshot").get(pk=self.pk)
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
                    "parent_gpr_id",
                    "continued_from_review_id",
                    "continuation_start",
                    "is_macro_consolidation",
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
    selected_item_ids = models.JSONField("Выбранные работы в составе комплекса", default=list, blank=True)
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
        if self.version_id and self.version.workspace_id and self.version.workspace.parent_gpr_id and self.work_id and not self.version.workspace.gpr_work_mappings.filter(smg_work_id=self.work_id).exists():
            raise ValidationError({'work': 'Работы СМГ должны быть связаны с работами исходного ГПР.'})
        if self.quantity is not None and self.quantity < 0:
            raise ValidationError({"quantity": "Объём не может быть отрицательным."})
        if self.load_profile_id and self.load_profile.company_id != self.company_id:
            raise ValidationError({"load_profile": "Профиль другой компании."})

        selected=self.selected_item_ids
        if not isinstance(selected,list) or any(type(pk) is not int or pk<=0 for pk in selected) or len(selected)!=len(set(selected)):
            raise ValidationError({'selected_item_ids':'Некорректный список выбранных работ.'})
        if selected and (self.work.kind!='COMPOSITE' or set(selected)-set(self.work.items.filter(company=self.company).values_list('pk',flat=True))):
            raise ValidationError({'selected_item_ids':'Выбранные работы не принадлежат этому комплексу.'})

        if not isinstance(self.item_quantities, dict):
            raise ValidationError({"item_quantities": "Объёмы подработ должны быть словарём."})
        if self.item_quantities:
            from decimal import Decimal, InvalidOperation
            from apps.works.progress import work_specification, quantity_from_totals
            if self.work.kind != "COMPOSITE" or not isinstance(self.item_quantities, dict):
                raise ValidationError({"item_quantities": "Отдельные объёмы допустимы только для составной работы."})
            items = list(self.work.items.filter(company=self.company))
            if selected:items=[item for item in items if item.pk in selected]
            if set(self.item_quantities) != {str(item.pk) for item in items}:
                raise ValidationError({"item_quantities": "Укажите объёмы всех подработ этой работы."})
            if self.version.version_kind == "FORECAST":
                baseline = self.version.workspace.baseline_version
                base_snapshot = self.version.baseline_review.snapshot if self.version.baseline_review_id else (baseline.snapshot if baseline else {})
                if self.work.items.filter(merge_origin__isnull=False).exists():
                    from apps.works.merge_planning import transform_snapshot
                    base_snapshot = transform_snapshot(base_snapshot,self.company)
                frozen = next((spec for spec in base_snapshot.get("works", []) if spec["id"] == self.work_id), None) if baseline else None
                if frozen and {item.pk: item.quantity_per_unit for item in items} != {item["id"]: Decimal(item["norm"]) for item in frozen.get("valuation_items",frozen["items"]) if not selected or item["id"] in selected}:
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
                allowed=(set(selected) if selected else set(self.work.items.values_list('pk',flat=True))) if self.work.kind == 'COMPOSITE' else {None}
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
        if self.pk and self.daily_override is not None and not getattr(self,"_preserve_selected_override",False):
            previous = type(self).objects.get(pk=self.pk)
            if (previous.quantity, previous.load_profile_id, previous.item_quantities, previous.selected_item_ids) != (self.quantity, self.load_profile_id, self.item_quantities, self.selected_item_ids):
                self.daily_override = None
                if kwargs.get('update_fields') is not None:
                    kwargs['update_fields']=set(kwargs['update_fields']) | {'daily_override'}
        self.full_clean()
        return super().save(*args, **kwargs)


class ResourceMonthAllocation(BaseCompanyModel):
    class Kind(models.TextChoices):
        LABOR = "labor", "Должность"
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

    crew_data = models.JSONField('Состав и расчёт экипажа', default=dict, blank=True)

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
    status = getattr(row, "_validated_workspace_status", None)
    if status is None:
        status = GlobalPlanVersion.objects.values_list("status", flat=True).get(pk=version.pk)
    if status not in ["DRAFT", "REJECTED"]:
        raise ValidationError("Отправленную или утверждённую версию менять нельзя.")
    if row.month and version.workspace.continuation_start and row.month < version.workspace.continuation_start:
        raise ValidationError('Прошлый период сохранен из утвержденного ГПР и недоступен для редактирования.')
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
    input_fingerprint = models.CharField(max_length=64, blank=True, editable=False)
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
    section = models.CharField("Раздел", max_length=20, choices=[("ESTIMATE","Сметчик"),("PRODUCTION","Работы"),("HR","Должность"),("TECH","Техника и ГСМ"),("SECURITY","Служба безопасности"),("SBO","Служба СБО"),("CEO","Генеральный директор"),("PLANNER","Подготовка")])
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


class ReportSnapshotProjection(models.Model):
    """Rebuildable, period-sized report data for an immutable approval round."""
    version = models.ForeignKey(GlobalPlanVersion, on_delete=models.CASCADE)
    approval_round = models.PositiveIntegerField()
    source_key = models.CharField(max_length=255, default="version")
    start = models.DateField()
    end = models.DateField()
    snapshot = models.JSONField(default=dict)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['version', 'approval_round', 'source_key', 'start', 'end'],
                                               name='unique_report_snapshot_period')]


class SmgWorkMapping(BaseCompanyModel):
    item_mapping = models.JSONField(default=dict, blank=True)
    workspace = models.ForeignKey(PlanningWorkspace, on_delete=models.CASCADE, related_name='gpr_work_mappings')
    gpr_work = models.ForeignKey('works.ProjectWork', on_delete=models.PROTECT, related_name='smg_mappings')
    smg_work = models.ForeignKey('works.ProjectWork', on_delete=models.PROTECT, related_name='gpr_mappings')

    class Meta:
        constraints = [models.UniqueConstraint(fields=['workspace', 'gpr_work'], name='unique_smg_gpr_work'), models.UniqueConstraint(fields=['workspace', 'smg_work'], name='unique_smg_target_work')]

    def clean(self):
        from django.core.exceptions import ValidationError
        if not self.workspace.parent_gpr_id or self.workspace.company_id != self.company_id or self.gpr_work.company_id != self.company_id or self.smg_work.company_id != self.company_id:
            raise ValidationError('Связь работ допустима только внутри СМГ своей компании.')
        if self.gpr_work.section.construction_object_id != self.workspace.parent_gpr.construction_object_id or self.smg_work.section.construction_object_id != self.workspace.construction_object_id:
            raise ValidationError('Работы должны принадлежать ГПР макрообъекта и СМГ субобъекта.')
        if self.gpr_work.unit != self.smg_work.unit or self.gpr_work.kind != self.smg_work.kind:
            raise ValidationError('Единица и тип работ ГПР и СМГ должны совпадать.')


class GprImportReceipt(BaseCompanyModel):
    nonce = models.UUIDField(unique=True)
    workspace = models.ForeignKey(PlanningWorkspace, on_delete=models.SET_NULL, null=True, blank=True)
    imported_by = models.ForeignKey('accounts.User', on_delete=models.PROTECT)


class WorkCarryover(BaseCompanyModel):
    is_forecast = models.BooleanField(default=False)
    forecast_as_of = models.DateField(null=True, blank=True)
    forecast_quantity = models.DecimalField(max_digits=15, decimal_places=3, null=True, blank=True)
    actual_quantity = models.DecimalField(max_digits=15, decimal_places=3, default=0)
    expected_completion = models.DecimalField(max_digits=15, decimal_places=3, default=0)
    added = models.BooleanField(default=True)
    version = models.ForeignKey(GlobalPlanVersion, on_delete=models.CASCADE, related_name='work_carryovers')
    source_version = models.ForeignKey(GlobalPlanVersion, on_delete=models.PROTECT, related_name='carried_work_sources')
    month = models.DateField()
    work = models.ForeignKey('works.ProjectWork', on_delete=models.PROTECT)
    quantity = models.DecimalField(max_digits=15, decimal_places=3)
    item_quantities = models.JSONField(default=dict)
    imported_by = models.ForeignKey('accounts.User', on_delete=models.PROTECT)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['version','month','work'], name='unique_work_carryover_month')]


class DailySchedule(BaseCompanyModel):
    replaces = models.ForeignKey('self', on_delete=models.PROTECT, null=True, blank=True, related_name='rebased_schedules')
    locked_before = models.DateField(null=True, blank=True)
    approval_round = models.PositiveIntegerField(default=0)
    construction_object = models.ForeignKey('projects.ConstructionObject', on_delete=models.PROTECT, related_name='daily_schedules')
    source = models.ForeignKey(GlobalPlanVersion, on_delete=models.PROTECT, related_name='daily_schedules', null=True, blank=True)
    month = models.DateField()
    revision = models.PositiveIntegerField()
    status = models.CharField(max_length=12, default='DRAFT', choices=[('DRAFT','Черновик'),('SUBMITTED','На согласовании'),('APPROVED','Утвержден'),('APPLIED','Применен'),('ARCHIVED','Предыдущая редакция')])
    created_by = models.ForeignKey('accounts.User', on_delete=models.PROTECT)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['construction_object','month','revision'], name='unique_object_daily_schedule_revision'), models.UniqueConstraint(fields=['construction_object','month'], condition=models.Q(status='APPLIED'), name='unique_object_applied_daily_schedule')]

    def __str__(self):
        return f'СМГ · {self.construction_object} · {self.month:%m.%Y} · редакция {self.revision}'

    def save(self, *args, **kwargs):
        if not self.construction_object_id and self.source_id:
            self.construction_object_id = self.source.construction_object_id
        from .subobject_smg import valid_reference
        if self.source_id and (self.source.company_id != self.company_id or not valid_reference(self.construction_object,self.source)):
            from django.core.exceptions import ValidationError
            raise ValidationError('Выберите ГПР этого объекта или его родительского объекта в той же компании и проекте.')
        super().save(*args, **kwargs)


class SubobjectWorkOrigin(BaseCompanyModel):
    source_work=models.ForeignKey('works.ProjectWork',on_delete=models.PROTECT,related_name='subobject_origins')
    local_work=models.OneToOneField('works.ProjectWork',on_delete=models.CASCADE,related_name='gpr_origin')
    item_mapping=models.JSONField(default=dict,blank=True)

    def clean(self):
        from django.core.exceptions import ValidationError
        from .subobject_smg import ancestors
        if self.source_work.company_id!=self.company_id or self.local_work.company_id!=self.company_id or self.source_work.section.construction_object_id==self.local_work.section.construction_object_id:
            raise ValidationError('Связь работ должна относиться к одной компании и разным объектам.')
        if self.source_work.section.construction_object_id not in ancestors(self.local_work.section.construction_object):raise ValidationError('Исходная работа должна относиться к родительскому объекту.')
        if not isinstance(self.item_mapping,dict):raise ValidationError('Проверьте сопоставление состава работ.')


class DailyScheduleEntry(models.Model):
    schedule = models.ForeignKey(DailySchedule,on_delete=models.CASCADE,related_name='entries')
    gpr_work = models.ForeignKey('works.ProjectWork',on_delete=models.PROTECT,null=True,blank=True,related_name='subobject_smg_entries',verbose_name='Работа общего ГПР')
    key = models.CharField(max_length=80)
    kind = models.CharField(max_length=12)
    label = models.TextField()
    metadata = models.JSONField(default=dict)
    original = models.JSONField(default=list)
    rows = models.JSONField(default=list)
    revision = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ['kind','label','pk']
        constraints = [models.UniqueConstraint(fields=['schedule','key'],name='unique_daily_schedule_entry')]


class CarryoverReviewTask(BaseCompanyModel):
    version = models.ForeignKey(GlobalPlanVersion, on_delete=models.CASCADE, related_name='carryover_tasks')
    # Null denotes the explicitly connected, frozen history of all sub-objects.
    source_version = models.ForeignKey(GlobalPlanVersion, on_delete=models.PROTECT, related_name='carryover_source_tasks', null=True, blank=True)
    month = models.DateField()
    due_date = models.DateField()
    assigned_to = models.ForeignKey('accounts.User', on_delete=models.PROTECT, related_name='carryover_tasks')
    correction_version = models.ForeignKey(GlobalPlanVersion, on_delete=models.SET_NULL, null=True, blank=True, related_name='carryover_corrections')
    corrected_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['version','source_version','month'], name='unique_carryover_review_task'), models.UniqueConstraint(fields=['version','month'],condition=models.Q(source_version__isnull=True),name='unique_macro_carryover_review_task')]


class EstimateReviewTask(BaseCompanyModel):
    version = models.ForeignKey(GlobalPlanVersion, null=True, blank=True, on_delete=models.CASCADE, related_name='estimate_reviews')
    schedule = models.ForeignKey(DailySchedule, null=True, blank=True, on_delete=models.CASCADE, related_name='estimate_reviews')
    assigned_to = models.ForeignKey('accounts.User', on_delete=models.PROTECT, related_name='estimate_reviews')
    created_by = models.ForeignKey('accounts.User', on_delete=models.PROTECT, related_name='requested_estimate_reviews')
    status = models.CharField(max_length=12, default='OPEN', choices=[('OPEN','На проверке'),('APPROVED','Проверено'),('REJECTED','На доработке')])
    fingerprint = models.CharField(max_length=64, blank=True)
    decisions = models.JSONField(default=dict, blank=True)
    comment = models.TextField(blank=True)
    approved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [models.CheckConstraint(condition=(models.Q(version__isnull=False, schedule__isnull=True) | models.Q(version__isnull=True, schedule__isnull=False)), name='estimate_review_one_target')]


class EstimateReviewEvent(BaseCompanyModel):
    task = models.ForeignKey(EstimateReviewTask, on_delete=models.CASCADE, related_name='events')
    actor = models.ForeignKey('accounts.User', on_delete=models.PROTECT)
    action = models.CharField(max_length=40)
    details = models.JSONField(default=dict)


class MacroHistorySource(BaseCompanyModel):
    workspace = models.ForeignKey(PlanningWorkspace,on_delete=models.CASCADE,related_name='macro_history_sources')
    review = models.ForeignKey(GlobalPlanReview,on_delete=models.PROTECT,related_name='macro_history_sources')
    construction_object = models.ForeignKey('projects.ConstructionObject',on_delete=models.PROTECT)
    start = models.DateField()
    end = models.DateField()

    class Meta:
        constraints = [models.UniqueConstraint(fields=['workspace','construction_object'],name='unique_macro_history_child')]


class WorkTask(BaseCompanyModel):
    schedule_review = models.ForeignKey('DailyScheduleReview',null=True,blank=True,on_delete=models.CASCADE,related_name='work_tasks')
    key = models.CharField(max_length=180)
    kind = models.CharField(max_length=20,choices=[('RESOURCES','Подготовка ресурсов'),('SUBMISSION','Следующий этап плана'),('ESTIMATE','Проверка сметчика'),('REMARK','Замечание'),('APPROVAL','Согласование'),('FACT','Факт за день'),('CARRYOVER','Уточнение остатков'),('MANUAL','Задание')])
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    status = models.CharField(max_length=20,default='OPEN',choices=[('OPEN','Открыто'),('IN_REVIEW','На проверке автора'),('DONE','Выполнено'),('SUPERSEDED','Заменено / отменено'),('CLOSED_ADMIN','Принудительно закрыто')])
    assigned_to = models.ForeignKey('accounts.User',null=True,blank=True,on_delete=models.SET_NULL,related_name='work_tasks')
    target_role = models.CharField(max_length=50,blank=True)
    created_by = models.ForeignKey('accounts.User',null=True,blank=True,on_delete=models.SET_NULL,related_name='authored_work_tasks')
    due_date = models.DateField(null=True,blank=True)
    construction_object = models.ForeignKey('projects.ConstructionObject',null=True,blank=True,on_delete=models.PROTECT)
    fact_date = models.DateField(null=True,blank=True)
    version = models.ForeignKey(GlobalPlanVersion,null=True,blank=True,on_delete=models.CASCADE,related_name='work_tasks')
    schedule = models.ForeignKey(DailySchedule,null=True,blank=True,on_delete=models.CASCADE)
    review = models.ForeignKey(GlobalPlanReview,null=True,blank=True,on_delete=models.CASCADE,related_name='work_tasks')
    estimate = models.ForeignKey(EstimateReviewTask,null=True,blank=True,on_delete=models.SET_NULL,related_name='work_tasks')
    carryover = models.ForeignKey(CarryoverReviewTask,null=True,blank=True,on_delete=models.SET_NULL)
    work = models.ForeignKey('works.ProjectWork',null=True,blank=True,on_delete=models.SET_NULL)
    parent = models.ForeignKey('self',null=True,blank=True,on_delete=models.SET_NULL,related_name='children')

    class Meta:
        ordering=['due_date','-created_at','-pk']
        constraints=[models.UniqueConstraint(fields=['company','key'],name='unique_company_work_task')]
        indexes=[models.Index(fields=['company','assigned_to','status'])]

    @property
    def role_label(self):
        return {'ESTIMATOR':'Сметчик','PRODUCTION_HEAD':'Руководитель производства','HR_HEAD':'Руководитель кадровой службы','TECH_HEAD':'Руководитель технической службы','SECURITY_HEAD':'Служба безопасности','SBO_HEAD':'Служба СБО','CEO':'Генеральный директор'}.get(self.target_role,'Не назначен')

    @property
    def overdue(self):
        from django.utils import timezone
        return self.status in {'OPEN','IN_REVIEW'} and self.due_date and self.due_date<timezone.localdate()


class WorkTaskEvent(BaseCompanyModel):
    actor_name = models.CharField(max_length=350,blank=True)
    task = models.ForeignKey(WorkTask,on_delete=models.CASCADE,related_name='events')
    actor = models.ForeignKey('accounts.User',null=True,blank=True,on_delete=models.SET_NULL)
    action = models.CharField(max_length=80)
    text = models.TextField(blank=True)

    class Meta:
        ordering=['created_at','pk']


class DailyTaskCursor(models.Model):
    user = models.ForeignKey('accounts.User',on_delete=models.CASCADE)
    construction_object = models.ForeignKey('projects.ConstructionObject',on_delete=models.CASCADE)
    last_date = models.DateField()

    class Meta:
        constraints=[models.UniqueConstraint(fields=['user','construction_object'],name='unique_dispatcher_daily_cursor')]


class MacroCarryoverWork(BaseCompanyModel):
    workspace = models.ForeignKey(PlanningWorkspace,on_delete=models.CASCADE,related_name='macro_carryover_works')
    source_work = models.ForeignKey('works.ProjectWork',on_delete=models.PROTECT,related_name='macro_carryover_sources')
    target_work = models.ForeignKey('works.ProjectWork',on_delete=models.PROTECT,related_name='macro_carryover_targets')
    item_mapping = models.JSONField(default=dict)

    class Meta:
        constraints=[models.UniqueConstraint(fields=['workspace','source_work'],name='unique_macro_carryover_source'),models.UniqueConstraint(fields=['workspace','target_work'],name='unique_macro_carryover_target')]


class DailyScheduleReview(BaseCompanyModel):
    schedule = models.ForeignKey(DailySchedule,on_delete=models.CASCADE,related_name='approval_reviews')
    number = models.PositiveIntegerField()
    fingerprint = models.CharField(max_length=64)
    snapshot = models.JSONField(default=list)
    submitted_by = models.ForeignKey('accounts.User',on_delete=models.PROTECT)

    class Meta:
        constraints=[models.UniqueConstraint(fields=['schedule','number'],name='unique_daily_schedule_approval_round')]

    def save(self,*args,**kwargs):
        if self.pk:raise ValidationError('Раунд согласования СМГ изменять нельзя.')
        return super().save(*args,**kwargs)


class DailyScheduleDecision(BaseCompanyModel):
    review = models.ForeignKey(DailyScheduleReview,on_delete=models.CASCADE,related_name='decisions')
    section = models.CharField(max_length=20)
    action = models.CharField(max_length=12)
    actor = models.ForeignKey('accounts.User',on_delete=models.PROTECT)
    actor_name = models.CharField(max_length=350)
    actor_role = models.CharField(max_length=50)
    comment = models.TextField(blank=True)

    class Meta:
        constraints=[models.UniqueConstraint(fields=['review','section','action'],name='unique_daily_schedule_decision')]

    def save(self,*args,**kwargs):
        if self.pk:raise ValidationError('Решение согласования СМГ изменять нельзя.')
        return super().save(*args,**kwargs)


class WorkResourceAssignment(BaseCompanyModel):
    """One allocation counted once across its shared leaf-work targets."""
    fuel_type = models.CharField('Вид ГСМ',max_length=20,blank=True,choices=ResourceMonthAllocation._meta.get_field('fuel_type').choices)
    daily_liters = models.DecimalField('Расход ГСМ в день, л',max_digits=12,decimal_places=3,default=0)
    fuel_price = models.DecimalField('Цена литра, ₽',max_digits=12,decimal_places=2,default=0)
    include_crew = models.BooleanField('Добавлять экипаж техники автоматически',default=True)
    key = models.UUIDField(default=uuid.uuid4,editable=False)
    version = models.ForeignKey(GlobalPlanVersion,on_delete=models.CASCADE,null=True,blank=True,related_name='resource_assignments')
    schedule = models.ForeignKey(DailySchedule,on_delete=models.CASCADE,null=True,blank=True,related_name='resource_assignments')
    work = models.ForeignKey('works.ProjectWork',on_delete=models.PROTECT,null=True,blank=True,related_name='resource_assignments',verbose_name='Работа / комплекс работ')
    work_item = models.ForeignKey('works.ProjectWorkItem',on_delete=models.PROTECT,null=True,blank=True,related_name='resource_assignments',verbose_name='Работа в составе комплекса')
    equipment_type = models.ForeignKey('resources.EquipmentType',on_delete=models.PROTECT,null=True,blank=True,verbose_name='Техника')
    position = models.ForeignKey('resources.Position',on_delete=models.PROTECT,null=True,blank=True,verbose_name='Должность без техники')
    start_date = models.DateField('Начало')
    end_date = models.DateField('Окончание')
    count = models.PositiveIntegerField('Количество техники / людей на смену',default=1)
    shifts_per_day = models.PositiveSmallIntegerField('Смен в день',default=1)
    hours_per_shift = models.DecimalField('Часов в смене',max_digits=4,decimal_places=2,default=8)
    crew_snapshot = models.JSONField(default=list,blank=True)

    def clean(self):
        from django.core.exceptions import ValidationError
        super().clean()
        if bool(self.version_id)==bool(self.schedule_id):raise ValidationError('Выберите только один план-владелец.')
        owner=self.version if self.version_id else self.schedule
        if owner.company_id!=self.company_id:raise ValidationError('План другой компании.')
        if owner.status not in ['DRAFT','REJECTED']:raise ValidationError('Назначения меняются только в черновике.')
        for name in ['work','work_item','equipment_type','position']:
            if getattr(self,name+'_id') and getattr(self,name).company_id!=self.company_id:raise ValidationError({name:'Запись другой компании.'})
        if self.work_item_id and (not self.work_id or self.work_item.project_work_id!=self.work_id):raise ValidationError({'work_item':'Выберите работу из указанного комплекса.'})
        if self.work_id and self.work.section.construction_object_id!=owner.construction_object_id:raise ValidationError({'work':'Работа другого объекта.'})
        if self.work_id and self.work.kind=='COMPOSITE' and not self.work_item_id:
            legacy=type(self).objects.filter(pk=self.pk,work_id=self.work_id,work_item__isnull=True).exists() if self.pk else False
            if not legacy:raise ValidationError({'work_item':'Ресурс назначается на работу в составе комплекса, самостоятельную работу или объект. Выберите работу внутри комплекса.'})
        if sum([bool(self.equipment_type_id),bool(self.position_id),bool(self.fuel_type)])!=1:raise ValidationError('Выберите только один ресурс: технику, должность или ГСМ.')
        if self.fuel_type and self.work_id:raise ValidationError({'work':'ГСМ назначается только на объект.'})
        if self.fuel_type and (self.daily_liters is None or self.daily_liters<=0):raise ValidationError({'daily_liters':'Укажите положительный расход ГСМ в день.'})
        if self.fuel_price is not None and self.fuel_price<0:raise ValidationError({'fuel_price':'Цена не может быть отрицательной.'})
        if self.start_date and self.end_date:
            from .estimate_review import review_period
            start,end=review_period(owner)
            if not start<=self.start_date<=self.end_date<=end:raise ValidationError('Период назначения должен находиться внутри редактируемого периода плана.')
        if self.count is not None and self.count<1:raise ValidationError({'count':'Количество должно быть положительным.'})
        if self.shifts_per_day is not None and not 1<=self.shifts_per_day<=24:raise ValidationError({'shifts_per_day':'Число смен должно быть от 1 до 24.'})
        if self.hours_per_shift is not None and (self.hours_per_shift<=0 or self.shifts_per_day and self.hours_per_shift*self.shifts_per_day>24):raise ValidationError({'hours_per_shift':'Суммарная продолжительность смен не должна превышать 24 часа в сутки.'})

    class Meta:
        ordering=['start_date','pk']
        constraints=[models.UniqueConstraint(fields=['version','key'],condition=models.Q(version__isnull=False),name='unique_gpr_assignment_key'),models.UniqueConstraint(fields=['schedule','key'],condition=models.Q(schedule__isnull=False),name='unique_smg_assignment_key'),models.CheckConstraint(condition=models.Q(hours_per_shift__gt=0,start_date__lte=models.F('end_date')),name='valid_assignment_hours_dates'),models.CheckConstraint(condition=(models.Q(version__isnull=False,schedule__isnull=True)|models.Q(version__isnull=True,schedule__isnull=False)),name='one_assignment_owner'),models.CheckConstraint(condition=(models.Q(equipment_type__isnull=False,position__isnull=True,fuel_type='')|models.Q(equipment_type__isnull=True,position__isnull=False,fuel_type='')|(models.Q(equipment_type__isnull=True,position__isnull=True)&~models.Q(fuel_type=''))),name='one_assignment_resource'),models.CheckConstraint(condition=models.Q(count__gte=1,shifts_per_day__gte=1,shifts_per_day__lte=24),name='positive_assignment_count_shifts')]


class WorkResourceTarget(models.Model):
    """Additional work links of one allocation; the primary legacy FK is retained."""
    assignment = models.ForeignKey(WorkResourceAssignment, on_delete=models.CASCADE, related_name='extra_targets')
    work = models.ForeignKey('works.ProjectWork', on_delete=models.PROTECT, related_name='shared_resource_links')
    work_item = models.ForeignKey('works.ProjectWorkItem', on_delete=models.PROTECT, null=True, blank=True, related_name='shared_resource_links')

    def clean(self):
        from django.core.exceptions import ValidationError
        super().clean()
        from .resource_targets import normalize
        if self.assignment_id and self.work_id:
            if not self.assignment.work_id:raise ValidationError('Общие ресурсы объекта не совмещаются с привязкой к работам.')
            owner = self.assignment.version if self.assignment.version_id else self.assignment.schedule
            normalize(owner, [(self.work_id, self.work_item_id)])
            if (self.work_id, self.work_item_id) == (self.assignment.work_id, self.assignment.work_item_id):
                raise ValidationError('Работа уже указана в основном назначении.')

    class Meta:
        verbose_name = 'дополнительная привязка ресурса к работе'
        verbose_name_plural = 'дополнительные привязки ресурсов к работам'
        ordering = ['work_id', 'work_item_id', 'pk']
        constraints = [
            models.UniqueConstraint(fields=['assignment','work','work_item'], condition=models.Q(work_item__isnull=False), name='unique_shared_resource_item'),
            models.UniqueConstraint(fields=['assignment','work'], condition=models.Q(work_item__isnull=True), name='unique_shared_resource_work'),
        ]


class PivotReportPreset(BaseCompanyModel):
    user = models.ForeignKey('accounts.User', on_delete=models.CASCADE, related_name='pivot_presets')
    section = models.CharField(max_length=16, choices=[('plan_fact','План / факт'),('facts','Факт'),('gpr','ГПР'),('smg','СМГ')])
    name = models.CharField(max_length=120)
    config = models.JSONField(default=dict)
    is_default = models.BooleanField(default=False)

    class Meta:
        ordering = ['name', 'pk']
        constraints = [models.UniqueConstraint(fields=['company','user','section','name'], name='pivot_preset_name_unique'),
                       models.UniqueConstraint(fields=['company','user','section'], condition=models.Q(is_default=True), name='pivot_preset_one_default')]


class PlanPreparation(BaseCompanyModel):
    version = models.OneToOneField(GlobalPlanVersion, null=True, blank=True, on_delete=models.CASCADE, related_name='preparation')
    schedule = models.OneToOneField(DailySchedule, null=True, blank=True, on_delete=models.CASCADE, related_name='preparation')
    manager = models.ForeignKey('accounts.User', on_delete=models.PROTECT, related_name='managed_plan_preparations')
    handed_by = models.ForeignKey('accounts.User', on_delete=models.PROTECT, related_name='handed_plan_preparations')
    resources_fingerprint = models.CharField(max_length=64, blank=True)
    resources_ready_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [models.CheckConstraint(condition=(models.Q(version__isnull=False,schedule__isnull=True)|models.Q(version__isnull=True,schedule__isnull=False)),name='preparation_exactly_one_plan')]

from .object_plan_models import ObjectWorkPlan, ObjectWorkPlanRow


class ResourceWorkLink(BaseCompanyModel):
    """Work links of monthly/day-table resources, without changing their numbers."""
    allocation = models.ForeignKey(ResourceMonthAllocation,on_delete=models.CASCADE,null=True,blank=True,related_name='graph_links')
    entry = models.ForeignKey(DailyScheduleEntry,on_delete=models.CASCADE,null=True,blank=True,related_name='graph_links')
    work = models.ForeignKey('works.ProjectWork',on_delete=models.PROTECT,related_name='table_resource_links')
    work_item = models.ForeignKey('works.ProjectWorkItem',on_delete=models.PROTECT,null=True,blank=True,related_name='table_resource_links')

    class Meta:
        ordering=['work_id','work_item_id','pk']
        constraints=[models.CheckConstraint(condition=(models.Q(allocation__isnull=False,entry__isnull=True)|models.Q(allocation__isnull=True,entry__isnull=False)),name='one_table_resource_link_owner'),
            models.UniqueConstraint(fields=['allocation','work','work_item'],condition=models.Q(work_item__isnull=False),name='unique_month_resource_item'),
            models.UniqueConstraint(fields=['allocation','work'],condition=models.Q(work_item__isnull=True),name='unique_month_resource_work'),
            models.UniqueConstraint(fields=['entry','work','work_item'],condition=models.Q(work_item__isnull=False),name='unique_day_resource_item'),
            models.UniqueConstraint(fields=['entry','work'],condition=models.Q(work_item__isnull=True),name='unique_day_resource_work')]
