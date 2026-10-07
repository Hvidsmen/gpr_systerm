"""Company-scoped, cascading filters for the work catalog."""

from django import forms
from apps.projects.models import Project, ConstructionObject, Section


class WorkListFilterForm(forms.Form):
    project = forms.ModelChoiceField(
        queryset=Project.objects.none(),
        required=False,
        label="Проект",
        empty_label="Все проекты",
    )
    construction_object = forms.ModelChoiceField(
        queryset=ConstructionObject.objects.none(),
        required=False,
        label="Объект",
        empty_label="Все объекты",
    )
    section = forms.ModelChoiceField(
        queryset=Section.objects.none(),
        required=False,
        label="Раздел",
        empty_label="Все разделы",
    )
    work = forms.CharField(
        required=False,
        max_length=255,
        label="Работа",
        widget=forms.TextInput(attrs={"placeholder": "Название работы"}),
    )
    subwork = forms.CharField(
        required=False,
        max_length=255,
        label="Подработа",
        widget=forms.TextInput(attrs={"placeholder": "Название подработы"}),
    )

    def __init__(self, *args, company, **kwargs):
        super().__init__(*args, **kwargs)
        projects = Project.objects.filter(company=company).order_by("name", "pk")
        objects = (
            ConstructionObject.objects.filter(company=company, project__company=company)
            .select_related("project")
            .order_by("name", "pk")
        )
        sections = (
            Section.objects.filter(
                company=company,
                construction_object__company=company,
                construction_object__project__company=company,
            )
            .select_related("construction_object")
            .order_by("name", "pk")
        )
        project_id = self.data.get("project", "")
        object_id = self.data.get("construction_object", "")
        if project_id:
            objects = (
                objects.filter(project_id=project_id)
                if str(project_id).isdigit()
                else objects.none()
            )
            sections = (
                sections.filter(construction_object__project_id=project_id)
                if str(project_id).isdigit()
                else sections.none()
            )
        if object_id:
            sections = (
                sections.filter(construction_object_id=object_id)
                if str(object_id).isdigit()
                else sections.none()
            )
        self.fields["project"].queryset = projects
        self.fields["construction_object"].queryset = objects
        self.fields["section"].queryset = sections
        self.fields["construction_object"].label_from_instance = (
            lambda obj: f"{obj.project.name} / {obj.name}"
        )
        self.fields["section"].label_from_instance = (
            lambda section: f"{section.construction_object.name} / {section.name}"
        )
        for field in self.fields.values():
            field.widget.attrs["class"] = (
                "form-select form-select-sm"
                if isinstance(field, forms.ModelChoiceField)
                else "form-control form-control-sm"
            )
