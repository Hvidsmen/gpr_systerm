"""Read-only grouped journals, including plans that have not been submitted."""
from collections import defaultdict
from datetime import date
from decimal import Decimal
from django.core.exceptions import ValidationError
from .models import GlobalPlanVersion
from .report_services import Source, selected_objects, build_matrix
from .workspace_services import (build_workspace_snapshot, specification, work_rows,
    finalize_work, resource_input, resource_rows, json_ready_in_place)


def draft_snapshot(version, start, end):
    if not version.workspace_id or version.version_kind != 'BASELINE':
        return build_workspace_snapshot(version, validate_inputs=False) if version.workspace_id else version.snapshot
    workspace = version.workspace
    allocations = version.work_allocations.filter(month__lte=end).select_related('work__load_profile', 'work__section', 'load_profile').order_by('work_id', 'month')
    works, current, rows, history, spec = [], None, [], defaultdict(Decimal), None
    day_cache = {}
    def finish():
        if spec is not None:
            context = [{'date': '0001-01-01', 'item_id': item, 'quantity': str(quantity)} for item, quantity in history.items()]
            works.append(finalize_work(spec, rows, workspace, context))
    for allocation in allocations.iterator(chunk_size=500):
        if allocation.work_id != current:
            finish()
            current, rows, history = allocation.work_id, [], defaultdict(Decimal)
            spec = specification(allocation.work)
            spec['section_name'] = allocation.work.section.name
        generated = allocation.daily_override if allocation.daily_override is not None else work_rows(
            workspace, spec, allocation.month, allocation.quantity, allocation.load_profile,
            allocation.item_quantities, day_cache=day_cache)
        for row in generated:
            day = date.fromisoformat(row['date'])
            if day < start:
                history[row['item_id']] += Decimal(row['quantity'])
            elif day <= end:
                rows.append(row)
    finish()
    resources = {kind: [] for kind in ('labor', 'equipment', 'fuel')}
    monthly = []
    for allocation in version.resource_allocations.filter(month__gte=start.replace(day=1), month__lte=end).select_related('brigade', 'equipment_type').iterator(chunk_size=500):
        source = resource_input(allocation)
        monthly.append(source)
        resources[source['kind']].extend(row for row in resource_rows(workspace, source, allocation.month) if str(start)<=row['date']<=str(end))
    return json_ready_in_place({'works': works, 'resources': resources, 'monthly_inputs': {'resources': monthly},
                               'version_kind': version.version_kind, 'warnings': []})


def journal_matrix(user, filters, kind):
    if kind == 'facts':
        result = build_matrix(user, filters, source_overrides={})
        result['fact_only'] = True
        result['column_count'] = 1 + len(result['columns']) + 1
        for obj in result['objects']:
            obj['warnings'] = []
        return result
    objects = selected_objects(user, filters)
    selected = defaultdict(list)
    candidates = GlobalPlanVersion.objects.filter(company=user.company, construction_object__in=objects,
                  start_date__lte=filters['end'], end_date__gte=filters['start']).defer('snapshot').select_related(
                  'workspace__construction_object', 'company').order_by('-version_number', '-pk')
    if filters.get('plan_status'):
        candidates = candidates.filter(status=filters['plan_status'])
    if filters['mode']=='baseline':
        candidates = candidates.filter(version_kind='BASELINE')
    versions = {}
    for version in candidates:
        versions.setdefault(version.construction_object_id, version)
    for obj in objects:
        version = filters.get(f'version_{obj.pk}') or versions.get(obj.pk)
        if version is None:
            continue
        try:
            if version.status in ('APPROVED', 'COMPLETED', 'SUBMITTED'):
                from .report_projection import period_snapshot
                snapshot = period_snapshot(version, filters['start'], filters['end'], **({'member': version, 'source_key': 'journal-submitted-' + version.updated_at.isoformat()} if version.status == 'SUBMITTED' else {}))
            else:
                snapshot = draft_snapshot(version, filters['start'], filters['end'])
        except ValidationError as exc:
            raise ValidationError(f'{obj.name}: {"; ".join(exc.messages)}') from exc
        selected[obj.pk].append(Source(version, snapshot, version.start_date, version.end_date))
    result = build_matrix(user, filters, source_overrides=selected, plan_only=True)
    for obj in result["objects"]:
        obj["warnings"] = [warning.replace("Согласованный план", "План") for warning in obj["warnings"]]
    return result
