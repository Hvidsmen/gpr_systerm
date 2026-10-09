"""Company-scoped equipment aliases, transactional transfers and immutable history."""
from copy import deepcopy
from decimal import Decimal
from django import forms
from django.core.exceptions import ValidationError
from django.db import transaction
from django.forms.models import model_to_dict
from django.contrib import messages
from django.shortcuts import render, redirect
from django.views import View
from core.permissions import require_roles, PLAN_ROLES
from .models import EquipmentType, EquipmentTypeMerge, Equipment


def equipment_aliases(company):
    return dict(EquipmentTypeMerge.objects.filter(company=company).values_list('source_id', 'target_id'))


def normalize_equipment_snapshot(snapshot, aliases):
    """Interpret archived IDs without rewriting approved snapshots or reviews."""
    if not aliases:
        return snapshot
    result = deepcopy(snapshot)
    for rows in [result.get('resources', {}).get('equipment', []),
                 result.get('monthly_inputs', {}).get('resources', [])]:
        for row in rows:
            pk = row.get('equipment_type_id')
            if pk in aliases:
                row['equipment_type_id'] = aliases[pk]
    def collapse(rows, monthly=False):
        grouped = {}
        untouched = []
        for row in rows:
            if monthly and row.get('kind') != 'equipment':
                untouched.append(row)
                continue
            identity = (row.get('month' if monthly else 'date'), row.get('equipment_type_id'), row.get('equipment_number', ''))
            if identity not in grouped:
                grouped[identity] = row
                continue
            destination = grouped[identity]
            fields = ['count', 'hours'] if monthly else ['planned_count', 'planned_machine_hours']
            rate = 'rate' if monthly else 'hourly_rate'
            hours = 'hours' if monthly else 'planned_machine_hours'
            a, b = Decimal(str(destination.get(hours) or 0)), Decimal(str(row.get(hours) or 0))
            if a + b and destination.get(rate) is not None and row.get(rate) is not None:
                destination[rate] = str((a * Decimal(str(destination[rate])) + b * Decimal(str(row[rate]))) / (a + b))
            for field in fields:
                if destination.get(field) is not None or row.get(field) is not None:
                    value = Decimal(str(destination.get(field) or 0)) + Decimal(str(row.get(field) or 0))
                    destination[field] = int(value) if field in ['count', 'planned_count'] else str(value)
        return untouched + list(grouped.values())
    resources = result.get('resources', {})
    if 'equipment' in resources:
        resources['equipment'] = collapse(resources['equipment'])
    monthly = result.get('monthly_inputs', {})
    if 'resources' in monthly:
        monthly['resources'] = collapse(monthly['resources'], monthly=True)
    catalog = result.get('catalogs', {}).get('equipment', {})
    for old, new in aliases.items():
        if str(old) in catalog:
            catalog.setdefault(str(new), catalog[str(old)])
            del catalog[str(old)]
    return result


class EquipmentChoice(forms.ModelChoiceField):
    def label_from_instance(self, item):
        return f'{item.name} · {item.category or "Без категории"} · {item.unit} · ID {item.pk}'


class EquipmentMergeForm(forms.Form):
    target = EquipmentChoice(queryset=EquipmentType.objects.none(), label='Оставить вид техники')
    sources = forms.ModelMultipleChoiceField(queryset=EquipmentType.objects.none(), label='Объединить с ним', widget=forms.CheckboxSelectMultiple)
    confirm = forms.BooleanField(label='Подтверждаю перенос данных и объединение')

    def __init__(self, *args, user, **kwargs):
        super().__init__(*args, **kwargs)
        qs = EquipmentType.objects.filter(company=user.company, merge_source__isnull=True).select_related('category').order_by('name', 'pk')
        self.fields['target'].queryset = qs.filter(is_active=True)
        self.fields['sources'].queryset = qs
        self.fields['sources'].label_from_instance = self.fields['target'].label_from_instance

    def clean(self):
        data = super().clean()
        if data.get('target') and data.get('sources') and data['target'] in data['sources']:
            raise ValidationError('Оставляемая запись не должна входить в список объединяемых.')
        return data


def transfer_rows(model, company, source, target, identity, fields, rate, audit, *, relation="equipment_type"):
    for row in model.objects.select_for_update().filter(company=company, **{relation: source}).order_by('pk'):
        # Frozen inputs are kept for history; aliases normalize them when read.
        if model._meta.label_lower == 'planning.resourcemonthallocation' and row.version.status not in ['DRAFT', 'REJECTED']:
            continue
        audit.append({'model': model._meta.label_lower, 'source': model_to_dict(row)})
        existing = model.objects.select_for_update().filter(company=company, **{relation: target}, **{key: getattr(row, key) for key in identity}).first()
        if existing:
            first, second = getattr(existing, rate), getattr(row, rate)
            if first is not None and second is not None and first != second:
                raise ValidationError(f'Разные ставки в {model._meta.verbose_name}: записи ID {existing.pk} и {row.pk}. Сначала согласуйте ставки.')
            if hasattr(row, 'load_profile_id') and existing.load_profile_id != row.load_profile_id:
                raise ValidationError(f'Разные профили распределения: записи ID {existing.pk} и {row.pk}. Сначала выберите одинаковый профиль.')
            audit[-1]['target_before'] = model_to_dict(existing)
            for field in fields:
                a, b = getattr(existing, field), getattr(row, field)
                setattr(existing, field, None if a is None and b is None else (a or 0) + (b or 0))
            setattr(existing, rate, first if first is not None else second)
            if hasattr(existing, 'comment'):
                existing.comment = '\n'.join(filter(None, [existing.comment, row.comment]))
            existing.full_clean()
            existing.save()
            row.delete()
        else:
            setattr(row, relation, target)
            row.full_clean()
            row.save()


@transaction.atomic
def merge_equipment(user, target_id, source_ids):
    require_roles(user, PLAN_ROLES)
    ids = sorted(set(source_ids) | {target_id})
    records = {item.pk: item for item in EquipmentType.objects.select_for_update().filter(company=user.company, pk__in=ids, merge_source__isnull=True).order_by("pk")}
    if len(records) != len(ids) or not source_ids or target_id in source_ids:
        raise ValidationError('Выберите доступные виды техники своей компании; оставляемая запись должна быть отдельной.')
    target = records[target_id]
    if not target.is_active:
        raise ValidationError('Оставляемый вид техники должен быть активен.')
    from apps.production.models import EquipmentPlan, EquipmentFact
    from apps.planning.models import ResourceMonthAllocation
    from django.core.serializers.json import DjangoJSONEncoder
    import json
    for source_id in sorted(set(source_ids)):
        source = records[source_id]
        if source.unit.strip().casefold() != target.unit.strip().casefold():
            raise ValidationError('Объединять можно только технику с одинаковыми единицами измерения.')
        audit = []
        transfer_rows(EquipmentPlan, user.company, source, target, ['construction_object_id', 'date', 'equipment_number'], ['planned_count', 'planned_machine_hours'], 'hourly_rate', audit)
        transfer_rows(EquipmentFact, user.company, source, target, ['construction_object_id', 'date', 'equipment_number'], ['planned_count', 'actual_count', 'machine_hours'], 'hourly_rate', audit)
        transfer_rows(ResourceMonthAllocation, user.company, source, target, ['version_id', 'month', 'kind', 'equipment_number'], ['count', 'hours'], 'rate', audit)
        machines = list(Equipment.objects.filter(company=user.company, type=source).values_list('pk', flat=True))
        Equipment.objects.filter(company=user.company, type=source).update(type=target)
        EquipmentTypeMerge.objects.filter(company=user.company, target=source).update(target=target)
        source.is_active = False
        source.save(update_fields=['is_active'])
        EquipmentTypeMerge.objects.create(company=user.company, source=source, target=target, created_by=user, audit=json.loads(json.dumps({'source': model_to_dict(source), 'target': model_to_dict(target), 'records': audit, 'equipment_ids': machines}, cls=DjangoJSONEncoder)))
    return len(set(source_ids))


class EquipmentMergeView(View):
    def get(self, request):
        require_roles(request.user, PLAN_ROLES)
        return render(request, 'resources/equipment_merge.html', {'form': EquipmentMergeForm(user=request.user)})

    def post(self, request):
        require_roles(request.user, PLAN_ROLES)
        if request.POST.get('merge_stage') == 'select':
            raw_ids = request.POST.getlist('selected')
            try:
                ids = sorted({int(value) for value in raw_ids})
            except (ValueError, TypeError):
                ids = []
            selected = list(EquipmentType.objects.filter(company=request.user.company,
                pk__in=ids, merge_source__isnull=True).order_by('pk'))
            active = [item for item in selected if item.is_active]
            if len(ids) < 2 or len(selected) != len(ids) or not active:
                form = EquipmentMergeForm(user=request.user)
                messages.error(request, 'Выберите не менее двух доступных видов техники, включая хотя бы один активный.')
            else:
                target = active[0]
                form = EquipmentMergeForm(user=request.user, initial={
                    'target': target.pk, 'sources': [item.pk for item in selected if item.pk != target.pk],
                })
            return render(request, 'resources/equipment_merge.html', {'form': form})
        form = EquipmentMergeForm(request.POST, user=request.user)
        if form.is_valid():
            try:
                count = merge_equipment(request.user, form.cleaned_data['target'].pk, [r.pk for r in form.cleaned_data['sources']])
                messages.success(request, f'Объединено видов техники: {count}. План, факт и машины перенесены; история версий сохранена.')
                return redirect('resources:equipment_type_list')
            except ValidationError as error:
                form.add_error(None, error)
        return render(request, 'resources/equipment_merge.html', {'form': form})
