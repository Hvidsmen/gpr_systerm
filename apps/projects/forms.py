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
