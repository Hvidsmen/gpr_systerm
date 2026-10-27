"""Sync the verified ZUP job exclusion flag independently of employee occupancy."""
import re
from django.core.exceptions import ValidationError
from django.utils import timezone
from apps.resources.models import Brigade, BrigadeMerge


def snapshot_states(report):
    if 'position_records' not in report:
        return None
    rows = report['position_records']; summary = report.get('position_source', {})
    if report.get('positions_snapshot_version') != 1 or not isinstance(rows, list) or not 0 < len(rows) <= 100000:
        raise ValidationError('Некорректный полный снимок штатных должностей ЗУП.')
    states = {}; seen = set(); counts = dict(total=len(rows), not_marked=0, excluded=0, eligible=0)
    for row in rows:
        if not isinstance(row, dict) or not re.fullmatch(r'[0-9a-fA-F]{32}', str(row.get('ref', ''))):
            raise ValidationError('В снимке должностей отсутствует идентификатор ЗУП.')
        ref = row['ref'].lower()
        if ref in seen: raise ValidationError('В снимке должностей повторяется идентификатор ЗУП.')
        seen.add(ref)
        if row.get('marked') not in ('0x00', '0x01') or row.get('excluded') not in ('0x00', '0x01'):
            raise ValidationError('Неизвестный признак удаления или исключения из штатного расписания.')
        name = row.get('name')
        if not isinstance(name, str) or len(name.strip()) > 150:
            raise ValidationError('Некорректное наименование штатной должности.')
        active = row['marked'] == '0x00' and row['excluded'] == '0x00'
        counts['not_marked'] += row['marked'] == '0x00'
        counts['excluded'] += row['marked'] == '0x00' and row['excluded'] == '0x01'
        counts['eligible'] += active
        if name.strip():
            key = name.strip().casefold()
            states[key] = states.get(key, False) or active
    if any(summary.get(key) != value for key, value in counts.items()):
        raise ValidationError('Снимок штатных должностей неполный или изменился во время чтения. Повторите загрузку.')
    names = report.get('position_names')
    if not isinstance(names, list) or any(not isinstance(row, dict) or not isinstance(row.get('name'), str) for row in names):
        raise ValidationError('Некорректный список актуальных должностей.')
    expected = {row['name'].strip().casefold() for row in names if row['name'].strip()}
    if expected != {key for key, active in states.items() if active}:
        raise ValidationError('Список актуальных должностей не соответствует полному снимку ЗУП.')
    return states


def sync_states(run, states, *, complete):
    if (run.server.casefold(), run.database.casefold()) != (run.connection.server.casefold(), run.connection.database.casefold()):
        raise ValidationError('Подключение к ЗУП изменилось. Запустите новую загрузку штатных должностей.')
    company_id = run.connection.company_id
    rows = list(Brigade.objects.filter(company_id=company_id))
    aliases = dict(BrigadeMerge.objects.filter(company_id=company_id).values_list('source_id', 'target_id'))
    status = {}
    for row in rows:
        key = row.name.strip().casefold()
        if key not in states: continue
        target = row.pk; seen = set()
        while target in aliases and target not in seen:
            seen.add(target); target = aliases[target]
        status[target] = status.get(target, False) or states[key]
    now = timezone.now(); source = run.server.casefold() + '/' + run.database.casefold()
    if complete:
        # Only previously synchronized rows are retired when a complete snapshot omits them.
        for row in rows:
            if row.pk not in aliases and row.staffing_synced_at and row.pk not in status:
                status[row.pk] = False
    for current in (True, False):
        ids = [pk for pk, value in status.items() if value is current]
        Brigade.objects.filter(company_id=company_id, pk__in=ids).update(staffing_current=current, staffing_synced_at=now, staffing_source=source)
    return {'current':sum(status.values()), 'not_current':len(status)-sum(status.values()), 'updated':len(status)}
