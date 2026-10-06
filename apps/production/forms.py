"""
Формы модуля производства.
"""
from django import forms
from .models import DailyFact, LaborFact, EquipmentFact, FuelFact, LaborPlan,EquipmentPlan,FuelPlan


class DailyFactForm(forms.ModelForm):
    class Meta:
        model = DailyFact
        fields = ['project_work', 'work_item', 'date', 'actual_quantity',
                  'deviation_reason', 'comment']
        widgets = {
            'project_work': forms.Select(attrs={'class': 'form-control'}),
            'work_item': forms.Select(attrs={'class': 'form-control'}),
            'date': forms.DateInput(attrs={'class': 'form-control', 'type': 'date'}),
            'actual_quantity': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.001'}),
            'deviation_reason': forms.Select(attrs={'class': 'form-control'}),
            'comment': forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
        }

class LaborFactForm(forms.ModelForm):
    class Meta:
        model = LaborFact
        fields = [
            'project', 'project_work',
            'date', 'brigade',
            'actual_workers',
            'actual_hours',
            'hourly_rate', 'comment'
        ]
        widgets = {
            'project': forms.Select(attrs={'class': 'form-control'}),
            'project_work': forms.Select(attrs={'class': 'form-control'}),
            'date': forms.DateInput(attrs={'class': 'form-control', 'type': 'date'}),
            'brigade': forms.Select(attrs={'class': 'form-control'}),
            'actual_workers': forms.NumberInput(attrs={'class': 'form-control'}),
            'actual_hours': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.5'}),
            'hourly_rate': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}),
            'comment': forms.Textarea(attrs={'class': 'form-control', 'rows': 2}),
        }
        labels = {
            'project': 'Проект',
            'project_work': 'Работа',
            'date': 'Дата',
            'brigade': 'Бригада',
            'actual_workers': 'Факт, чел',
            'actual_hours': 'Факт, чел-час',
            'hourly_rate': 'Ставка, ₽/час',
            'comment': 'Комментарий',
        }


class EquipmentPlanForm(forms.ModelForm):
    class Meta:
        model = EquipmentPlan
        fields = ['project', 'project_work', 'date', 'equipment_type', 'equipment_number',
                  'planned_count', 'planned_machine_hours', 'hourly_rate', 'comment']
        widgets = {
            'project': forms.Select(attrs={'class': 'form-control'}),
            'project_work': forms.Select(attrs={'class': 'form-control'}),
            'date': forms.DateInput(attrs={'class': 'form-control', 'type': 'date'}),
            'equipment_type': forms.Select(attrs={'class': 'form-control'}),  # ← Select вместо TextInput
            'equipment_number': forms.TextInput(attrs={'class': 'form-control'}),
            'planned_count': forms.NumberInput(attrs={'class': 'form-control'}),
            'planned_machine_hours': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.5'}),
            'hourly_rate': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}),
            'comment': forms.Textarea(attrs={'class': 'form-control', 'rows': 2}),
        }
        labels = {
            'project': 'Проект',
            'project_work': 'Работа',
            'date': 'Дата',
            'equipment_type': 'Вид техники',
            'equipment_number': 'Гос. номер / инв. №',
            'planned_count': 'План, ед',
            'planned_machine_hours': 'План, маш-час',
            'hourly_rate': 'Ставка, ₽/маш-час',
            'comment': 'Комментарий',
        }


class EquipmentFactForm(forms.ModelForm):
    class Meta:
        model = EquipmentFact
        fields = ['project', 'project_work', 'date', 'equipment_type', 'equipment_number',
                  'actual_count', 'machine_hours', 'hourly_rate', 'comment']
        widgets = {
            'project': forms.Select(attrs={'class': 'form-control'}),
            'project_work': forms.Select(attrs={'class': 'form-control'}),
            'date': forms.DateInput(attrs={'class': 'form-control', 'type': 'date'}),
            'equipment_type': forms.Select(attrs={'class': 'form-control'}),  # ← Select вместо TextInput
            'equipment_number': forms.TextInput(attrs={'class': 'form-control'}),
            'actual_count': forms.NumberInput(attrs={'class': 'form-control'}),
            'machine_hours': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.5'}),
            'hourly_rate': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}),
            'comment': forms.Textarea(attrs={'class': 'form-control', 'rows': 2}),
        }
        labels = {
            'project': 'Проект',
            'project_work': 'Работа',
            'date': 'Дата',
            'equipment_type': 'Вид техники',
            'equipment_number': 'Гос. номер / инв. №',
            'actual_count': 'Факт, ед',
            'machine_hours': 'Факт, маш-час',
            'hourly_rate': 'Ставка, ₽/маш-час',
            'comment': 'Комментарий',
        }


class EquipmentPlanRangeForm(forms.Form):
    """Форма для создания плана по технике на ДИАПАЗОН дат."""
    project = forms.ModelChoiceField(
        queryset=None,
        label='Проект',
        widget=forms.Select(attrs={'class': 'form-control'})
    )
    project_work = forms.ModelChoiceField(
        queryset=None,
        label='Работа (необязательно)',
        required=False,
        widget=forms.Select(attrs={'class': 'form-control'})
    )
    equipment_type = forms.ModelChoiceField(  # ← ИЗМЕНИЛИ на ModelChoiceField
        queryset=None,
        label='Вид техники',
        widget=forms.Select(attrs={'class': 'form-control'})
    )
    equipment_number = forms.CharField(
        label='Гос. номер / инв. №',
        required=False,
        widget=forms.TextInput(attrs={'class': 'form-control'})
    )
    date_start = forms.DateField(
        label='Дата начала',
        widget=forms.DateInput(attrs={'class': 'form-control', 'type': 'date'})
    )
    date_end = forms.DateField(
        label='Дата окончания',
        widget=forms.DateInput(attrs={'class': 'form-control', 'type': 'date'})
    )
    planned_count = forms.IntegerField(
        label='Количество единиц',
        widget=forms.NumberInput(attrs={'class': 'form-control'})
    )
    planned_machine_hours = forms.DecimalField(
        label='Маш-часы (необязательно)',
        required=False,
        widget=forms.NumberInput(attrs={'class': 'form-control', 'step': '0.5'})
    )
    hourly_rate = forms.DecimalField(
        label='Ставка, ₽/маш-час (необязательно)',
        required=False,
        widget=forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'})
    )
    comment = forms.CharField(
        label='Комментарий',
        required=False,
        widget=forms.Textarea(attrs={'class': 'form-control', 'rows': 2})
    )

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        if user and hasattr(user, 'company'):
            from apps.projects.models import Project
            from apps.works.models import ProjectWork
            from apps.resources.models import EquipmentType

            self.fields['project'].queryset = Project.objects.filter(company=user.company)
            self.fields['project_work'].queryset = ProjectWork.objects.filter(company=user.company)
            self.fields['equipment_type'].queryset = EquipmentType.objects.filter(
                company=user.company, is_active=True
            )

    def clean(self):
        cleaned_data = super().clean()
        date_start = cleaned_data.get('date_start')
        date_end = cleaned_data.get('date_end')

        if date_start and date_end and date_start > date_end:
            raise forms.ValidationError('Дата начала не может быть позже даты окончания')

        return cleaned_data

class LaborPlanForm(forms.ModelForm):
    class Meta:
        model = LaborPlan
        fields = ['project', 'project_work', 'date', 'brigade',
                  'planned_workers', 'planned_hours', 'hourly_rate', 'comment']
        widgets = {
            'project': forms.Select(attrs={'class': 'form-control'}),
            'project_work': forms.Select(attrs={'class': 'form-control'}),
            'date': forms.DateInput(attrs={'class': 'form-control', 'type': 'date'}),
            'brigade': forms.Select(attrs={'class': 'form-control'}),
            'planned_workers': forms.NumberInput(attrs={'class': 'form-control'}),
            'planned_hours': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.5'}),
            'hourly_rate': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}),
            'comment': forms.Textarea(attrs={'class': 'form-control', 'rows': 2}),
        }
        labels = {
            'project': 'Проект',
            'project_work': 'Работа',
            'date': 'Дата',
            'brigade': 'Бригада',
            'planned_workers': 'План, чел',
            'planned_hours': 'План, чел-час',
            'hourly_rate': 'Ставка, ₽/час',
            'comment': 'Комментарий',
        }


class LaborPlanRangeForm(forms.Form):
    """Форма для создания плана по людям на ДИАПАЗОН дат."""
    project = forms.ModelChoiceField(
        queryset=None,
        label='Проект',
        widget=forms.Select(attrs={'class': 'form-control'})
    )
    project_work = forms.ModelChoiceField(
        queryset=None,
        label='Работа (необязательно)',
        required=False,  # ← СДЕЛАЛИ НЕОБЯЗАТЕЛЬНЫМ
        widget=forms.Select(attrs={'class': 'form-control'})
    )
    brigade = forms.ModelChoiceField(
        queryset=None,
        label='Бригада',
        widget=forms.Select(attrs={'class': 'form-control'})
    )
    date_start = forms.DateField(
        label='Дата начала',
        widget=forms.DateInput(attrs={'class': 'form-control', 'type': 'date'})
    )
    date_end = forms.DateField(
        label='Дата окончания',
        widget=forms.DateInput(attrs={'class': 'form-control', 'type': 'date'})
    )
    planned_workers = forms.IntegerField(
        label='Количество человек',
        widget=forms.NumberInput(attrs={'class': 'form-control'})
    )
    planned_hours = forms.DecimalField(
        label='Чел-часы (необязательно)',
        required=False,
        widget=forms.NumberInput(attrs={'class': 'form-control', 'step': '0.5'})
    )
    hourly_rate = forms.DecimalField(
        label='Ставка, ₽/час (необязательно)',
        required=False,
        widget=forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'})
    )
    comment = forms.CharField(
        label='Комментарий',
        required=False,
        widget=forms.Textarea(attrs={'class': 'form-control', 'rows': 2})
    )

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        if user and hasattr(user, 'company'):
            from apps.projects.models import Project
            from apps.works.models import ProjectWork
            from apps.resources.models import Brigade

            self.fields['project'].queryset = Project.objects.filter(company=user.company)
            self.fields['project_work'].queryset = ProjectWork.objects.filter(company=user.company)
            self.fields['brigade'].queryset = Brigade.objects.filter(company=user.company, is_active=True)

    def clean(self):
        cleaned_data = super().clean()
        date_start = cleaned_data.get('date_start')
        date_end = cleaned_data.get('date_end')

        if date_start and date_end and date_start > date_end:
            raise forms.ValidationError('Дата начала не может быть позже даты окончания')

        return cleaned_data


class FuelPlanForm(forms.ModelForm):
    """Форма плана по ГСМ на один день."""

    class Meta:
        model = FuelPlan
        fields = ['project', 'project_work', 'date', 'fuel_type',
                  'planned_liters', 'price_per_liter', 'equipment_ref', 'comment']
        widgets = {
            'project': forms.Select(attrs={'class': 'form-control'}),
            'project_work': forms.Select(attrs={'class': 'form-control'}),
            'date': forms.DateInput(attrs={'class': 'form-control', 'type': 'date'}),
            'fuel_type': forms.Select(attrs={'class': 'form-control'}),
            'planned_liters': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.1'}),
            'price_per_liter': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}),
            'equipment_ref': forms.TextInput(attrs={'class': 'form-control'}),
            'comment': forms.Textarea(attrs={'class': 'form-control', 'rows': 2}),
        }
        labels = {
            'project': 'Проект',
            'project_work': 'Работа',
            'date': 'Дата',
            'fuel_type': 'Вид ГСМ',
            'planned_liters': 'План, л',
            'price_per_liter': 'Цена за литр, ₽',
            'equipment_ref': 'Привязка к технике',
            'comment': 'Комментарий',
        }




class FuelFactForm(forms.ModelForm):
    """Форма факта по ГСМ."""

    class Meta:
        model = FuelFact
        fields = ['project', 'project_work', 'date', 'fuel_type',
                  'actual_liters', 'price_per_liter', 'equipment_ref', 'comment']
        widgets = {
            'project': forms.Select(attrs={'class': 'form-control'}),
            'project_work': forms.Select(attrs={'class': 'form-control'}),
            'date': forms.DateInput(attrs={'class': 'form-control', 'type': 'date'}),
            'fuel_type': forms.Select(attrs={'class': 'form-control'}),
            'actual_liters': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.1'}),
            'price_per_liter': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}),
            'equipment_ref': forms.TextInput(attrs={'class': 'form-control'}),
            'comment': forms.Textarea(attrs={'class': 'form-control', 'rows': 2}),
        }
        labels = {
            'project': 'Проект',
            'project_work': 'Работа',
            'date': 'Дата',
            'fuel_type': 'Вид ГСМ',
            'actual_liters': 'Факт, л',
            'price_per_liter': 'Цена за литр, ₽',
            'equipment_ref': 'Привязка к технике',
            'comment': 'Комментарий',
        }


class FuelPlanRangeForm(forms.Form):
    """Форма для создания плана по ГСМ на ДИАПАЗОН дат."""
    project = forms.ModelChoiceField(
        queryset=None,
        label='Проект (необязательно)',
        required=False,
        widget=forms.Select(attrs={'class': 'form-control'})
    )
    project_work = forms.ModelChoiceField(
        queryset=None,
        label='Работа (необязательно)',
        required=False,
        widget=forms.Select(attrs={'class': 'form-control'})
    )
    fuel_type = forms.ChoiceField(
        label='Вид ГСМ',
        choices=[
            ('DIESEL', 'Дизельное топливо'),
            ('PETROL_92', 'Бензин АИ-92'),
            ('PETROL_95', 'Бензин АИ-95'),
            ('PETROL_98', 'Бензин АИ-98'),
            ('GAS', 'Газ (пропан/метан)'),
            ('OIL', 'Масло моторное'),
            ('OTHER', 'Другое'),
        ],
        widget=forms.Select(attrs={'class': 'form-control'})
    )
    date_start = forms.DateField(
        label='Дата начала',
        widget=forms.DateInput(attrs={'class': 'form-control', 'type': 'date'})
    )
    date_end = forms.DateField(
        label='Дата окончания',
        widget=forms.DateInput(attrs={'class': 'form-control', 'type': 'date'})
    )
    planned_liters = forms.DecimalField(
        label='План, л',
        widget=forms.NumberInput(attrs={'class': 'form-control', 'step': '0.1'})
    )
    price_per_liter = forms.DecimalField(
        label='Цена за литр, ₽ (необязательно)',
        required=False,
        widget=forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'})
    )
    equipment_ref = forms.CharField(
        label='Привязка к технике',
        required=False,
        widget=forms.TextInput(attrs={'class': 'form-control'})
    )
    comment = forms.CharField(
        label='Комментарий',
        required=False,
        widget=forms.Textarea(attrs={'class': 'form-control', 'rows': 2})
    )

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        if user and hasattr(user, 'company'):
            from apps.projects.models import Project
            from apps.works.models import ProjectWork

            self.fields['project'].queryset = Project.objects.filter(company=user.company)
            self.fields['project_work'].queryset = ProjectWork.objects.filter(company=user.company)

    def clean(self):
        cleaned_data = super().clean()
        date_start = cleaned_data.get('date_start')
        date_end = cleaned_data.get('date_end')

        if date_start and date_end and date_start > date_end:
            raise forms.ValidationError('Дата начала не может быть позже даты окончания')

        return cleaned_data


class LaborFactDailyForm(forms.Form):
    """Форма выбора параметров для массового ввода факта по людям."""
    date = forms.DateField(
        label='Дата',
        widget=forms.DateInput(attrs={'class': 'form-control', 'type': 'date'})
    )
    project = forms.ModelChoiceField(
        queryset=None,
        label='Проект',
        required=False,
        widget=forms.Select(attrs={'class': 'form-control'})
    )
    project_work = forms.ModelChoiceField(
        queryset=None,
        label='Работа',
        required=False,
        widget=forms.Select(attrs={'class': 'form-control'})
    )

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        if user and hasattr(user, 'company'):
            from apps.projects.models import Project
            from apps.works.models import ProjectWork

            self.fields['project'].queryset = Project.objects.filter(company=user.company)
            self.fields['project_work'].queryset = ProjectWork.objects.filter(company=user.company)


class EquipmentFactDailyForm(forms.Form):
    """Форма выбора параметров для массового ввода факта по технике."""
    date = forms.DateField(
        label='Дата',
        widget=forms.DateInput(attrs={'class': 'form-control', 'type': 'date'})
    )
    project = forms.ModelChoiceField(
        queryset=None,
        label='Проект',
        required=False,
        widget=forms.Select(attrs={'class': 'form-control'})
    )
    project_work = forms.ModelChoiceField(
        queryset=None,
        label='Работа',
        required=False,
        widget=forms.Select(attrs={'class': 'form-control'})
    )
    equipment_type = forms.ModelChoiceField(
        queryset=None,
        label='Вид техники',
        required=False,
        widget=forms.Select(attrs={'class': 'form-control'})
    )

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        if user and hasattr(user, 'company'):
            from apps.projects.models import Project
            from apps.works.models import ProjectWork
            from apps.resources.models import EquipmentType

            self.fields['project'].queryset = Project.objects.filter(company=user.company)
            self.fields['project_work'].queryset = ProjectWork.objects.filter(company=user.company)
            self.fields['equipment_type'].queryset = EquipmentType.objects.filter(
                company=user.company, is_active=True
            )


class FuelFactDailyForm(forms.Form):
    """Форма выбора параметров для массового ввода факта по ГСМ."""
    date = forms.DateField(
        label='Дата',
        widget=forms.DateInput(attrs={'class': 'form-control', 'type': 'date'})
    )
    project = forms.ModelChoiceField(
        queryset=None,
        label='Проект',
        required=False,
        widget=forms.Select(attrs={'class': 'form-control'})
    )
    project_work = forms.ModelChoiceField(
        queryset=None,
        label='Работа',
        required=False,
        widget=forms.Select(attrs={'class': 'form-control'})
    )
    fuel_type = forms.ChoiceField(
        label='Вид ГСМ',
        required=False,
        choices=[('', '— Все виды —')] + FuelFact.FUEL_TYPE_CHOICES,
        widget=forms.Select(attrs={'class': 'form-control'})
    )

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        if user and hasattr(user, 'company'):
            from apps.projects.models import Project
            from apps.works.models import ProjectWork

            self.fields['project'].queryset = Project.objects.filter(company=user.company)
            self.fields['project_work'].queryset = ProjectWork.objects.filter(company=user.company)


class FactInputFilterForm(forms.Form):
    """Форма выбора параметров для ввода факта работ."""
    date = forms.DateField(
        label='Дата',
        widget=forms.DateInput(attrs={'class': 'form-control', 'type': 'date'})
    )
    project = forms.ModelChoiceField(
        label='Проект',
        required=False,
        queryset=None,
        widget=forms.Select(attrs={'class': 'form-control'})
    )
    construction_object = forms.ModelChoiceField(
        label='Объект',
        required=False,
        queryset=None,
        widget=forms.Select(attrs={'class': 'form-control'})
    )
    section = forms.ModelChoiceField(
        label='Раздел',
        required=False,
        queryset=None,
        widget=forms.Select(attrs={'class': 'form-control'})
    )

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        if user and hasattr(user, 'company'):
            from apps.projects.models import Project, ConstructionObject, Section
            self.fields['project'].queryset = Project.objects.filter(
                company=user.company
            ).order_by('name')
            self.fields['construction_object'].queryset = ConstructionObject.objects.filter(
                company=user.company
            ).order_by('name')
            self.fields['section'].queryset = Section.objects.filter(
                company=user.company
            ).order_by('name')