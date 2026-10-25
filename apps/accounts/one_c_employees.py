"""Import employee catalogue identities from the verified ZUP schema (no payroll)."""
import re
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from apps.resources.models import Employee, Position
from .models import Company

EXPECTED = {
    '_Reference297': ('Справочник.Сотрудники', {'_Fld30276RRef': 'ФизическоеЛицо', '_Fld30277RRef': 'ГоловнаяОрганизация', '_Fld30280': 'ВАрхиве'}),
    '_Reference383': ('Справочник.ФизическиеЛица', {'_Fld31276': 'Фамилия', '_Fld31277': 'Имя', '_Fld31278': 'Отчество'}),
    '_Reference166': ('Справочник.Организации', {}),
    '_Reference216': ('Справочник.ПодразделенияОрганизаций', {}),
    '_Reference92': ('Справочник.Должности', {}),
    '_InfoRg21744': ('РегистрСведений.ТекущиеКадровыеДанныеСотрудников', {'_Fld21746RRef': 'Сотрудник', '_Fld21748RRef': 'ТекущаяОрганизация', '_Fld21749RRef': 'ТекущееПодразделение', '_Fld21750RRef': 'ТекущаяДолжность'}),
}


def source_confirmed(connection):
    run = connection.runs.filter(status='DONE', server=connection.server, database=connection.database).exclude(
        report__has_key='positions_import').exclude(report__has_key='employees_import').order_by('-created_at').first()
    if run is None:
        return False
    labels = dict(run.report.get('metadata_labels', {}))
    if (connection.metadata_server, connection.metadata_database) == (connection.server, connection.database):
        for key, manual in connection.metadata_labels.items():
            previous = labels.get(key, {})
            labels[key] = {**previous, **manual, 'columns': {**previous.get('columns', {}), **manual.get('columns', {})}}
    for table, (name, fields) in EXPECTED.items():
        label = labels.get('dbo.' + table, {})
        columns = {c['column'] for c in run.report.get('schema', []) if c.get('schema') == 'dbo' and c.get('table') == table}
        standard = {'_IDRRef', '_Description'} if table.startswith('_Reference') else set()
        if table == '_Reference297':
            standard |= {'_Marked', '_Code'}
        if label.get('name') != name or not standard.union(fields).issubset(columns):
            return False
        if any(label.get('columns', {}).get(key) != value for key, value in fields.items()):
            return False
    return True


@transaction.atomic
def apply_employees(run, report):
    connection = run.connection
    Company.objects.select_for_update().get(pk=connection.company_id)
    connection.refresh_from_db()
    if (connection.server, connection.database) != (run.server, run.database) or not source_confirmed(connection):
        raise ValidationError('Подключение или подтверждённая структура ЗУП изменились. Выполните диагностику и повторите загрузку.')
    rows = report.get('employees')
    if 'employees' not in report:
        raise ValidationError('Обработчик 1С вернул отчёт без списка сотрудников. Обновите проект и фоновый портал, затем повторите загрузку. Изменения не внесены.')
    if not isinstance(rows, list):
        raise ValidationError('Неверный формат списка сотрудников из ЗУП: ожидался массив записей. Изменения не внесены.')
    if not rows:
        raise ValidationError('ЗУП вернула 0 сотрудников без пометки удаления. Проверьте источник и подключение. Изменения не внесены.')
    if len(rows) > 200000:
        raise ValidationError(f'Источник вернул {len(rows)} строк; лимит загрузки — 200 000. Изменения не внесены.')
    normalized, seen = [], set()
    for item in rows:
        if not isinstance(item, dict):
            raise ValidationError('Некорректная запись сотрудника.')
        ref = item.get('source_id', '')
        if not isinstance(ref, str) or not re.fullmatch('[0-9a-fA-F]{32}', ref) or int(ref, 16) == 0:
            raise ValidationError('Некорректный идентификатор сотрудника ЗУП.')
        ref = ref.lower()
        if ref in seen:
            raise ValidationError('Источник вернул несколько кадровых записей для одного сотрудника. Загрузка отменена: требуется проверить структуру текущих данных.')
        seen.add(ref)
        values = {}
        for source, target in [('first_name', 'first_name'), ('last_name', 'last_name'), ('middle_name', 'middle_name'),
                               ('employee_number', 'employee_number'), ('organisation', 'zup_organisation'),
                               ('department', 'zup_department'), ('position', 'zup_position_name')]:
            value = item.get(source) or ''
            if not isinstance(value, str) or len(value.strip()) > Employee._meta.get_field(target).max_length:
                raise ValidationError('Некорректные или слишком длинные данные сотрудника. Изменения не внесены.')
            values[target] = value.strip()
        if not values['last_name'] or not values['first_name']:
            raise ValidationError('В ЗУП есть сотрудник без заполненных имени или фамилии. Изменения не внесены; проверьте ФИО в источнике.')
        if item.get('archived') not in (0, 1, False, True):
            raise ValidationError('Некорректный признак архива сотрудника.')
        values['is_active'] = not bool(item['archived'])
        normalized.append((ref, values))
    source = {'company_id': connection.company_id, 'zup_server': run.server.casefold(), 'zup_database': run.database.casefold()}
    existing = {e.zup_ref: e for e in Employee.objects.select_for_update().filter(**source).exclude(zup_ref='')}
    positions = {}
    for position in Position.objects.filter(company_id=connection.company_id):
        positions.setdefault(position.name.strip().casefold(), []).append(position)
    counts = {'created': 0, 'updated': 0, 'unchanged': 0, 'archived': sum(not values['is_active'] for _, values in normalized)}
    now = timezone.now()
    for ref, values in normalized:
        matches = positions.get(values['zup_position_name'].casefold(), [])
        values['position'] = matches[0] if len(matches) == 1 else None
        employee = existing.get(ref)
        if employee is None:
            employee = Employee(**source, zup_ref=ref, **values)
            counts['created'] += 1
        else:
            changed = any(getattr(employee, key) != value for key, value in values.items())
            counts['updated' if changed else 'unchanged'] += 1
            for key, value in values.items():
                setattr(employee, key, value)
        employee.zup_synced_at = now
        employee.save()
    from apps.audit.models import AuditLog
    AuditLog.objects.create(user=run.created_by, action='UPDATE', entity_type='resources.Employee', entity_id=str(run.pk),
                            new_value=counts, comment='Загрузка списка сотрудников из ЗУП; ручные записи сохранены')
    return counts
