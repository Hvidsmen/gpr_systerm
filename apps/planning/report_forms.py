from calendar import monthrange
from datetime import date
from django import forms
from apps.projects.models import Project, ConstructionObject
from apps.works.models import WorkGroup
from apps.resources.models import BrigadeGroup, BrigadeMacroGroup, EquipmentCategory
from apps.production.models import FuelFact
from .models import GlobalPlanVersion, ProjectPlanVersion
from .workspace_services import months_between

SECTIONS = [
    ("works", "Работы"),
    ("labor", "Люди"),
    ("equipment", "Техника"),
    ("fuel", "ГСМ"),
]


class MatrixReportForm(forms.Form):
    project = forms.ModelChoiceField(
        queryset=Project.objects.none(), required=False, label="Проект"
    )
    objects = forms.ModelMultipleChoiceField(
        queryset=ConstructionObject.objects.none(),
        required=False,
        label="Строительные объекты",
    )
    start = forms.DateField(
        label="Начало периода",
        widget=forms.DateInput(format="%Y-%m-%d", attrs={"type": "date"}),
    )
    end = forms.DateField(
        label="Конец периода",
        widget=forms.DateInput(format="%Y-%m-%d", attrs={"type": "date"}),
    )
    mode = forms.ChoiceField(
        label="План",
        choices=[
            ("latest", "Актуальный согласованный"),
            ("baseline", "Базовый согласованный"),
        ],
    )
    consolidated = forms.ModelChoiceField(
        queryset=ProjectPlanVersion.objects.none(),
        required=False,
        label="Сводная версия проекта",
    )
    month = forms.DateField(required=False, widget=forms.HiddenInput())
    sections = forms.MultipleChoiceField(
        choices=SECTIONS,
        required=False,
        widget=forms.CheckboxSelectMultiple(),
        label="Разделы",
    )
    works_q = forms.CharField(required=False, label="Название работы / подработы")
    work_group = forms.ModelChoiceField(
        queryset=WorkGroup.objects.none(), required=False, label="Группа работ"
    )
    work_kind = forms.ChoiceField(
        required=False,
        choices=[
            ("", "Все виды работ"),
            ("SIMPLE", "Простые"),
            ("COMPOSITE", "Составные"),
        ],
        label="Вид работы",
    )
    labor_q = forms.CharField(required=False, label="Название бригады")
    macro_group = forms.ModelChoiceField(
        queryset=BrigadeMacroGroup.objects.none(), required=False, label="Макрогруппа"
    )
    brigade_group = forms.ModelChoiceField(
        queryset=BrigadeGroup.objects.none(), required=False, label="Группа бригад"
    )
    equipment_q = forms.CharField(required=False, label="Название / номер техники")
    equipment_category = forms.ModelChoiceField(
        queryset=EquipmentCategory.objects.none(),
        required=False,
        label="Категория техники",
    )
    fuel_type = forms.ChoiceField(
        required=False,
        choices=[("", "Все виды ГСМ"), *FuelFact.FUEL_TYPE_CHOICES],
        label="Вид ГСМ",
    )

    def __init__(self, *args, user, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user
        for name, model in [
            ("project", Project),
            ("objects", ConstructionObject),
            ("work_group", WorkGroup),
            ("macro_group", BrigadeMacroGroup),
            ("brigade_group", BrigadeGroup),
            ("equipment_category", EquipmentCategory),
        ]:
            self.fields[name].queryset = model.objects.filter(company=user.company)
        self.fields["consolidated"].queryset = ProjectPlanVersion.objects.filter(
            company=user.company, status="FIXED"
        ).select_related("project")
        project_id = self.data.get("project")
        if project_id and str(project_id).isdigit():
            self.fields["objects"].queryset = self.fields["objects"].queryset.filter(
                project_id=project_id
            )
        # Each object has its own approved version choices; IDs cannot cross objects/companies.
        self.version_fields = []
        candidates = (
            GlobalPlanVersion.objects.filter(
                company=user.company, status__in=["APPROVED", "COMPLETED"]
            )
            .select_related("construction_object")
            .order_by("construction_object__name", "-version_number")
        )
        by_object = {}
        for version in candidates:
            by_object.setdefault(version.construction_object_id, []).append(version)
        for obj in self.fields["objects"].queryset:
            if obj.pk not in by_object:
                continue
            name = f"version_{obj.pk}"
            field = forms.ModelChoiceField(
                queryset=candidates.filter(construction_object=obj),
                required=False,
                label=obj.name,
                empty_label="Автоматически по выбранному режиму",
            )
            field.choices = [
                ("", field.empty_label),
                *[
                    (
                        v.pk,
                        f"v{v.version_number} · {v.title or v.get_version_kind_display()} · {v.start_date:%d.%m.%Y} — {v.end_date:%d.%m.%Y}",
                    )
                    for v in by_object[obj.pk]
                ],
            ]
            self.fields[name] = field
            self.version_fields.append(name)
        for name, field in self.fields.items():
            if name in ["sections", "month"]:
                continue
            field.widget.attrs["class"] = (
                "form-select form-select-sm"
                if isinstance(
                    field,
                    (
                        forms.ChoiceField,
                        forms.ModelChoiceField,
                        forms.ModelMultipleChoiceField,
                    ),
                )
                else "form-control form-control-sm"
            )
        self.fields["objects"].widget.attrs["size"] = 4
        self.fields["objects"].help_text = (
            "Без выбора — все доступные объекты. Для нескольких используйте Ctrl."
        )

    def clean(self):
        data = super().clean()
        start, end = data.get("start"), data.get("end")
        if start and end:
            if start > end:
                self.add_error("end", "Конец периода раньше начала.")
            elif len(months_between(start, end)) > 120:
                self.add_error("end", "Выберите период не более 120 месяцев.")
            month = data.get("month")
            if month and (
                month.day != 1
                or month > end
                or month.replace(day=monthrange(month.year, month.month)[1]) < start
            ):
                self.add_error("month", "Месяц находится вне выбранного периода.")
        consolidated = data.get("consolidated")
        if consolidated:
            if data.get("project") and consolidated.project_id != data["project"].pk:
                self.add_error(
                    "consolidated", "Сводная версия относится к другому проекту."
                )
            if (
                start
                and end
                and (start < consolidated.start_date or end > consolidated.end_date)
            ):
                self.add_error(
                    "end",
                    "Период отчёта должен находиться внутри периода сводной версии.",
                )
            if (
                data.get("objects")
                and data["objects"]
                .exclude(pk__in=consolidated.members.values("construction_object_id"))
                .exists()
            ):
                self.add_error(
                    "objects", "Объект не входит в состав выбранной сводной версии."
                )
            if any(data.get(name) for name in self.version_fields):
                self.add_error(
                    "consolidated",
                    "Для сводной версии используются её сохранённые планы. Сбросьте отдельные версии объектов.",
                )
        return data
