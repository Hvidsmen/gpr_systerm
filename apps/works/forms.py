from django import forms
from django.utils.translation import gettext_lazy as _

from .models import ProjectWork, ProjectWorkItem, WorkTemplateItem, MeasurementUnit
from ..projects.models import Section, Project, ConstructionObject
from core.permissions import scope_queryset
from .price_sources import source_field, identity
from .prices import price_on


class UnitChoiceMixin:
    def configure_catalog(self, company):
        units = MeasurementUnit.objects.filter(company=company)
        self.fields['unit'] = forms.ChoiceField(
            label='Единица измерения',
            choices=[('', 'Выберите единицу измерения'), *[(unit.symbol, str(unit)) for unit in units]],
            widget=forms.Select(attrs={'class': 'form-select'}),
        )


class ProjectWorkForm(UnitChoiceMixin, forms.ModelForm):
    """Форма для создания/редактирования РАБОТЫ."""

    project = forms.ModelChoiceField(queryset=Project.objects.none(), label='Проект', empty_label='Выберите проект')
    construction_object = forms.ModelChoiceField(queryset=ConstructionObject.objects.none(), label='Строительный объект', empty_label='Сначала выберите проект')

    def __init__(self, *args, user=None, fixed_object=None, **kwargs):
        super().__init__(*args, **kwargs)
        company = user.company if user else self.instance.company if self.instance.company_id else None
        if company:
            self.instance.company = company
        projects = Project.objects.filter(company=company) if company else Project.objects.none()
        objects = ConstructionObject.objects.filter(company=company) if company else ConstructionObject.objects.none()
        sections = Section.objects.filter(company=company) if company else Section.objects.none()
        if user:
            projects, objects, sections = (scope_queryset(qs, user) for qs in (projects, objects, sections))
        if fixed_object:
            projects = projects.filter(pk=fixed_object.project_id)
            objects = objects.filter(pk=fixed_object.pk)
            sections = sections.filter(construction_object=fixed_object)
            self.initial.update(project=fixed_object.project_id, construction_object=fixed_object.pk)
            self.fields['project'].disabled = self.fields['construction_object'].disabled = True
        elif self.instance.section_id:
            obj = self.instance.section.construction_object
            self.initial.setdefault('project', obj.project_id)
            self.initial.setdefault('construction_object', obj.pk)
        self.fields['project'].queryset = projects
        project_id = self.data.get(self.add_prefix('project')) if self.is_bound and not fixed_object else self.initial.get('project')
        object_id = self.data.get(self.add_prefix('construction_object')) if self.is_bound and not fixed_object else self.initial.get('construction_object')
        try:
            project_id = int(project_id)
        except (ValueError, TypeError):
            project_id = None
        try:
            object_id = int(object_id)
        except (ValueError, TypeError):
            object_id = None
        self.fields['construction_object'].queryset = objects.filter(project_id=project_id) if project_id else objects.none()
        self.fields['section'].queryset = sections.filter(construction_object_id=object_id, construction_object__project_id=project_id) if object_id and project_id else sections.none()
        self.fields['construction_object'].empty_label = 'Выберите объект' if project_id else 'Сначала выберите проект'
        self.fields['section'].empty_label = 'Выберите раздел' if object_id else 'Сначала выберите объект'
        for name in ('project', 'construction_object', 'section'):
            self.fields[name].label_from_instance = lambda row: f'{row.name}'
        self.hierarchy = {
            'objects': list(objects.values('id', 'project_id', 'code', 'name')),
            'sections': list(sections.values('id', 'construction_object_id', 'code', 'name')),
        }
        for name in ('template', 'load_profile'):
            self.fields[name].queryset = self.fields[name].queryset.filter(company=company) if company else self.fields[name].queryset.none()
        for field in self.fields.values():
            if isinstance(field.widget, forms.Select):
                field.widget.attrs['class'] = 'form-select'
        kind = self.data.get(self.add_prefix('kind')) if self.is_bound else self.initial.get('kind', self.instance.kind)
        self.fields['load_profile'].required = kind == ProjectWork.Kind.SIMPLE
        self.fields['load_profile'].help_text = 'Обязателен для простой работы. Для составной профили задаются в подработах.'
        if self.instance.pk and "unit_price" in self.fields:
            self.fields["unit_price"].disabled = True
            self.fields["unit_price"].help_text = "Цена меняется в карточке работы с сохранением истории."
        if not self.instance.pk:
            self.fields['price_source'] = source_field(company)
            if self.is_bound and self.data.get(self.add_prefix('price_source')):
                self.fields['unit_price'].required = False
        self.configure_catalog(company)
        self.fields["work_group"].queryset = self.fields["work_group"].queryset.filter(company=company)
        self.fields["work_group"].empty_label = "Без группы"
        self.order_fields(['project', 'construction_object', 'section', *self._meta.fields])

    def clean(self):
        data = super().clean()
        source = data.get('price_source')
        if source:
            if identity(source.unit) != identity(data.get('unit') or ''):
                self.add_error('price_source', 'Единицы измерения работ не совпадают.')
            else:
                data['unit_price'] = price_on(source)
        template = data.get("template")
        if template and data.get("kind") == "COMPOSITE":
            version = (
                template.versions.filter(is_current=True).first()
                or template.versions.order_by("-version_number").first()
            )
            if version and version.items.filter(load_profile__isnull=True).exists():
                self.add_error('template', 'В шаблоне есть подработы без профиля нагрузки. Заполните их профили перед использованием.')
            if version and version.items.filter(quantity_per_unit__lte=0).exists():
                self.add_error(
                    "template",
                    "В шаблоне есть неположительные нормативы. Исправьте шаблон перед использованием.",
                )
        return data

    class Meta:
        model = ProjectWork
        fields = [
            "name",
            "work_group",
            "unit",
            "section",
            "unit_price",
            "template",
            "status",
            "kind",
            "allow_fractional",
            "load_profile",
        ]
        widgets = {
            "code": forms.TextInput(
                attrs={"class": "form-control", "placeholder": "Например: 01.01.01"}
            ),
            "name": forms.TextInput(attrs={"class": "form-control"}),
            "unit": forms.TextInput(attrs={"class": "form-control"}),
            "section": forms.Select(attrs={"class": "form-control"}),
            "unit_price": forms.NumberInput(
                attrs={"class": "form-control", "step": "0.01"}
            ),
            "template": forms.Select(attrs={"class": "form-control"}),
            "status": forms.Select(attrs={"class": "form-control"}),
        }
        labels = {
            "code": "Код работы",
            "name": "Название работы",
            "unit": "Единица измерения",
            "section": "Раздел",
            "unit_price": "Цена за единицу, ₽",
            "template": "Шаблон работы",
            "status": "Статус",
        }
        help_texts = {
            "code": "Уникальный код в рамках раздела",
            "template": "Если выбран — подработы будут созданы автоматически из шаблона",
        }


class ProjectWorkItemForm(UnitChoiceMixin, forms.ModelForm):
    """Форма для создания/редактирования ПОДРАБОТЫ."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['load_profile'].required = True
        self.fields['load_profile'].help_text = 'Выберите профиль распределения объёма по рабочим дням.'

    class Meta:
        model = ProjectWorkItem
        fields = ["name", "unit", "load_profile", "weight", "quantity_per_unit"]
        widgets = {
            "name": forms.TextInput(attrs={"class": "form-control"}),
            "unit": forms.TextInput(attrs={"class": "form-control"}),
            "load_profile": forms.Select(attrs={"class": "form-control"}),
            "weight": forms.NumberInput(
                attrs={"class": "form-control", "step": "0.01"}
            ),
            "quantity_per_unit": forms.NumberInput(
                attrs={"class": "form-control", "step": "0.001"}
            ),
        }
        labels = {
            "name": "Название подработы",
            "unit": "Единица измерения",
            "load_profile": "Профиль нагрузки",
            "weight": "Вес (%)",
            "quantity_per_unit": "Норматив подработы (на ед. работы)",
        }
        help_texts = {
            "weight": "Доля стоимости подработы в общей стоимости работы",
            "quantity_per_unit": "Сколько единиц подработы нужно на 1 единицу работы",
        }


# apps/works/forms.py


class WorkSectionForm(forms.ModelForm):
    class Meta:
        model = Section
        fields = ["name", "construction_object"]
        widgets = {
            "name": forms.TextInput(attrs={"class": "form-control"}),
            "construction_object": forms.Select(attrs={"class": "form-control"}),
        }


class WorkTemplateItemForm(UnitChoiceMixin, forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['load_profile'].required = True
        self.fields['load_profile'].help_text = 'Выберите профиль распределения объёма по рабочим дням.'

    class Meta:
        model = WorkTemplateItem
        fields = [
            "name",
            "unit",
            "sequence",
            "weight",
            "quantity_per_unit",
            "load_profile",
        ]
        widgets = {
            "name": forms.TextInput(attrs={"class": "form-control"}),
            "unit": forms.TextInput(attrs={"class": "form-control"}),
            "sequence": forms.NumberInput(attrs={"class": "form-control"}),
            "weight": forms.NumberInput(
                attrs={"class": "form-control", "step": "0.01"}
            ),
            "quantity_per_unit": forms.NumberInput(
                attrs={"class": "form-control", "step": "0.001"}
            ),
            "load_profile": forms.Select(attrs={"class": "form-control"}),
        }
        labels = {
            "name": "Название подработы",
            "unit": "Единица измерения",
            "sequence": "Порядок",
            "weight": "Вес (%)",
            "quantity_per_unit": "Норматив подработы (на ед. работы)",
            "load_profile": "Профиль нагрузки",
        }
        help_texts = {
            "weight": "Доля стоимости подработы в общей стоимости работы",
            "quantity_per_unit": "Сколько единиц подработы нужно на 1 единицу работы",
        }
