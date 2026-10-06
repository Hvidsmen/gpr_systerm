from django import forms
from django.utils.translation import gettext_lazy as _

from .models import Employee, Brigade,EquipmentType


class EmployeeForm(forms.ModelForm):
    class Meta:
        model = Employee
        fields = ['first_name', 'last_name', 'middle_name', 'position', 'is_active']
        widgets = {
            'first_name': forms.TextInput(attrs={'class': 'form-control'}),
            'last_name': forms.TextInput(attrs={'class': 'form-control'}),
            'middle_name': forms.TextInput(attrs={'class': 'form-control'}),
            'position': forms.Select(attrs={'class': 'form-control'}),
            'is_active': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }

class BrigadeForm(forms.ModelForm):
    class Meta:
        model = Brigade
        fields = ['code', 'name',  'description', 'is_active']
        widgets = {
            'code': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'БР-001'}),
            'name': forms.TextInput(attrs={'class': 'form-control'}),

            'description': forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
            'is_active': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }
        labels = {
            'code': 'Код бригады',
            'name': 'Название',

            'description': 'Описание',
            'is_active': 'Активна',
        }

class EquipmentTypeForm(forms.ModelForm):
    class Meta:
        model = EquipmentType
        fields = ['name', 'category', 'is_active']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control'}),
            'category': forms.TextInput(attrs={'class': 'form-control'}),
            'is_active': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }
        labels = {
            'name': 'Название',
            'category': 'Категория',
            'is_active': 'Активна',
        }