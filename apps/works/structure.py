"""Atomic editing of catalogue norms, units, profiles and cost shares."""
from copy import deepcopy
from decimal import Decimal
import hashlib
import json
from django import forms
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import F
from django.http import HttpResponseNotAllowed
from django.shortcuts import redirect
from core.permissions import PLAN_ROLES, require_roles
from apps.planning.models import LoadProfile
from .models import MeasurementUnit
from .cost_shares import CostSharesForm
from .views import WorkDetailView


def structure_revision(items):
    values = [(i.pk, i.name, i.unit, str(i.quantity_per_unit), i.load_profile_id,
               str(i.weight), i.parent_id, i.sequence, i.updated_at.isoformat()) for i in items]
    return hashlib.sha256(json.dumps(values, ensure_ascii=False).encode()).hexdigest()


class WorkStructureForm(CostSharesForm):
    def __init__(self, *args, items, company, **kwargs):
        super().__init__(*args, items=items, **kwargs)
        self.initial['revision'] = structure_revision(self.items)
        units = list(MeasurementUnit.objects.filter(company=company).order_by('symbol').values_list('symbol', flat=True))
        profiles = LoadProfile.objects.filter(company=company).order_by('name', 'pk')
        profile_choices = [('', 'Выберите профиль'), *[(p.pk, p.name) for p in profiles]]
        for item in self.items:
            choices = [(u, u) for u in dict.fromkeys([*units, item.unit]) if u]
            self.fields[f'unit_{item.pk}'] = forms.ChoiceField(label=f'Единица: {item.name}', initial=item.unit,
                choices=choices, widget=forms.Select(attrs={'class':'form-select form-select-sm',
                    'aria-label':f'Единица измерения {item.name}'}))
            self.fields[f'norm_{item.pk}'] = forms.DecimalField(label=f'Норматив: {item.name}',
                initial=item.quantity_per_unit, min_value=Decimal('.001'), max_digits=10, decimal_places=3,
                localize=True, widget=forms.TextInput(attrs={'class':'form-control form-control-sm',
                    'inputmode':'decimal', 'aria-label':f'Норматив {item.name}'}))
            self.fields[f'profile_{item.pk}'] = forms.ModelChoiceField(label=f'Профиль: {item.name}',
                queryset=profiles, initial=item.load_profile_id, empty_label='Выберите профиль',
                widget=forms.Select(attrs={'class':'form-select form-select-sm',
                    'aria-label':f'Профиль нагрузки {item.name}'}))
            self.fields[f'profile_{item.pk}'].choices = profile_choices

    def clean(self):
        data = super().clean()
        self.validated_items = []
        if self.errors:
            return data
        for original in self.items:
            item = deepcopy(original)
            item.unit = data[f'unit_{item.pk}']
            item.quantity_per_unit = data[f'norm_{item.pk}']
            item.load_profile = data[f'profile_{item.pk}']
            item.weight = data[f'share_{item.pk}']
            item._catalogue_preview=True
            try:
                item.full_clean()
            except ValidationError as exc:
                mapping = {'unit':'unit', 'quantity_per_unit':'norm', 'load_profile':'profile', 'weight':'share'}
                if hasattr(exc, 'message_dict'):
                    for field, errors in exc.message_dict.items():
                        key = f'{mapping[field]}_{item.pk}' if field in mapping else None
                        for error in errors:self.add_error(key, error)
                else:
                    for error in exc.messages:self.add_error(None, f'{item.name}: {error}')
            self.validated_items.append(item)
        return data


class WorkStructureUpdate(WorkDetailView):
    def get(self, request, *args, **kwargs):
        return HttpResponseNotAllowed(['POST'])

    def post(self, request, *args, **kwargs):
        require_roles(request.user, PLAN_ROLES)
        self.object = self.get_object()
        with transaction.atomic():
            type(self.object).objects.filter(pk=self.object.pk).update(name=F('name'))
            items = list(self.object.items.select_for_update().order_by('sequence', 'pk'))
            form = WorkStructureForm(request.POST, items=items, company=request.user.company)
            if self.object.kind != 'COMPOSITE':
                form.is_valid()
                form.add_error(None, 'Состав редактируется только у комплекса работ.')
            elif form.is_valid():
                if form.cleaned_data['revision'] != structure_revision(items):
                    form.add_error(None, 'Состав изменился в другой вкладке. Обновите страницу перед сохранением.')
                else:
                    from .propagation import preview
                    changes={'items':{str(before.pk):{key:str(getattr(after,key)) if key in {'quantity_per_unit','weight'} else getattr(after,key) for key in ['unit','quantity_per_unit','load_profile_id','weight'] if getattr(before,key)!=getattr(after,key)} for before,after in zip(items,form.validated_items)}}
                    try:return preview(request,self.object,changes)
                    except ValidationError as exc:form.add_error(None,exc)
        self.structure_form = form
        return self.render_to_response(self.get_context_data(), status=400)
