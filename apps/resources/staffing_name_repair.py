"""Repair previously imported staffing labels after a verified ZUP snapshot.

Only records synchronized from this exact connection are renamed. Existing
canonical records retain identity; linked data follows the normal merge service.
Each job is a savepoint: conflicting settings are reported rather than lost.
"""
from collections import defaultdict
from decimal import Decimal
from django.core.exceptions import ValidationError
from django.db import transaction
from core.permissions import PERSONNEL_CATALOG_EDIT_ROLES, role_code
from apps.audit.models import AuditLog
from .employee_staffing import staffing_parts
from .models import Brigade
from .position_catalog import position_key, position_for


def repair_staffing_names(run, names):
    result = {'renamed': 0, 'merged': 0, 'warnings': []}
    company_id = run.connection.company_id
    source = run.server.casefold() + '/' + run.database.casefold()
    allowed = {position_key(name): name for name in names}
    records = list(Brigade.objects.filter(company_id=company_id, merge_source__isnull=True)
        .select_related('position_mapping__position').order_by('pk'))
    canonical = defaultdict(list)
    dirty = defaultdict(list)
    for row in records:
        name, department = staffing_parts(row.name)
        key = position_key(name)
        if not department:
            canonical[position_key(row.name)].append(row)
        elif key in allowed and row.staffing_synced_at and row.staffing_source.casefold() == source:
            dirty[key].append(row)
    if dirty and (not run.created_by or role_code(run.created_by) not in PERSONNEL_CATALOG_EDIT_ROLES):
        result['warnings'].append('Для исправления старых штатных названий запустите загрузку от ответственного за справочник или администратора.')
        return result
    for key, rows in dirty.items():
        candidates = canonical.get(key, [])
        if len(candidates) > 1:
            result['warnings'].append(allowed[key] + ': несколько существующих должностей с чистым названием; требуется ручное объединение.')
            continue
        target = candidates[0] if candidates else rows[0]
        items = [*candidates, *rows]
        original = [{'id': row.pk, 'name': row.name} for row in rows]
        try:
            with transaction.atomic():
                if not all(row.is_active for row in items):
                    raise ValidationError('Есть отключённые вручную записи; автоматическое объединение не выполняется.')
                # Preserve unique manual settings; conflicting choices need a person.
                updates = {'name': allowed[key]}
                for field in ('group_id', 'macro_group_id'):
                    values = {getattr(row, field) for row in items if getattr(row, field) is not None}
                    if len(values) > 1:
                        raise ValidationError('У подразделений заданы разные группы должностей.')
                    if values:
                        updates[field] = values.pop()
                rates = {row.position_mapping.position.default_hourly_rate for row in items if getattr(row, 'position_mapping', None) and row.position_mapping.position.default_hourly_rate}
                if len(rates) > 1:
                    raise ValidationError('У подразделений заданы разные ставки должности.')
                updates['description'] = '\n\n'.join(dict.fromkeys(row.description for row in items if row.description))
                updates['excel_aliases'] = list(dict.fromkeys(alias for row in items for alias in row.excel_aliases))
                for row in rows:
                    row.name = allowed[key]
                    row.save(update_fields=['name', 'updated_at'])
                for field, value in updates.items():
                    setattr(target, field, value)
                target.full_clean()
                target.save()
                if rates:
                    position = position_for(target)
                    position.default_hourly_rate = rates.pop()
                    position.save(update_fields=['default_hourly_rate'])
                sources = [row.pk for row in items if row.pk != target.pk]
                if sources:
                    from .brigade_batch_merge import preview
                    from .brigade_merge import merge_brigades
                    plan = preview(run.connection.company, record_ids=[row.pk for row in items], target_id=target.pk)
                    if not plan['eligible'] or plan['blocked']:
                        errors = [error for group in plan['groups'] for error in group['errors']]
                        raise ValidationError(errors or ['Не удалось проверить связи должностей.'])
                    merge_brigades(run.created_by, target.pk, sources)
                AuditLog.objects.create(user=run.created_by, action='UPDATE', entity_type='resources.Brigade',
                    entity_id=str(target.pk), old_value={'staffing_names': original},
                    new_value={'name': target.name, 'merged_ids': sources, 'run': str(run.pk)},
                    comment='Исправление названий должностей из штатного расписания; подразделения отделены от должности')
            result['renamed'] += len(rows)
            result['merged'] += len(sources)
        except ValidationError as exc:
            result['warnings'].append(allowed[key] + ': ' + '; '.join(exc.messages))
    return result
