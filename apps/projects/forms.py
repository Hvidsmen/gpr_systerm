from django import forms
from django.utils.translation import gettext_lazy as _

from .models import Project, ConstructionObject, Section


class ProjectForm(forms.ModelForm):
    class Meta:
        model = Project
        fields = ['code', 'name', 'description', 'status', 'start_date', 'end_date']
        widgets = {
            'code': forms.TextInput(attrs={'class': 'form-control'}),
            'name': forms.TextInput(attrs={'class': 'form-control'}),
            'description': forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
            'status': forms.Select(attrs={'class': 'form-control'}),
            'start_date': forms.DateInput(attrs={'class': 'form-control', 'type': 'date'}),
            'end_date': forms.DateInput(attrs={'class': 'form-control', 'type': 'date'}),
        }


class ConstructionObjectForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['parent'].queryset = ConstructionObject.objects.filter(
            company_id=self.instance.company_id, project_id=self.instance.project_id,
        ).exclude(pk=self.instance.pk)

    def clean_parent(self):
        parent = self.cleaned_data.get('parent')
        ancestor = parent
        seen = set()
        while ancestor:
            if ancestor.pk == self.instance.pk or ancestor.pk in seen:
                raise forms.ValidationError('Нельзя создавать цикл родительских объектов.')
            seen.add(ancestor.pk)
            ancestor = ancestor.parent
        return parent

    def clean_code(self):
        code = self.cleaned_data['code']
        if ConstructionObject.objects.filter(project_id=self.instance.project_id, code=code).exclude(pk=self.instance.pk).exists():
            raise forms.ValidationError('Объект с таким кодом уже есть в проекте.')
        return code

    class Meta:
        model = ConstructionObject
        fields = ['code', 'name', 'parent']
        widgets = {
            'code': forms.TextInput(attrs={'class': 'form-control'}),
            'name': forms.TextInput(attrs={'class': 'form-control'}),
            'parent': forms.Select(attrs={'class': 'form-control'}),
        }


class SectionForm(forms.ModelForm):
    """Форма редактирования раздела объекта."""
    class Meta:
        model = Section
        fields = ['code', 'name']
        widgets = {
            'code': forms.TextInput(attrs={'class': 'form-control'}),
            'name': forms.TextInput(attrs={'class': 'form-control'}),
        }
        labels = {
            'code': 'Код',
            'name': 'Название',
        }

    def clean_code(self):
        code = self.cleaned_data['code']
        if self.instance.construction_object_id:
            duplicates = Section.objects.filter(
                construction_object_id=self.instance.construction_object_id,
                code=code,
            ).exclude(pk=self.instance.pk)
            if duplicates.exists():
                raise forms.ValidationError('Раздел с таким кодом уже есть на этом объекте.')
        return code
