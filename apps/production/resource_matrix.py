"""Dated resource journals, keeping object and resource identities separate."""
from collections import OrderedDict
from decimal import Decimal
from django import forms
from core.permissions import scope_queryset
from .fact_matrix import FactMatrixFilterForm


class ResourceMatrixFilterForm(FactMatrixFilterForm):
    def __init__(self, *args, user, model, identities, **kwargs):
        super().__init__(*args, user=user, **kwargs)
        del self.fields['work']
        for name in identities:
            field = model._meta.get_field(name)
            if field.is_relation:
                self.fields[name] = forms.ModelChoiceField(
                    queryset=scope_queryset(field.remote_field.model.objects.all(), user),
                    required=False, label=field.verbose_name,
                    widget=forms.Select(attrs={'class': 'form-select form-select-sm'}),
                )
            elif field.choices:
                self.fields[name] = forms.ChoiceField(
                    choices=[('', 'Все')] + list(field.choices), required=False,
                    label=field.verbose_name,
                    widget=forms.Select(attrs={'class': 'form-select form-select-sm'}),
                )
            else:
                self.fields[name] = forms.CharField(required=False, label=field.verbose_name,
                    widget=forms.TextInput(attrs={'class': 'form-control form-control-sm'}))


def resource_rows(records, days, identities, field_names, quantity_field, prefix):
    from django.urls import reverse
    rows = OrderedDict()
    metrics = [name for name in field_names if name not in identities]
    for record in records:
        identity = (record.construction_object_id, *(getattr(record, name + '_id', getattr(record, name)) for name in identities))
        if identity not in rows:
            labels = [str(getattr(record, 'get_' + name + '_display')())
                      if record._meta.get_field(name).choices else str(getattr(record, name) or '—')
                      for name in identities]
            rows[identity] = {'object': record.construction_object, 'label': ' / '.join(labels),
                              'by_day': {}, 'total': Decimal(0)}
        row = rows[identity]
        row['by_day'][record.date] = {
            'record': record,
            'values': [{'label': record._meta.get_field(name).verbose_name,
                        'value': getattr(record, name)} for name in metrics],
            'edit': reverse('production:' + prefix + '_update', args=[record.pk]),
            'delete': reverse('production:' + prefix + '_delete', args=[record.pk]),
        }
        row['total'] += Decimal(getattr(record, quantity_field) or 0)
    for row in rows.values():
        row['cells'] = [row['by_day'].get(day) for day in days]
    return list(rows.values())
