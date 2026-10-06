"""
Общие утилиты и миксины для модуля производства.
"""
from django.utils.dateparse import parse_date


def setup_resource_form(form, company):
    """Limit resource choices consistently for plan and fact forms."""
    for name in ('project', 'project_work', 'brigade', 'equipment_type'):
        if name in form.fields:
            field = form.fields[name]
            field.queryset = field.queryset.filter(company=company)
            if name in ('brigade', 'equipment_type'):
                field.queryset = field.queryset.filter(is_active=True)


def parse_date_safe(date_str):
    """Безопасный парсинг даты. Возвращает None при ошибке."""
    if not date_str:
        return None
    try:
        return parse_date(date_str)
    except (ValueError, TypeError):
        return None


def parse_fk_id(value):
    """
    Преобразует значение FK из строки в int или None.
    Решает проблему 'Field id expected a number but got ""'.
    """
    if value is None:
        return None
    if isinstance(value, int):
        return value
    value = str(value).strip()
    if not value:
        return None
    try:
        return int(value)
    except (ValueError, TypeError):
        return None


def build_update_data(request, fields_config):
    """
    Собирает dict для queryset.update() из POST-данных.

    fields_config: список кортежей (field_name, converter)
    Например: [('actual_workers', lambda v: int(v) if v else None)]
    """
    update_data = {}
    for field, converter in fields_config:
        value = request.POST.get(field)
        if value is not None and value != '':
            update_data[field] = converter(value)
    return update_data


def apply_filters(queryset, request, filter_map):
    """
    Применяет фильтры из GET-параметров к queryset.

    filter_map: dict {param_name: field_name}
    Например: {'project': 'project_id', 'brigade': 'brigade_id__in'}
    """
    for param, field in filter_map.items():
        value = request.GET.get(param)
        if value:
            if field.endswith('__in'):
                values = request.GET.getlist(param)
                if values:
                    queryset = queryset.filter(**{field: values})
            else:
                queryset = queryset.filter(**{field: value})
    return queryset


def build_matrix(items, row_key_func, row_data_func, date_field='date'):
    """
    Универсальное построение матрицы.

    items: queryset объектов
    row_key_func(item) -> строковый ключ строки
    row_data_func(item) -> dict с данными строки (brigade, project_work и т.д.)
    """
    rows = {}
    dates_set = set()

    for item in items:
        row_key = row_key_func(item)
        if row_key not in rows:
            rows[row_key] = {**row_data_func(item), 'items_by_date': {}}
        date_value = getattr(item, date_field)
        rows[row_key]['items_by_date'][date_value.isoformat()] = item
        dates_set.add(date_value)

    sorted_dates = sorted(dates_set)
    return rows, sorted_dates
