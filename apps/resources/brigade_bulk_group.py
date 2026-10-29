"""Assign catalogue groups to selected positions without changing other fields."""
from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import Http404, QueryDict
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST
from core.permissions import PERSONNEL_CATALOG_EDIT_ROLES, require_roles
from .models import Brigade, BrigadeGroup, BrigadeMacroGroup


class GroupAssignmentForm(forms.Form):
    group = forms.ChoiceField(label='Группа', required=False)

    def __init__(self, *args, company, **kwargs):
        super().__init__(*args, **kwargs)
        for name, model in [('group', BrigadeGroup)]:
            self.fields[name].choices = [('', 'Не менять'), ('clear', 'Без группы' if name == 'group' else 'Без макрогруппы')] + [
                (str(row.pk), row.name) for row in model.objects.filter(company=company).order_by('name', 'pk')]

    def clean(self):
        data = super().clean()
        if not self.errors and not any(data.values()):
            raise forms.ValidationError('Выберите группу для изменения.')
        return data


def list_url(query):
    params = QueryDict(query)
    safe = QueryDict(mutable=True)
    for key in ('name','group','staffing_current','active_employees','view','page'):
        if params.get(key):
            safe[key] = params[key]
    return reverse('resources:brigade_list') + ('?' + safe.urlencode() if safe else '')


@login_required
@require_POST
def assign_groups(request):
    require_roles(request.user, PERSONNEL_CATALOG_EDIT_ROLES)
    try:
        ids = {int(value) for value in request.POST.getlist('selected')}
    except (TypeError, ValueError):
        raise Http404('Неверный выбор должностей.')
    if any(value < 1 or value > 9223372036854775807 for value in ids):
        raise Http404('Неверный выбор должностей.')
    if not 1 <= len(ids) <= 500:
        messages.error(request, 'Выберите от одной до 500 должностей.')
        return redirect(list_url(request.POST.get('return_query', '')))
    with transaction.atomic():
        form = GroupAssignmentForm(request.POST, company=request.user.company, prefix='assignment')
        valid = form.is_valid()
        # Lock group targets before positions, following the group merge lock order.
        if valid:
            for name, model in [('group', BrigadeGroup)]:
                value = form.cleaned_data[name]
                if value and value != 'clear' and not model.objects.select_for_update().filter(company=request.user.company, pk=value).exists():
                    form.add_error(name, 'Группа уже удалена. Выберите другую.')
                    valid = False
        rows = list(Brigade.objects.select_for_update().filter(
            company=request.user.company, pk__in=ids, merge_source__isnull=True).order_by('pk'))
        if len(rows) != len(ids):
            raise Http404('Одна из выбранных должностей недоступна. Обновите список.')
        if valid:
            updates = {name + '_id': None if value == 'clear' else int(value)
                       for name, value in form.cleaned_data.items() if value}
            changed = [row for row in rows if any(getattr(row, key) != value for key, value in updates.items())]
            if changed:
                from apps.audit.models import AuditLog
                AuditLog.objects.bulk_create([AuditLog(user=request.user, action='UPDATE', entity_type='resources.Brigade',
                    entity_id=str(row.pk), old_value={key: getattr(row, key) for key in updates}, new_value=updates,
                    comment='Массовое назначение группы должностей') for row in changed])
                Brigade.objects.filter(company=request.user.company, pk__in=[row.pk for row in changed]).update(**updates, updated_at=timezone.now())
            messages.success(request, f'Группы сохранены. Изменено должностей: {len(changed)} из {len(rows)}.')
            return redirect(list_url(request.POST.get('return_query', '')))
    return render(request, 'resources/brigade_bulk_group.html', {
        'form': form, 'rows': rows, 'return_query': request.POST.get('return_query', ''),
        'return_url': list_url(request.POST.get('return_query', '')),
    }, status=400)
