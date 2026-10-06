from django import forms
from django.db import connections
from .models import BrigadeGroup, BrigadeMacroGroup, EquipmentCategory


class CatalogFilterForm(forms.Form):
    name = forms.CharField(label='Название', required=False, max_length=150,
        widget=forms.TextInput(attrs={'type':'search','placeholder':'Часть названия'}))

    def __init__(self, *args, company, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            if isinstance(field, forms.ModelChoiceField):
                field.queryset = field.queryset.filter(company=company)
                field.widget.attrs['class'] = 'form-select form-select-sm'
            else:
                field.widget.attrs['class'] = 'form-control form-control-sm'


class BrigadeFilterForm(CatalogFilterForm):
    group = forms.ModelChoiceField(label='Группа', queryset=BrigadeGroup.objects.all(), required=False, empty_label='Все группы')
    macro_group = forms.ModelChoiceField(label='Макрогруппа', queryset=BrigadeMacroGroup.objects.all(), required=False, empty_label='Все макрогруппы')


class EquipmentFilterForm(CatalogFilterForm):
    category = forms.ModelChoiceField(label='Категория', queryset=EquipmentCategory.objects.all(), required=False, empty_label='Все категории')


class CatalogFilterMixin:
    filter_form_class = None

    def filter_queryset(self, queryset):
        self.filter_form = self.filter_form_class(self.request.GET, company=self.get_company())
        if not self.filter_form.is_valid():
            return queryset.none()
        for key, value in self.filter_form.cleaned_data.items():
            if not value:
                continue
            if key == 'name':
                if connections[queryset.db].vendor == 'sqlite':
                    # SQLite's LIKE does not fold Cyrillic characters.
                    ids = [pk for pk, name in queryset.values_list('pk','name') if value.casefold() in name.casefold()]
                    queryset = queryset.filter(pk__in=ids)
                else:
                    queryset = queryset.filter(name__icontains=value)
            else:
                queryset = queryset.filter(**{key:value})
        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        params = self.request.GET.copy()
        params.pop('page', None)
        context.update(filter_form=self.filter_form, filter_query=params.urlencode(), export_route=self.export_route,
            filters_active=any(self.request.GET.get(key) for key in self.filter_form.fields))
        return context
