from core.excel_mapping import ExcelAliasesField
from django import forms
from django.utils.translation import gettext_lazy as _

from .models import Employee, Brigade, EquipmentType, BrigadeGroup, BrigadeMacroGroup


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
    excel_aliases = ExcelAliasesField()
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['unit'].required = False
        for name in ('group', 'macro_group'):
            self.fields[name].queryset = self.fields[name].queryset.filter(company_id=self.instance.company_id)
            self.fields[name].widget.attrs['class'] = 'form-select'
            self.fields[name].empty_label = 'Не выбрана'

    def clean_unit(self):
        return self.cleaned_data.get('unit') or 'чел.'

    class Meta:
        model = Brigade
        fields = ['excel_aliases', 'name', 'group', 'macro_group', 'unit', 'description', 'is_active']
        widgets = {
            'code': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'БР-001'}),
            'unit': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'чел.'}),
            'name': forms.TextInput(attrs={'class': 'form-control'}),

            'description': forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
            'is_active': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }
        labels = {
            'code': 'Код должности',
            'name': 'Название',

            'description': 'Описание',
            'is_active': 'Активна',
        }

from .driver_categories import DRIVER_CATEGORY_CHOICES, DRIVER_CLASS_CHOICES


class EquipmentTypeForm(forms.ModelForm):
    excel_aliases = ExcelAliasesField()
    driver_classes = forms.MultipleChoiceField(label="Допустимые классы водителя", choices=DRIVER_CLASS_CHOICES, required=False, widget=forms.SelectMultiple(attrs={"class":"form-select"}), help_text="Квалификационный класс: 1-й, 2-й или 3-й. Можно выбрать несколько допустимых вариантов.")
    driver_categories = forms.MultipleChoiceField(label="Допустимые категории водителя", choices=DRIVER_CATEGORY_CHOICES, required=False, widget=forms.SelectMultiple(attrs={"class":"form-select"}), help_text="Можно выбрать несколько вариантов. Категории ВУ и удостоверения тракториста-машиниста указаны отдельно.")
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['category'].queryset = EquipmentCategory.objects.filter(company_id=self.instance.company_id)
        self.fields['category'].empty_label = 'Выберите категорию'
        self.fields['unit'].required = False

    def clean_unit(self):
        return self.cleaned_data.get('unit') or 'ед.'

    class Meta:
        model = EquipmentType
        fields = ['excel_aliases', 'name', 'category', 'driver_categories', 'driver_classes', 'unit', 'is_active']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control'}),
            'unit': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'ед.'}),
            'category': forms.Select(attrs={'class': 'form-select'}),
            'is_active': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }
        labels = {
            'name': 'Название',
            'category': 'Категория',
            'is_active': 'Активна',
        }

from .models import EquipmentCategory


class EquipmentCategoryForm(forms.ModelForm):
    class Meta:
        model = EquipmentCategory
        fields = ['name']
        widgets = {'name': forms.TextInput(attrs={'class': 'form-control'})}

    def __init__(self, *args, company, **kwargs):
        super().__init__(*args, **kwargs)
        self.instance.company = company

    def clean_name(self):
        name = self.cleaned_data['name']
        names = EquipmentCategory.objects.filter(company=self.instance.company).exclude(pk=self.instance.pk).values_list('name', flat=True)
        if any(existing.casefold() == name.casefold() for existing in names):
            raise forms.ValidationError('Такая категория уже есть в справочнике.')
        return name


class BrigadeCatalogForm(EquipmentCategoryForm):
    def clean_name(self):
        name = self.cleaned_data['name']
        names = type(self.instance).objects.filter(company=self.instance.company).exclude(pk=self.instance.pk).values_list('name', flat=True)
        if any(existing.casefold() == name.casefold() for existing in names):
            raise forms.ValidationError('Такая запись уже есть в справочнике.')
        return name


class BrigadeGroupForm(BrigadeCatalogForm):
    class Meta:
        model = BrigadeGroup
        fields = ['name']


class BrigadeMacroGroupForm(BrigadeCatalogForm):
    class Meta:
        model = BrigadeMacroGroup
        fields = ['name']
