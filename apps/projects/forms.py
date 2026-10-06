from django import forms
from django.utils.translation import gettext_lazy as _

from .models import Project, ConstructionObject, Section


class ProjectForm(forms.ModelForm):
    class Meta:
        model = Project
        fields = ['name', 'description', 'status', 'start_date', 'end_date']
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

    class Meta:
        model = ConstructionObject
        fields = ['name', 'parent']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control'}),
            'parent': forms.Select(attrs={'class': 'form-select'}),
        }


class SectionForm(forms.ModelForm):
    """Форма редактирования раздела объекта."""
    class Meta:
        model = Section
        fields = ['name']
        widgets = {
            'code': forms.TextInput(attrs={'class': 'form-control'}),
            'name': forms.TextInput(attrs={'class': 'form-control'}),
        }
        labels = {
            'code': 'Код',
            'name': 'Название',
        }
