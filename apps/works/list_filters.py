"""Company-scoped, cascading filters for the work catalog."""

from django import forms
from apps.projects.models import Project, ConstructionObject, Section
from .models import ComplexObject, ProjectWork, ProjectWorkItem


class WorkListFilterForm(forms.Form):
    legacy_only=forms.BooleanField(required=False,label='Есть пометка «Старая структура»',widget=forms.CheckboxInput(attrs={'class':'form-check-input'}))
    q = forms.CharField(required=False, max_length=255, label='Поиск',
        widget=forms.TextInput(attrs={'type': 'search', 'placeholder': 'Найти комплекс или работу в его составе'}))

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
    complex_object = forms.ModelChoiceField(queryset=ComplexObject.objects.none(), required=False, label='Комплексный объект', empty_label='Все комплексные объекты')
    package = forms.ModelChoiceField(queryset=ProjectWork.objects.none(), required=False, label='Комплекс работ', empty_label='Все комплексные работы')
    selected_work = forms.ChoiceField(required=False, label='Работа')
    work = forms.CharField(
        required=False,
        max_length=255,
        label="Работа",
        widget=forms.TextInput(attrs={"placeholder": "Название работы"}),
    )
    subwork = forms.CharField(
        required=False,
        max_length=255,
        label="Работа в составе комплекса",
        widget=forms.TextInput(attrs={"placeholder": "Название работы в составе комплекса"}),
    )

    def __init__(self, *args, company, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        from core.permissions import role_code
        if not user or role_code(user)!='ADMIN':self.fields.pop('legacy_only',None)
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
        if user:
            from core.permissions import scope_queryset
            projects, objects, sections = (scope_queryset(queryset, user) for queryset in (projects, objects, sections))
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
        works=ProjectWork.objects.filter(company=company,section__in=sections,merged_source__isnull=True)
        complex_objects=ComplexObject.objects.filter(company=company,construction_object__in=objects)
        section_id=self.data.get('section','')
        if section_id:
            works=works.filter(section_id=section_id) if str(section_id).isdigit() else works.none()
            complex_objects=complex_objects.filter(work_packages__section_id=section_id).distinct() if str(section_id).isdigit() else complex_objects.none()
        complex_id=self.data.get('complex_object','')
        self.fields['complex_object'].queryset=complex_objects.order_by('name','pk')
        if complex_id:
            works=works.filter(complex_object_id=complex_id) if str(complex_id).isdigit() else works.none()
        packages=works.filter(kind=ProjectWork.Kind.COMPOSITE)
        self.fields['package'].queryset=packages.order_by('name','pk')
        package_id=self.data.get('package','')
        if package_id:
            works=works.filter(pk=package_id,kind=ProjectWork.Kind.COMPOSITE) if str(package_id).isdigit() else works.none()
        simple=works.filter(kind=ProjectWork.Kind.SIMPLE).order_by('name','pk')
        items=ProjectWorkItem.objects.filter(company=company,project_work__in=works).select_related('project_work').order_by('name','pk')
        self.fields['selected_work'].choices=[('', 'Все работы'), *[(f'w:{row.pk}', f'{row.name} · самостоятельная') for row in simple], *[(f'i:{row.pk}', f'{row.name} · {row.project_work.name}') for row in items]]
        self.fields['work'].label='Поиск по названию комплекса / работы'
        self.fields['subwork'].label='Поиск по работе в составе комплекса'
        self.order_fields(['q', 'project','construction_object','section','complex_object','package','selected_work','work','subwork'])
        for field in self.fields.values():
            field.widget.attrs["class"] = (
                "form-select form-select-sm"
                if isinstance(field, forms.ChoiceField)
                else "form-control form-control-sm"
            )
        if 'legacy_only' in self.fields:self.fields['legacy_only'].widget.attrs['class']='form-check-input'
