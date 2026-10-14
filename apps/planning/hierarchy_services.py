"""Explicit distribution of macro-object GPR into monthly sub-object SMG."""
from calendar import monthrange
from copy import deepcopy
from decimal import Decimal
from django.core.exceptions import ValidationError
from django.db import transaction
from core.permissions import require_roles, PLAN_ROLES
from apps.projects.models import ConstructionObject, Section
from apps.works.models import ProjectWork
from .models import PlanningWorkspace, WorkMonthAllocation, SmgWorkMapping
from .workspace_services import WorkspaceService
from .report_projection import period_snapshot


def month_specs(version, month):
    end = month.replace(day=monthrange(month.year, month.month)[1])
    if month.day != 1 or month < version.start_date or end > version.end_date:
        raise ValidationError('Выберите полный месяц внутри периода ГПР.')
    if version.status not in ('APPROVED', 'COMPLETED'):
        raise ValidationError('Распределение СМГ создаётся из утверждённой версии ГПР.')
    if version.workspace_id and version.workspace.parent_gpr_id:
        raise ValidationError('Выберите ГПР макрообъекта, а не СМГ субобъекта.')
    return period_snapshot(version, month, end).get('works', [])


def work_volume(spec):
    return sum((Decimal(row['quantity']) for row in spec.get('daily', [])), Decimal(0))


def distribution(version, month, include_facts=False):
    specs = month_specs(version, month)
    workspaces = list(PlanningWorkspace.objects.filter(company=version.company, parent_gpr=version, start_date=month).select_related('construction_object', 'baseline_version').defer('baseline_version__snapshot').order_by('construction_object__name'))
    values, targets = {}, {}
    baseline_ids = {workspace.pk: workspace.baseline_version_id for workspace in workspaces}
    quantities = {(version_id, work_id): quantity for version_id, work_id, quantity in WorkMonthAllocation.objects.filter(version_id__in=baseline_ids.values(), month=month).values_list('version_id', 'work_id', 'quantity')}
    for link in SmgWorkMapping.objects.filter(workspace__in=workspaces).select_related('workspace', 'smg_work'):
        quantity = quantities.get((baseline_ids[link.workspace_id], link.smg_work_id), Decimal(0))
        values[(link.gpr_work_id, link.workspace_id)] = quantity
        targets[(link.gpr_work_id, link.workspace_id)] = link.smg_work
    result = []
    for spec in specs:
        cells = []
        for workspace in workspaces:
            key = spec['id'], workspace.pk
            fact = None
            if include_facts and key in targets:
                from apps.works.progress import WorkProgressService
                end = month.replace(day=monthrange(month.year, month.month)[1])
                facts = WorkProgressService.facts(targets[key], until=end)
                fact = sum((value['daily'] for day, value in facts.items() if month <= day <= end), Decimal(0)) if any(month <= day <= end for day in facts) else None
            cells.append({'workspace': workspace, 'quantity': values.get(key, Decimal(0)), 'fact': fact})
        planned = work_volume(spec)
        assigned = sum((cell['quantity'] for cell in cells), Decimal(0))
        known_facts = [cell['fact'] for cell in cells if cell['fact'] is not None]
        result.append({'id': spec['id'], 'name': spec['name'], 'unit': spec['unit'], 'gpr': planned, 'cells': cells, 'smg': assigned, 'remaining': planned-assigned, 'excess': max(assigned-planned, Decimal(0)), 'fact': sum(known_facts, Decimal(0)) if known_facts else None})
    return workspaces, result


def smg_warnings(workspace):
    if not workspace.parent_gpr_id:
        return []
    from .models import GlobalPlanVersion
    parent = GlobalPlanVersion.objects.defer('snapshot').get(pk=workspace.parent_gpr_id)
    workspaces, rows = distribution(parent, workspace.start_date)
    rows += resource_distribution(parent, workspace.start_date, workspaces)
    return [f"СМГ превышают ГПР: {row['name']} — на {row['excess']} {row['unit']}. Согласование разрешено." for row in rows if row['excess']]


@transaction.atomic
def create_smg(user, version, child, month, quantities):
    require_roles(user, PLAN_ROLES)
    version = type(version).objects.select_for_update().defer('snapshot').get(pk=version.pk, company=user.company)
    child = ConstructionObject.objects.select_for_update().get(pk=child.pk, company=user.company)
    if child.parent_id != version.construction_object_id or child.project_id != version.construction_object.project_id:
        raise ValidationError('Выберите субобъект этого макрообъекта.')
    specs = month_specs(version, month)
    if set(quantities) != {spec['id'] for spec in specs}:
        raise ValidationError('Укажите объёмы всех работ ГПР, включая нулевые.')
    if PlanningWorkspace.objects.filter(parent_gpr=version, construction_object=child, start_date=month).exists():
        raise ValidationError('СМГ этого субобъекта на месяц уже существует. Откройте его для редактирования.')
    end = month.replace(day=monthrange(month.year, month.month)[1])
    workspace = WorkspaceService.create(user, child, f'СМГ {child.name} · {month:%m.%Y}', month, end)
    workspace.parent_gpr = version
    workspace.save(update_fields=['parent_gpr'])
    for spec in specs:
        quantity = Decimal(quantities[spec['id']])
        if not quantity.is_finite() or quantity < 0:
            raise ValidationError('Объём СМГ должен быть неотрицательным.')
        source = ProjectWork.objects.get(pk=spec['id'], company=user.company, section__construction_object=version.construction_object)
        existing = SmgWorkMapping.objects.filter(company=user.company, gpr_work=source, workspace__parent_gpr__construction_object=version.construction_object, workspace__construction_object=child).select_related('smg_work').first()
        item_mapping = {}
        reusable = None
        if not existing:
            candidates = list(ProjectWork.objects.filter(company=user.company, section__construction_object=child, section__name=source.section.name, name=spec['name'], unit=spec['unit'], kind=spec['kind'], allow_fractional=spec['allow_fractional'], merged_source__isnull=True).prefetch_related('items'))
            if len(candidates) == 1:
                candidate = candidates[0]
                target_items = list(candidate.items.all())
                frozen_items = spec.get('items', [])
                def fingerprint(item):
                    return item['name'], item['unit'], Decimal(item['norm']), Decimal(item['weight'])
                targets = {}
                for item in target_items:
                    targets.setdefault((item.name, item.unit, item.quantity_per_unit, item.weight), []).append(item.pk)
                if len(target_items) == len(frozen_items) and all(len(targets.get(fingerprint(item), [])) == 1 for item in frozen_items):
                    reusable = candidate
                    item_mapping = {str(targets[fingerprint(item)][0]): item['id'] for item in frozen_items}
        if existing:
            work = existing.smg_work
            item_mapping = existing.item_mapping
        elif reusable:
            work = reusable
        else:
            section = Section.objects.filter(company=user.company, construction_object=child, name=source.section.name).first()
            if section is None:
                section = Section.objects.create(company=user.company, construction_object=child, name=source.section.name)
            work = deepcopy(source)
            work.pk, work.code, work.section = None, '', section
            work._state.adding = True
            work.name, work.unit = spec['name'], spec['unit']
            work.kind, work.allow_fractional = spec['kind'], spec['allow_fractional']
            work.unit_price = Decimal(spec.get('unit_price') or 0)
            work.save()
            frozen_items = {item['id']: item for item in spec.get('items', [])}
            cloned_items = []
            for item in source.items.all():
                frozen = frozen_items.get(item.pk)
                if frozen is None:
                    continue
                cloned = deepcopy(item)
                cloned.pk, cloned.project_work = None, work
                cloned.parent = None
                cloned._state.adding = True
                cloned.name, cloned.unit = frozen['name'], frozen['unit']
                cloned.quantity_per_unit = Decimal(frozen['norm'])
                cloned.weight = Decimal(frozen['weight'])
                if hasattr(cloned, 'code'):
                    cloned.code = ''
                cloned.save()
                item_mapping[str(cloned.pk)] = frozen['id']
                cloned_items.append((item.parent_id, cloned))
            target_ids = {source_id: int(target_id) for target_id, source_id in item_mapping.items()}
            for parent_id, cloned in cloned_items:
                if parent_id:
                    cloned.parent_id = target_ids.get(parent_id)
                    cloned.save(update_fields=['parent'])
        link = SmgWorkMapping(company=user.company, workspace=workspace, gpr_work=source, smg_work=work, item_mapping=item_mapping)
        link.full_clean()
        link.save()
        allocation = WorkMonthAllocation(company=user.company, version=workspace.baseline_version, work=work, month=month, quantity=quantity)
        allocation.full_clean()
        allocation.save()
    return workspace


def rollup_subobject_facts(user, objects, sources, start, end, work_facts, resource_facts):
    """Count actuals at macro level only through the selected GPR's SMG links."""
    from collections import defaultdict
    from apps.production.models import DailyFact, LaborFact, EquipmentFact, FuelFact
    selected = {obj.pk: sources.get(obj.pk, []) for obj in objects}
    workspaces = list(PlanningWorkspace.objects.filter(company=user.company, parent_gpr__construction_object_id__in=selected, start_date__lte=end).select_related('parent_gpr').defer('parent_gpr__snapshot'))
    if not workspaces:
        return
    def active(workspace, day):
        return workspace.start_date <= day <= workspace.end_date and any(source.start <= day <= source.end for source in selected.get(workspace.parent_gpr.construction_object_id, []))
    mappings = list(SmgWorkMapping.objects.filter(company=user.company, workspace__in=workspaces).select_related('workspace__parent_gpr').defer('workspace__parent_gpr__snapshot'))
    by_work = defaultdict(list)
    for link in mappings:
        by_work[link.smg_work_id].append(link)
    counted_work = set()
    for fact in DailyFact.objects.filter(company=user.company, project_work_id__in=by_work, date__lte=end).values('pk', 'project_work_id', 'date', 'work_item_id', 'actual_quantity'):
        for link in by_work[fact['project_work_id']]:
            identity = fact['pk'], link.gpr_work_id
            if active(link.workspace, fact['date']) and identity not in counted_work:
                counted_work.add(identity)
                item = link.item_mapping.get(str(fact['work_item_id'])) if fact['work_item_id'] else None
                if fact['work_item_id'] and item is None:
                    continue
                work_facts[link.gpr_work_id].append((fact['date'], item, fact['actual_quantity']))
    by_object = defaultdict(list)
    for workspace in workspaces:
        by_object[workspace.construction_object_id].append(workspace)
    counted_resources = set()
    for model, kind, fields, amount in [(LaborFact, 'labor', ['brigade_id'], 'actual_workers'), (EquipmentFact, 'equipment', ['equipment_type_id', 'equipment_number'], 'actual_count'), (FuelFact, 'fuel', ['fuel_type'], 'actual_liters')]:
        for fact in model.objects.filter(company=user.company, construction_object_id__in=by_object, date__range=(start, end)).values('construction_object_id', 'date', *fields, amount, *(['actual_balance'] if kind=='fuel' else [])):
            for workspace in by_object[fact['construction_object_id']]:
                identity = kind, workspace.parent_gpr.construction_object_id, fact['construction_object_id'], fact['date'], *(fact[field] for field in fields)
                if active(workspace, fact['date']) and identity not in counted_resources:
                    counted_resources.add(identity)
                    key = (workspace.parent_gpr.construction_object_id, *(fact[field] for field in fields))
                    if fact[amount] is not None:
                        resource_facts[kind][key][fact['date']] += Decimal(fact[amount])
                    if kind=='fuel' and fact['actual_balance'] is not None:
                        resource_facts['balance'][key][fact['date']] += Decimal(fact['actual_balance'])


def resource_distribution(version, month, workspaces):
    from collections import defaultdict
    from .models import ResourceMonthAllocation
    from .workspace_services import resource_input
    end = month.replace(day=monthrange(month.year, month.month)[1])
    snapshot = period_snapshot(version, month, end)
    baseline_ids = {workspace.baseline_version_id: workspace.pk for workspace in workspaces}
    entries, assigned = {}, defaultdict(lambda: Decimal(0))
    def identity(kind, row):
        return (kind, row.get('brigade_id')) if kind=='labor' else (kind, row.get('equipment_type_id'), row.get('equipment_number', '')) if kind=='equipment' else (kind, row.get('fuel_type'))
    for kind, records in snapshot.get('resources', {}).items():
        for record in records:
            key = identity(kind, record)
            fields = [('planned_workers', 'чел.', 'average')] if kind=='labor' else [('planned_count', 'ед.', 'average')] if kind=='equipment' else [('planned_liters', 'л', 'sum'), ('planned_balance', 'л', 'last')]
            for field, unit, operation in fields:
                if record.get(field) is None:
                    continue
                group = entries.setdefault((key, field), {'label': record.get('label', str(key)), 'kind': kind, 'unit': unit, 'operation': operation, 'values': {}})
                group['values'][record['date']] = group['values'].get(record['date'], Decimal(0)) + Decimal(str(record[field]))
    for allocation in ResourceMonthAllocation.objects.filter(version_id__in=baseline_ids, month=month).select_related('brigade', 'equipment_type'):
        data = resource_input(allocation)
        key = identity(allocation.kind, data)
        fields = [('planned_workers', allocation.count, 'чел.', 'average')] if allocation.kind=='labor' else [('planned_count', allocation.count, 'ед.', 'average')] if allocation.kind=='equipment' else [('planned_liters', allocation.liters, 'л', 'sum'), ('planned_balance', allocation.balance, 'л', 'last')]
        for field, value, unit, operation in fields:
            entries.setdefault((key, field), {'label': data['label'], 'kind': allocation.kind, 'unit': unit, 'operation': operation, 'values': {}})
            assigned[(key, field, baseline_ids[allocation.version_id])] += Decimal(value)
    result = []
    for (key, field), entry in entries.items():
        values = entry['values']
        if entry['operation']=='average':
            planned = sum(values.values(), Decimal(0))/Decimal(end.day)
        elif entry['operation']=='sum':
            planned = sum(values.values(), Decimal(0))
        else:
            planned = values[max(values)] if values else Decimal(0)
        cells = [{'workspace': workspace, 'quantity': assigned[(key, field, workspace.pk)]} for workspace in workspaces]
        total = sum((cell['quantity'] for cell in cells), Decimal(0))
        suffix = ' · остаток' if field=='planned_balance' else ' · расход' if field=='planned_liters' else ''
        result.append({**entry, 'name': entry['label']+suffix, 'gpr': planned, 'cells': cells, 'smg': total, 'remaining': planned-total, 'excess': max(total-planned, Decimal(0))})
    return result
