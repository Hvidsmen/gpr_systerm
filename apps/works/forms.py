from django import forms
from django.utils.translation import gettext_lazy as _

from .models import ProjectWork, ProjectWorkItem, WorkTemplateItem
from ..projects.models import Section


class ProjectWorkForm(forms.ModelForm):
    """Форма для создания/редактирования РАБОТЫ."""

    def clean(self):
        data = super().clean()
        template = data.get("template")
        if template and data.get("kind") == "COMPOSITE":
            version = (
                template.versions.filter(is_current=True).first()
                or template.versions.order_by("-version_number").first()
            )
            if version and version.items.filter(quantity_per_unit__lte=0).exists():
                self.add_error(
                    "template",
                    "В шаблоне есть неположительные нормативы. Исправьте шаблон перед использованием.",
                )
        return data

    class Meta:
        model = ProjectWork
        fields = [
            "code",
            "name",
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


class ProjectWorkItemForm(forms.ModelForm):
    """Форма для создания/редактирования ПОДРАБОТЫ."""

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


class WorkTemplateItemForm(forms.ModelForm):
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
