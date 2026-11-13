from django import forms
from django.forms.models import ModelChoiceIteratorValue


class RemoteEmployeeMixin:
    """Render only selected employees; the browser obtains result pages remotely."""
    def __init__(self, attrs=None, choices=()):
        attrs = dict(attrs or {})
        attrs.setdefault('data-remote-search-url', '/personnel/employees/search/')
        super().__init__(attrs=attrs, choices=choices)

    def optgroups(self, name, value, attrs=None):
        field = getattr(self.choices, 'field', None)
        if field is None:
            return super().optgroups(name, value, attrs)
        selected = {str(item) for item in value if str(item)}
        subgroup = []
        index = 0
        if not self.allow_multiple_selected and not field.required:
            subgroup.append(self.create_option(name, '', field.empty_label or 'Не выбрано', not selected, index))
            index += 1
        if selected:
            for obj in field.queryset.filter(pk__in=selected):
                subgroup.append(self.create_option(
                    name, ModelChoiceIteratorValue(obj.pk, obj), field.label_from_instance(obj), True, index,
                ))
                index += 1
        return [(None, subgroup, 0)]


class RemoteEmployeeSelect(RemoteEmployeeMixin, forms.Select):
    pass


class RemoteEmployeeSelectMultiple(RemoteEmployeeMixin, forms.SelectMultiple):
    pass
