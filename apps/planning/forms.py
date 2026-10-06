from django import forms
from django.utils.translation import gettext_lazy as _

from .models import MonthlyPlan, LoadProfile, LoadProfileItem, ProductionCalendar, CalendarDay


class MonthlyPlanForm(forms.ModelForm):
    class Meta:
        model = MonthlyPlan
        fields = ['project_work', 'year', 'month', 'planned_quantity', 'start_date', 'end_date', 'mismatch_strategy']
        widgets = {
            'project_work': forms.Select(attrs={'class': 'form-control'}),
            'year': forms.NumberInput(attrs={'class': 'form-control'}),
            'month': forms.NumberInput(attrs={'class': 'form-control', 'min': 1, 'max': 12}),
            'planned_quantity': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.001'}),
            'start_date': forms.DateInput(format='%Y-%m-%d', attrs={'class': 'form-control', 'type': 'date'}),
            'end_date': forms.DateInput(format='%Y-%m-%d', attrs={'class': 'form-control', 'type': 'date'}),
            'mismatch_strategy': forms.Select(attrs={'class': 'form-control'}),
        }


class LoadProfileForm(forms.ModelForm):
    class Meta:
        model = LoadProfile
        fields = ['code', 'name', 'description']
        widgets = {
            'code': forms.TextInput(attrs={'class': 'form-control'}),
            'name': forms.TextInput(attrs={'class': 'form-control'}),
            'description': forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
        }


class LoadProfileItemForm(forms.ModelForm):
    class Meta:
        model = LoadProfileItem
        fields = ['workday_number', 'percentage']
        widgets = {
            'workday_number': forms.NumberInput(attrs={'class': 'form-control', 'min': 1}),
            'percentage': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01', 'min': 0, 'max': 100}),
        }
        labels = {
            'workday_number': _('Номер рабочего дня'),
            'percentage': _('Процент нагрузки'),
        }
        help_texts = {
            'workday_number': _('Порядковый номер рабочего дня (1, 2, 3...)'),
            'percentage': _('Доля объёма, выполняемая в этот день'),
        }


class ProductionCalendarForm(forms.ModelForm):
    class Meta:
        model = ProductionCalendar
        fields = ['code', 'name', 'year', 'is_default']
        widgets = {
            'code': forms.TextInput(attrs={'class': 'form-control'}),
            'name': forms.TextInput(attrs={'class': 'form-control'}),
            'year': forms.NumberInput(attrs={'class': 'form-control'}),
            'is_default': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }


class CalendarDayForm(forms.ModelForm):
    """Форма редактирования отдельного дня календаря."""
    class Meta:
        model = CalendarDay
        fields = ['date', 'is_working', 'is_holiday', 'is_shortened', 'note']
        widgets = {
            'date': forms.DateInput(attrs={'class': 'form-control', 'type': 'date', 'readonly': True}),
            'is_working': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'is_holiday': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'is_shortened': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'note': forms.TextInput(attrs={'class': 'form-control'}),
        }
        labels = {
            'date': _('Дата'),
            'is_working': _('Рабочий день'),
            'is_holiday': _('Праздник'),
            'is_shortened': _('Сокращённый'),
            'note': _('Примечание'),
        }
        help_texts = {
            'is_working': _('День, когда ведутся работы'),
            'is_holiday': _('Официальный нерабочий праздничный день'),
            'is_shortened': _('Предпраздничный день (на 1 час меньше)'),
        }

    def clean(self):
        cleaned_data = super().clean()
        is_working = cleaned_data.get('is_working')
        is_holiday = cleaned_data.get('is_holiday')

        # День не может быть одновременно рабочим и праздником
        if is_working and is_holiday:
            raise forms.ValidationError(
                _('День не может быть одновременно рабочим и праздничным.')
            )

        return cleaned_data


class PlanVersionForm(forms.Form):
    """Форма создания новой версии плана."""

    source_version = forms.ModelChoiceField(
        queryset=None,
        label='Исходная версия',
        help_text='Версия, на основе которой будет создана новая',
        widget=forms.Select(attrs={'class': 'form-control'})
    )
    comment = forms.CharField(
        label='Комментарий',
        required=False,
        help_text='Описание изменений в новой версии',
        widget=forms.Textarea(attrs={'class': 'form-control', 'rows': 3})
    )
    regenerate = forms.BooleanField(
        label='Перегенерировать дневные планы',
        required=False,
        initial=True,
        help_text='Если отмечено, дневные планы будут пересозданы на основе текущих параметров плана'
    )

    def __init__(self, *args, plan=None, **kwargs):
        super().__init__(*args, **kwargs)
        if plan:
            self.fields['source_version'].queryset = plan.versions.all().order_by('-version_number')


class VersionCreateForm(forms.Form):
    """Форма создания новой версии с изменением параметров распределения."""

    # Параметры плана (можно изменить)
    start_date = forms.DateField(
        label='Дата начала',
        widget=forms.DateInput(format='%Y-%m-%d', attrs={'class': 'form-control', 'type': 'date'})
    )
    end_date = forms.DateField(
        label='Дата окончания',
        widget=forms.DateInput(format='%Y-%m-%d', attrs={'class': 'form-control', 'type': 'date'})
    )
    planned_quantity = forms.DecimalField(
        label='Плановый объём',
        max_digits=15,
        decimal_places=3,
        widget=forms.NumberInput(attrs={'class': 'form-control', 'step': '0.001'})
    )
    mismatch_strategy = forms.ChoiceField(
        label='Стратегия при несовпадении дней',
        choices=[
            ('STRETCH', 'Растянуть'),
            ('COMPRESS', 'Сжать'),
            ('TRUNCATE', 'Обрезать'),
            ('STRICT', 'Строгий режим'),
        ],
        widget=forms.Select(attrs={'class': 'form-control'})
    )

    # Профили нагрузки для каждой подработы
    # Динамически добавляются в __init__

    comment = forms.CharField(
        label='Комментарий к версии',
        required=False,
        widget=forms.Textarea(attrs={'class': 'form-control', 'rows': 2})
    )

    def __init__(self, *args, plan=None, **kwargs):
        super().__init__(*args, **kwargs)
        if plan:
            # Заполняем начальные значения из плана
            self.fields['start_date'].initial = plan.start_date
            self.fields['end_date'].initial = plan.end_date
            self.fields['planned_quantity'].initial = plan.planned_quantity
            self.fields['mismatch_strategy'].initial = plan.mismatch_strategy

            # Добавляем поле профиля для каждой подработы
            work = plan.project_work
            for item in work.items.filter(load_profile__isnull=False):
                field_name = f'profile_{item.pk}'
                self.fields[field_name] = forms.ModelChoiceField(
                    queryset=LoadProfile.objects.filter(company=plan.company),
                    label=f'Профиль для "{item.name}"',
                    initial=item.load_profile,
                    required=True,
                    widget=forms.Select(attrs={'class': 'form-control'})
                )

    def clean(self):
        cleaned_data = super().clean()
        start_date = cleaned_data.get('start_date')
        end_date = cleaned_data.get('end_date')

        if start_date and end_date and start_date >= end_date:
            raise forms.ValidationError('Дата начала должна быть раньше даты окончания')

        return cleaned_data