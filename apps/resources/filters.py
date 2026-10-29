from django import forms
from django.db import connections
from .models import BrigadeGroup, BrigadeMacroGroup, EquipmentCategory, EquipmentType


class CatalogFilterForm(forms.Form):
    name = forms.CharField(label='Название', required=False, max_length=150,
        widget=forms.TextInput(attrs={'type':'search','placeholder':'Часть названия'}))

    def __init__(self, *args, company, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            if isinstance(field, forms.ModelChoiceField):
                field.queryset = field.queryset.filter(company=company)
                if field.queryset.model in {BrigadeGroup,EquipmentCategory}:
                    from .group_order import ordered
                    field.queryset=ordered(field.queryset,kind='equipment' if field.queryset.model is EquipmentCategory else 'labor')
                field.widget.attrs['class'] = 'form-select form-select-sm'
            else:
                field.widget.attrs['class'] = 'form-control form-control-sm'


class BrigadeFilterForm(CatalogFilterForm):
    staffing_current = forms.ChoiceField(label='Штатное расписание ЗУП', required=False,
        choices=[('', 'Все должности'), ('yes', 'Актуальные'), ('no', 'Неактуальные'), ('unknown', 'Не получено из ЗУП')])
    group = forms.ModelChoiceField(label='Группа', queryset=BrigadeGroup.objects.all(), required=False, empty_label='Все группы')
    active_employees = forms.ChoiceField(label='Активные сотрудники', required=False,
        choices=[('', 'Все должности'), ('yes', 'Есть активные'), ('no', 'Нет активных')])

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['active_employees'].widget.attrs['class'] = 'form-select form-select-sm'


class EquipmentFilterForm(CatalogFilterForm):
    parent = forms.ModelChoiceField(label='Техника для планирования', queryset=EquipmentType.objects.filter(parent__isnull=True,merge_source__isnull=True), required=False, empty_label='Вся техника для планирования')
    category = forms.ModelChoiceField(label='Группа техники', queryset=EquipmentCategory.objects.all(), required=False, empty_label='Все группы техники')
    is_active = forms.ChoiceField(label='Статус',required=False,choices=[('','Все статусы'),('True','Активные'),('False','Неактивные')])

    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        self.fields['name'].widget.attrs['placeholder']='Название техники или модели'
        self.fields['is_active'].widget.attrs['class']='form-select form-select-sm'


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
            elif key == 'parent':
                from django.db.models import Q
                queryset = queryset.filter(Q(pk=value.pk)|Q(parent=value))
            elif key == 'category' and queryset.model is EquipmentType:
                from django.db.models import Q
                queryset = queryset.filter(Q(category=value)|Q(category__isnull=True,parent__category=value))
            elif key == 'active_employees':
                queryset = queryset.filter(active_employee_count__gt=0) if value == 'yes' else queryset.filter(active_employee_count=0)
            elif key == 'staffing_current':
                queryset = queryset.filter(staffing_current__isnull=True) if value == 'unknown' else queryset.filter(staffing_current=value=='yes')
            else:
                queryset = queryset.filter(**{key:value})
        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['catalogue_count']=context['paginator'].count if context['paginator'] else self.object_list.count()
        params = self.request.GET.copy()
        params.pop('page', None)
        context.update(filter_form=self.filter_form, filter_query=params.urlencode(), export_route=self.export_route,
            filters_active=any(self.request.GET.get(key) for key in self.filter_form.fields))
        return context
