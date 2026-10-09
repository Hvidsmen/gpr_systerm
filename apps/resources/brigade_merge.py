"""Merge company-scoped positions, preserving people and frozen plan history."""
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
from .models import Brigade, BrigadeMerge, BrigadeMember
from .equipment_merge import transfer_rows

def brigade_aliases(company):
    return dict(BrigadeMerge.objects.filter(company=company).values_list('source_id', 'target_id'))


def normalize_brigade_snapshot(snapshot, aliases):
    """Interpret archived IDs without rewriting approved snapshots or reviews."""
    if not aliases:
        return snapshot
    result = deepcopy(snapshot)
    for rows in [result.get('resources', {}).get('labor', []),
                 result.get('monthly_inputs', {}).get('resources', [])]:
        for row in rows:
            pk = row.get('brigade_id')
            if pk in aliases:
                row['brigade_id'] = aliases[pk]
    def collapse(rows, monthly=False):
        grouped = {}
        untouched = []
        for row in rows:
            if monthly and row.get('kind') != 'labor':
                untouched.append(row)
                continue
            identity = (row.get('month' if monthly else 'date'), row.get('brigade_id'))
            if identity not in grouped:
                grouped[identity] = row
                continue
            destination = grouped[identity]
            fields = ['count', 'hours'] if monthly else ['planned_workers', 'planned_hours']
            rate = 'rate' if monthly else 'hourly_rate'
            hours = 'hours' if monthly else 'planned_hours'
            a, b = Decimal(str(destination.get(hours) or 0)), Decimal(str(row.get(hours) or 0))
            if a + b and destination.get(rate) is not None and row.get(rate) is not None:
                destination[rate] = str((a * Decimal(str(destination[rate])) + b * Decimal(str(row[rate]))) / (a + b))
            for field in fields:
                if destination.get(field) is not None or row.get(field) is not None:
                    value = Decimal(str(destination.get(field) or 0)) + Decimal(str(row.get(field) or 0))
                    destination[field] = int(value) if field in ['count', 'planned_workers'] else str(value)
        return untouched + list(grouped.values())
    resources = result.get('resources', {})
    if 'labor' in resources:
        resources['labor'] = collapse(resources['labor'])
    monthly = result.get('monthly_inputs', {})
    if 'resources' in monthly:
        monthly['resources'] = collapse(monthly['resources'], monthly=True)
    catalog = result.get('catalogs', {}).get('labor', {})
    for old, new in aliases.items():
        if str(old) in catalog:
            catalog.setdefault(str(new), catalog[str(old)])
            del catalog[str(old)]
    return result


class BrigadeChoice(forms.ModelChoiceField):
    def label_from_instance(self, item):
        return f'{item.name} · {item.macro_group or "Без макрогруппы"} · {item.group or "Без группы"} · {item.unit} · ID {item.pk}'


class BrigadeMergeForm(forms.Form):
    target = BrigadeChoice(queryset=Brigade.objects.none(), label='Оставить должность (бригаду)')
    sources = forms.ModelMultipleChoiceField(queryset=Brigade.objects.none(), label='Объединить с ней', widget=forms.CheckboxSelectMultiple)
    confirm = forms.BooleanField(label='Подтверждаю перенос данных и объединение')

    def __init__(self, *args, user, **kwargs):
        super().__init__(*args, **kwargs)
        qs = Brigade.objects.filter(company=user.company, merge_source__isnull=True).select_related('group', 'macro_group').order_by('name', 'pk')
        self.fields['target'].queryset = qs.filter(is_active=True)
        self.fields['sources'].queryset = qs
        self.fields['sources'].label_from_instance = self.fields['target'].label_from_instance

    def clean(self):
        data = super().clean()
        if data.get('target') and data.get('sources') and data['target'] in data['sources']:
            raise ValidationError('Оставляемая запись не должна входить в список объединяемых.')
        return data


@transaction.atomic
def merge_brigades(user, target_id, source_ids):
    require_roles(user, PLAN_ROLES)
    ids = sorted(set(source_ids) | {target_id})
    records = {item.pk: item for item in Brigade.objects.select_for_update().filter(company=user.company,
        pk__in=ids, merge_source__isnull=True).order_by('pk')}
    if len(records) != len(ids) or not source_ids or target_id in source_ids:
        raise ValidationError('Выберите доступные должности своей компании; оставляемая запись должна быть отдельной.')
    target = records[target_id]
    if not target.is_active:
        raise ValidationError('Оставляемая должность должна быть активна.')
    from apps.production.models import LaborPlan, LaborFact
    from apps.planning.models import ResourceMonthAllocation
    from apps.rotation.models import RotationRole, RotationPerson, RotationPlan
    from django.core.serializers.json import DjangoJSONEncoder
    import json
    for source_id in sorted(set(source_ids)):
        source = records[source_id]
        if source.unit.strip().casefold() != target.unit.strip().casefold():
            raise ValidationError('Объединять можно только должности с одинаковыми единицами измерения.')
        audit = []
        transfer_rows(LaborPlan, user.company, source, target, ['construction_object_id', 'date'],
            ['planned_workers', 'planned_hours'], 'hourly_rate', audit, relation='brigade')
        transfer_rows(LaborFact, user.company, source, target, ['construction_object_id', 'date'],
            ['planned_workers', 'actual_workers', 'planned_hours', 'actual_hours'], 'hourly_rate', audit, relation='brigade')
        transfer_rows(ResourceMonthAllocation, user.company, source, target, ['version_id', 'month', 'kind'],
            ['count', 'hours'], 'rate', audit, relation='brigade')
        for member in BrigadeMember.objects.select_for_update().filter(company=user.company, brigade=source):
            audit.append({'model':'resources.brigademember', 'source':model_to_dict(member)})
            existing = BrigadeMember.objects.filter(company=user.company, brigade=target, employee=member.employee).first()
            if existing:
                existing.role_in_brigade = '; '.join(dict.fromkeys(filter(None,[existing.role_in_brigade,member.role_in_brigade])))
                existing.full_clean();existing.save();member.delete()
            else:
                member.brigade = target;member.full_clean();member.save()
        for role in RotationRole.objects.select_for_update().filter(company=user.company, brigade=source):
            audit.append({'model':'rotation.rotationrole','source':model_to_dict(role)})
            existing = RotationRole.objects.select_for_update().filter(company=user.company, plan=role.plan, brigade=target).first()
            if existing:
                # Personal schedules and their manual statuses stay attached to the same people.
                RotationPerson.objects.filter(company=user.company, position=role).update(position=existing)
                role.delete()
            else:
                role.brigade = target;role.save()
        BrigadeMerge.objects.filter(company=user.company, target=source).update(target=target)
        source.is_active = False;source.save(update_fields=['is_active'])
        BrigadeMerge.objects.create(company=user.company,source=source,target=target,created_by=user,
            audit=json.loads(json.dumps({'source':model_to_dict(source),'target':model_to_dict(target),'records':audit},cls=DjangoJSONEncoder)))
    aliases = brigade_aliases(user.company)
    for plan in RotationPlan.objects.select_for_update().filter(company=user.company):
        if any(row['brigade_id'] in aliases for row in plan.demand):
            grouped = {}
            for row in plan.demand:
                key = (aliases.get(row['brigade_id'],row['brigade_id']),row['date'])
                grouped[key] = grouped.get(key,0) + row['count']
            plan.demand = [{'brigade_id':pk,'date':day,'count':count} for (pk,day),count in sorted(grouped.items())]
            plan.save(update_fields=['demand','updated_at'])
    return len(set(source_ids))


class BrigadeMergeView(View):
    def get(self, request):
        require_roles(request.user, PLAN_ROLES)
        return render(request,'resources/brigade_merge.html',{'form':BrigadeMergeForm(user=request.user)})

    def post(self, request):
        require_roles(request.user, PLAN_ROLES)
        if request.POST.get('merge_stage') == 'select':
            try: ids = sorted({int(v) for v in request.POST.getlist('selected')})
            except (ValueError,TypeError): ids = []
            selected = list(Brigade.objects.filter(company=request.user.company,pk__in=ids,merge_source__isnull=True).order_by('pk'))
            active = [item for item in selected if item.is_active]
            initial = {}
            if len(ids) < 2 or len(selected) != len(ids) or not active:
                messages.error(request,'Выберите не менее двух доступных должностей, включая хотя бы одну активную.')
            else:
                initial = {'target':active[0].pk,'sources':[item.pk for item in selected if item.pk!=active[0].pk]}
            return render(request,'resources/brigade_merge.html',{'form':BrigadeMergeForm(user=request.user,initial=initial)})
        form = BrigadeMergeForm(request.POST,user=request.user)
        if form.is_valid():
            try:
                count = merge_brigades(request.user,form.cleaned_data['target'].pk,[r.pk for r in form.cleaned_data['sources']])
                messages.success(request,f'Объединено должностей: {count}. План, факт, люди и перевахта перенесены; история сохранена.')
                return redirect('resources:brigade_list')
            except ValidationError as error: form.add_error(None,error)
        return render(request,'resources/brigade_merge.html',{'form':form})


def normalize_resource_snapshot(snapshot, company):
    from .equipment_merge import equipment_aliases, normalize_equipment_snapshot
    return normalize_brigade_snapshot(normalize_equipment_snapshot(snapshot, equipment_aliases(company)), brigade_aliases(company))
