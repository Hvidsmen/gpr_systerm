"""Edit a complete cost distribution without changing work norms or quantities."""
import hashlib
import json
from decimal import Decimal
from django import forms
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import F
from django.http import HttpResponseNotAllowed
from django.shortcuts import redirect
from django.utils import timezone
from core.permissions import require_roles, PLAN_ROLES
from .models import ProjectWorkItem
from .views import WorkDetailView


def revision(items):
    return hashlib.sha256(json.dumps([(i.pk, str(i.weight), i.updated_at.isoformat()) for i in items]).encode()).hexdigest()


class CostSharesForm(forms.Form):
    revision = forms.CharField(widget=forms.HiddenInput)

    def __init__(self, *args, items, **kwargs):
        super().__init__(*args, **kwargs)
        self.items = list(items)
        self.initial['revision'] = revision(self.items)
        for item in self.items:
            self.fields[f'share_{item.pk}'] = forms.DecimalField(label=f'Доля от суммы: {item.name}, %',
                initial=item.weight, min_value=0, max_value=100, max_digits=5, decimal_places=2, localize=True,
                widget=forms.TextInput(attrs={'class':'form-control form-control-sm','inputmode':'decimal','data-cost-share':'', 'aria-label':f'Доля стоимости {item.name}, %'}))

    def clean(self):
        values = super().clean()
        if not self.errors and sum((values[f'share_{i.pk}'] for i in self.items), Decimal(0)) != 100:
            raise ValidationError('Сумма долей всех работ комплекса должна быть 100%.')
        return values


class CostSharesUpdate(WorkDetailView):
    def get(self, request, *args, **kwargs):
        return HttpResponseNotAllowed(['POST'])

    def post(self, request, *args, **kwargs):
        require_roles(request.user, PLAN_ROLES)
        self.object = self.get_object()
        with transaction.atomic():
            type(self.object).objects.filter(pk=self.object.pk).update(name=F('name'))
            items = list(self.object.items.select_for_update().order_by('sequence', 'pk'))
            form = CostSharesForm(request.POST, items=items)
            if form.is_valid():
                if form.cleaned_data['revision'] != revision(items):
                    form.add_error(None, 'Состав работ или доли изменились в другой вкладке. Обновите страницу.')
                elif self.object.kind != 'COMPOSITE':
                    form.add_error(None, 'Доли задаются для работ в составе комплекса.')
                else:
                    from .propagation import preview
                    changes={'items':{str(item.pk):{'weight':str(form.cleaned_data[f'share_{item.pk}'])} for item in items}}
                    try:return preview(request,self.object,changes)
                    except ValidationError as exc:form.add_error(None,exc)
        self.cost_shares_form = form
        return self.render_to_response(self.get_context_data(), status=400)
