from core.excel_mapping import ExcelAliasesField
from django import forms
from django.utils.translation import gettext_lazy as _

from .models import Employee, Brigade, EquipmentType, BrigadeGroup, BrigadeMacroGroup


class EmployeeForm(forms.ModelForm):
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        for name in ['first_name','last_name']:self.fields[name].required=True
        from django.db.models import Q
        self.fields['position'].queryset=self.fields['position'].queryset.filter(Q(planning_mapping__isnull=False,planning_mapping__brigade__merge_source__isnull=True)|Q(pk=self.instance.position_id)).exclude(Q(planning_mapping__brigade__catalogue_review__status__in=['PENDING','REJECTED','REPLACED']) & ~Q(pk=self.instance.position_id)).select_related('planning_mapping__brigade')
        self.fields['position'].label_from_instance=lambda p:p.planning_mapping.brigade.name if getattr(p,'planning_mapping',None) else p.name

    class Meta:
        model = Employee
        fields = ['first_name', 'last_name', 'middle_name', 'employee_number', 'gender', 'position', 'is_active']
        widgets = {
            'first_name': forms.TextInput(attrs={'class': 'form-control'}),
            'last_name': forms.TextInput(attrs={'class': 'form-control'}),
            'middle_name': forms.TextInput(attrs={'class': 'form-control'}),
            'gender': forms.Select(attrs={'class': 'form-select'}),
            'position': forms.Select(attrs={'class': 'form-control'}),
            'is_active': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }

class BrigadeForm(forms.ModelForm):
    excel_aliases = ExcelAliasesField(label='Ассоциации из Excel',
        help_text='По одному названию в строке. Эти варианты используются при импорте Excel и в поиске. Основное название также находится автоматически.')
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['unit'].required = False
        for name in ('group',):
            from .group_order import ordered
            self.fields[name].queryset = ordered(self.fields[name].queryset.filter(company_id=self.instance.company_id))
            self.fields[name].widget.attrs['class'] = 'form-select'
            self.fields[name].empty_label = 'Не выбрана'

    def clean_unit(self):
        return self.cleaned_data.get('unit') or 'чел.'

    class Meta:
        model = Brigade
        fields = ['excel_aliases', 'name', 'group', 'unit', 'description', 'is_active']
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
    excel_aliases = ExcelAliasesField(label='Ассоциации из Excel',
        help_text='По одному названию в строке. Эти варианты используются при импорте Excel и в поиске. Основное название также находится автоматически.')
    driver_classes = forms.MultipleChoiceField(label="Допустимые классы водителя", choices=DRIVER_CLASS_CHOICES, required=False, widget=forms.SelectMultiple(attrs={"class":"form-select"}), help_text="Квалификационный класс: 1-й, 2-й или 3-й. Можно выбрать несколько допустимых вариантов.")
    driver_categories = forms.MultipleChoiceField(label="Допустимые категории водителя", choices=DRIVER_CATEGORY_CHOICES, required=False, widget=forms.SelectMultiple(attrs={"class":"form-select"}), help_text="Можно выбрать несколько вариантов. Категории ВУ и удостоверения тракториста-машиниста указаны отдельно.")
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from .group_order import ordered
        self.fields['category'].queryset =ordered(EquipmentCategory.objects.filter(company_id=self.instance.company_id),kind='equipment')
        self.fields['category'].empty_label = 'Выберите группу техники'
        self.fields['category'].widget.attrs['data-equipment-group-select']=''
        self.inline_groups=self.fields['category'].queryset
        self.fields['parent'].queryset = EquipmentType.objects.filter(company_id=self.instance.company_id,parent__isnull=True,merge_source__isnull=True).exclude(pk=self.instance.pk)
        self.parent_group_options=list(self.fields['parent'].queryset.values('id','category_id'))
        parent_id=self.data.get(self.add_prefix('parent')) if self.is_bound else self.initial.get('parent',self.instance.parent_id)
        inherited=self.fields['parent'].queryset.filter(pk=parent_id).first() if str(parent_id or '').isdigit() else None
        if inherited:
            self.initial['category']=inherited.category_id
            self.fields['category'].disabled=True
        self.fields['category'].help_text='У моделей группа наследуется от техники для планирования. Изменение группы родителя применяется ко всем моделям и машинам.'
        self.fields['parent'].empty_label = 'Отдельная позиция в ГПР и СМГ'
        self.fields['parent'].help_text = 'Для модели, например «CAT 320», выберите плановую позицию «Экскаватор». Если добавляете саму технику для ГПР и СМГ, оставьте поле пустым.'
        self.fields['unit'].required = False

    def clean_unit(self):
        return self.cleaned_data.get('unit') or 'ед.'

    def clean_parent(self):
        parent=self.cleaned_data.get('parent')
        if parent and self.instance.pk and self.instance.model_variants.exists():
            raise forms.ValidationError('К этой плановой позиции уже привязаны модели. Сначала перенесите их к другой технике для планирования.')
        return parent

    def clean(self):
        data=super().clean()
        if data.get('parent'):data['category']=data['parent'].category
        return data

    class Meta:
        model = EquipmentType
        fields = ['excel_aliases', 'name', 'brand_model', 'parent', 'category', 'driver_categories', 'driver_classes', 'unit', 'is_active']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control'}),
            'brand_model': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Например, CAT 320'}),
            'unit': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'ед.'}),
            'category': forms.Select(attrs={'class': 'form-select'}),
            'is_active': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }
        labels = {
            'name': 'Название',
            'parent': 'Техника для планирования',
            'category': 'Группа техники',
            'is_active': 'Активна',
        }

from .models import EquipmentCategory


class EquipmentCategoryForm(forms.ModelForm):
    sort_order = forms.IntegerField(label='Порядок',min_value=0,max_value=1000000,required=False,
        help_text='Меньшее число — выше в списке. Используется во всех разделах портала.',widget=forms.NumberInput(attrs={'class':'form-control'}))

    def clean_sort_order(self):
        if self.cleaned_data.get('sort_order') is not None:
            return self.cleaned_data['sort_order']
        if self.instance._state.adding:
            from .group_order import default_priority
            return default_priority(self.cleaned_data.get('name'),'equipment' if isinstance(self.instance,EquipmentCategory) else 'labor')
        return self.instance.sort_order

    class Meta:
        model = EquipmentCategory
        fields = ['name', 'sort_order']
        widgets = {'name': forms.TextInput(attrs={'class': 'form-control'})}

    def __init__(self, *args, company, **kwargs):
        super().__init__(*args, **kwargs)
        self.instance.company = company

    def clean_name(self):
        name = self.cleaned_data['name']
        names = EquipmentCategory.objects.filter(company=self.instance.company).exclude(pk=self.instance.pk).values_list('name', flat=True)
        if any(existing.casefold() == name.casefold() for existing in names):
            raise forms.ValidationError('Такая группа техники уже есть в справочнике.')
        return name


class BrigadeCatalogForm(EquipmentCategoryForm):
    def clean_name(self):
        name = self.cleaned_data['name']
        names = type(self.instance).objects.filter(company=self.instance.company).exclude(pk=self.instance.pk).values_list('name', flat=True)
        if any(existing.casefold() == name.casefold() for existing in names):
            raise forms.ValidationError('Такая запись уже есть в справочнике.')
        return name


class BrigadeGroupForm(BrigadeCatalogForm):
    staffing_mode_present = forms.CharField(initial='1', widget=forms.HiddenInput, required=False)

    def clean_is_general_staff(self):
        if not self.data.get('staffing_mode_present'):
            return self.instance.is_general_staff
        return self.cleaned_data['is_general_staff']

    class Meta:
        model = BrigadeGroup
        fields = ['name', 'sort_order', 'is_general_staff']


class BrigadeMacroGroupForm(BrigadeCatalogForm):
    sort_order = None
    class Meta:
        model = BrigadeMacroGroup
        fields = ['name']
