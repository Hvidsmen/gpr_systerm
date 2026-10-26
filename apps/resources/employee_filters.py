from django import forms
from django.db import connections
from django.db.models import Q
from .employee_staffing import staffing_parts


class EmployeeFilterForm(forms.Form):
    catalogue_position = forms.IntegerField(required=False,min_value=1,widget=forms.HiddenInput)
    q = forms.CharField(label='ФИО или табельный номер', required=False, max_length=250,
        widget=forms.TextInput(attrs={'type': 'search', 'class': 'form-control', 'placeholder': 'Введите имя, фамилию или номер'}))
    position = forms.CharField(label='Должность', required=False, max_length=250,
        widget=forms.TextInput(attrs={'type': 'search', 'class': 'form-control', 'placeholder': 'Часть названия должности'}))
    include_archived = forms.BooleanField(label='Показывать уволенных / архивных', required=False,
        widget=forms.CheckboxInput(attrs={'class': 'form-check-input'}))


def filtered_employees(queryset, form):
    if not form.is_valid():
        return queryset.none()
    if form.cleaned_data.get('catalogue_position'):queryset=queryset.filter(position_id=form.cleaned_data['catalogue_position'])
    if not form.cleaned_data['include_archived']:
        queryset = queryset.filter(is_active=True)
    terms = form.cleaned_data['q'].casefold().split()
    position = form.cleaned_data['position'].casefold()
    if position or (connections[queryset.db].vendor == 'sqlite' and terms):
        # SQLite LIKE handles Latin case only. Match Cyrillic consistently with
        # other portal catalogues, while still returning a paginated queryset.
        ids = []
        for pk, full, last, first, middle, number, source_position, staffing_position, local_position in queryset.values_list(
                'pk', 'zup_full_name', 'last_name', 'first_name', 'middle_name', 'employee_number',
                'zup_position_name', 'zup_card__staffing_position', 'position__name').iterator(chunk_size=2000):
            name = ' '.join([full, last, first, middle, number]).casefold()
            job = (source_position or staffing_parts(staffing_position)[0] or local_position or '').casefold()
            if all(term in name for term in terms) and position in job:
                ids.append(pk)
        return queryset.filter(pk__in=ids)
    for term in terms:
        queryset = queryset.filter(Q(zup_full_name__icontains=term) | Q(last_name__icontains=term) |
            Q(first_name__icontains=term) | Q(middle_name__icontains=term) | Q(employee_number__icontains=term))
    return queryset
