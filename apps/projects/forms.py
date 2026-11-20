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
        self.fields['parent'].label = 'Макрообъект (родитель)'
        self.fields['parent'].help_text = 'Например, для КТП-1 и КТП-2 выберите КТП. Для макрообъекта оставьте поле пустым.'
        self.fields['parent'].queryset = ConstructionObject.objects.filter(
            company_id=self.instance.company_id, project_id=self.instance.project_id,
        ).exclude(pk=self.instance.pk)
        from apps.accounts.models import UatDepartment
        self.fields['uat_departments'] = forms.ModelMultipleChoiceField(
            queryset=UatDepartment.objects.filter(
                company_id=self.instance.company_id, is_active=True,
            ).order_by('name', 'pk'),
            required=False,
            label='Подразделения УАТ',
            help_text='Можно выбрать несколько. Путевые листы этих подразделений попадут в факт объекта.',
            widget=forms.SelectMultiple(attrs={'class': 'form-select', 'size': 8}),
        )
        if self.instance.pk:
            self.fields['uat_departments'].initial = self.instance.uat_departments.filter(is_active=True)

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

    def save(self, commit=True):
        obj = super().save(commit=commit)
        selected = self.cleaned_data.get('uat_departments')

        def save_departments():
            from apps.accounts.models import UatDepartment
            selected_ids = list(selected.values_list('pk', flat=True)) if selected is not None else []
            UatDepartment.objects.filter(
                company=obj.company, construction_object=obj,
            ).exclude(pk__in=selected_ids).update(construction_object=None)
            UatDepartment.objects.filter(
                company=obj.company, pk__in=selected_ids,
            ).update(construction_object=obj)

        if commit:
            save_departments()
        else:
            original_save_m2m = self.save_m2m

            def save_m2m():
                original_save_m2m()
                save_departments()

            self.save_m2m = save_m2m
        return obj

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
