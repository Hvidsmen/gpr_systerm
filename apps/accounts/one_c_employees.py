"""Import employee cards from the verified ZUP schema; never infer enum labels."""
import re
from datetime import date
from decimal import Decimal, InvalidOperation
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from apps.resources.models import Employee, Position
from .models import Company

EXPECTED = {
    '_Reference297': ('Справочник.Сотрудники', {'_Fld30276RRef': 'ФизическоеЛицо', '_Fld30277RRef': 'ГоловнаяОрганизация', '_Fld30280': 'ВАрхиве'}),
    '_Reference383': ('Справочник.ФизическиеЛица', {'_Fld31276': 'Фамилия', '_Fld31277': 'Имя', '_Fld31278': 'Отчество', '_Fld31265': 'ФИО'}),
    '_Reference166': ('Справочник.Организации', {}),
    '_Reference216': ('Справочник.ПодразделенияОрганизаций', {}),
    '_Reference92': ('Справочник.Должности', {}),
    '_InfoRg21744': ('РегистрСведений.ТекущиеКадровыеДанныеСотрудников', {'_Fld21746RRef': 'Сотрудник', '_Fld21748RRef': 'ТекущаяОрганизация', '_Fld21749RRef': 'ТекущееПодразделение', '_Fld21750RRef': 'ТекущаяДолжность'}),
    '_Reference394': ('Справочник.ШтатноеРасписание', {'_Fld31539RRef': 'Подразделение', '_Fld31540RRef': 'Должность'}),
    '_Reference75': ('Справочник.ГрафикиРаботыСотрудников', {}),
    '_InfoRg18183': ('РегистрСведений.КадроваяИсторияСотрудниковИнтервальный', {
        '_Fld18184RRef': 'Сотрудник', '_Fld18186': 'ДатаНачала', '_Fld18189': 'ПериодЗаписи',
        '_Fld18193RRef': 'Организация', '_Fld18194RRef': 'Должность', '_Fld18198RRef': 'Подразделение',
        '_Fld18199RRef': 'ДолжностьПоШтатномуРасписанию', '_Fld18200': 'КоличествоСтавок', '_Fld18205': 'ПустойИнтервал'}),
    '_InfoRg18156': ('РегистрСведений.КадроваяИсторияСотрудников', {
        '_Period': 'Период', '_Active': 'Активность записи', '_Fld18157RRef': 'Сотрудник',
        '_Fld18160RRef': 'Организация', '_Fld18161RRef': 'Подразделение', '_Fld18162RRef': 'Должность',
        '_Fld18163RRef': 'ДолжностьПоШтатномуРасписанию', '_Fld18164': 'КоличествоСтавок'}),
    '_InfoRg16734': ('РегистрСведений.ГрафикРаботыСотрудников', {
        '_Period': 'Период', '_Active': 'Активность записи', '_Fld16735RRef': 'Сотрудник',
        '_Fld16737RRef': 'ГрафикРаботы', '_Fld16738': 'ДействуетДо'}),
    '_InfoRg20185': ('РегистрСведений.ПлановыйФОТИтоги', {
        '_Period': 'Период', '_Fld20186RRef': 'Сотрудник', '_Fld20187': 'ДатаОкончания',
        '_Fld20193': 'ТарифнаяСтавка', '_Fld20194': 'СовокупнаяТарифнаяСтавка',
        '_Fld20195': 'ФОТ', '_Fld20197': 'Используется'}),
}
EXPECTED['_Reference394'][1]['_OwnerIDRRef'] = 'Владелец'
EXPECTED['_Reference383'][1].update({'_Fld31257': 'ДатаРождения', '_Fld31259': 'ИНН', '_Fld31260': 'СтраховойНомерПФР', '_Fld31261': 'МестоРождения'})
EXPECTED['_InfoRg21744'][1].update({'_Fld21751': 'ДатаУвольнения', '_Fld21754': 'ДатаПриема', '_Fld45771': 'КоличествоСтавок'})
EXPECTED['_InfoRg18183'][1]['_Fld18203_RRRef'] = 'РегистраторЗаписи'
EXPECTED['_Reference297'][1]['_Fld30282RRef'] = 'ГоловнойСотрудник'
EXPECTED['_InfoRg18211'] = ('РегистрСведений.КадроваяИсторияСотрудниковИспр', {
    '_Fld18213': 'ПериодИзмерение', '_Fld18214RRef': 'Сотрудник', '_Fld18222RRef': 'Должность'})
EXPECTED['_InfoRg20861'] = ('РегистрСведений.РеестрКадровыхПриказов', {
    '_Active': 'Активность записи', '_Fld20862RRef': 'Сотрудник', '_Fld20867': 'Дата',
    '_Fld20869_RRRef': 'Должность', '_Fld20869_S': 'Должность'})
EXPECTED['_InfoRg16829'] = ('РегистрСведений.ДанныеДляПодбораСотрудников', {
    '_Fld16831RRef': 'Сотрудник', '_Fld16837RRef': 'Подразделение', '_Fld39554RRef': 'Организация',
    '_Fld16838RRef': 'Должность', '_Fld16839RRef': 'ДолжностьПоШтатномуРасписанию',
    '_Fld16842': 'КоличествоСтавок', '_Fld16846': 'Начало', '_Fld16847': 'Окончание',
    '_Fld39556': 'ПометкаУдаления', '_Fld39557': 'ВАрхиве'})
EXPECTED['_InfoRg40095'] = ('РегистрСведений.ЗанятостьПозицийШтатногоРасписанияИнтервальный', {
    '_Fld40096RRef': 'Сотрудник', '_Fld40097': 'ДатаОкончания', '_Fld40098': 'ДатаНачала',
    '_Fld40101_RRRef': 'ДокументОснование', '_Fld40105RRef': 'ПозицияШтатногоРасписания',
    '_Fld40107': 'КоличествоСтавок', '_Fld40108': 'ДействуетДо', '_Fld40113': 'ПустойИнтервал'})
EXPECTED['_InfoRg59135'] = ('РегистрСведений.ядс_ЗначенияПоказателейВРазрезеСистемОплатыТруда', {
    '_Period':'Период', '_Fld59138RRef':'Сотрудник', '_Fld59139RRef':'Должность'})
PROBES = ('current_row', 'current_position_ref', 'current_position_join', 'interval_position_join',
          'history_position_join', 'staffing_position_join', 'corrected_history_position_ref',
          'order_position', 'head_employee_position_ref', 'picker_candidate', 'picker_position_join', 'picker_ambiguous',
          'staffing_occupancy_position_ref', 'custom_position_ref', 'occupancy_candidate', 'occupancy_ambiguous',
          'occupancy_staffing_join', 'occupancy_position_ref', 'occupancy_position_join', 'occupancy_staffing_named',
          'occupancy_position_row', 'occupancy_department_row', 'occupancy_department_named', 'staffing_display_fallback')

# Only these extra, validated fields can enter the card. Personal/pay data is
# rendered solely for ADMIN/HR_HEAD; it is never added to list/job/audit output.
CARD_TEXT = {'inn': 12, 'snils': 30, 'birth_place': 500, 'schedule': 250,
             'position_source': 100, 'staffing_position': 250, 'staffing_source': 100}
CARD_DATES = {'birth_date', 'hire_date', 'dismissal_date', 'position_since', 'pay_since', 'staffing_since'}
CARD_NUMBERS = {'fte', 'tariff_rate', 'combined_tariff_rate', 'payroll_fund'}


POSITION_STATES = {'job', 'staffing', 'ambiguous', 'no_staffing_assignment', 'no_position_link', 'missing_job_record', 'empty_job_name'}

def card_values(item):
    result = {}
    for key, limit in CARD_TEXT.items():
        value = item.get(key)
        if value is None:
            continue
        if not isinstance(value, str) or len(value.strip()) > limit:
            raise ValidationError('Некорректное текстовое поле карточки ЗУП. Изменения не внесены.')
        if value.strip():
            result[key] = value.strip()
    for key in CARD_DATES:
        value = item.get(key)
        if value in (None, ''):
            continue
        try:
            if not isinstance(value, str) or len(value) != 10:
                raise ValueError
            parsed = date.fromisoformat(value)
            if not 1900 <= parsed.year <= 2199:
                raise ValueError
        except (ValueError, TypeError):
            raise ValidationError('Некорректная дата карточки ЗУП. Проверьте смещение дат источника. Изменения не внесены.')
        result[key] = parsed.isoformat()
    for key in CARD_NUMBERS:
        value = item.get(key)
        if value is None or value == '':
            continue
        try:
            if isinstance(value, bool) or len(str(value)) > 40:
                raise ValueError
            number = Decimal(str(value))
            if not number.is_finite() or not 0 <= number <= Decimal('999999999999.99') or number.as_tuple().exponent < -6:
                raise ValueError
            result[key] = format(number.normalize(), 'f')
        except (InvalidOperation, ValueError):
            raise ValidationError('Некорректное числовое поле карточки ЗУП. Изменения не внесены.')
    state = item.get('position_state')
    if state not in (None, ''):
        if not isinstance(state, str) or state not in POSITION_STATES:
            raise ValidationError('Некорректное состояние проверки должности ЗУП.')
        result['position_state'] = state
    return result


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
        if table in {'_InfoRg18156', '_InfoRg16734'}:
            standard |= {'_RecorderRRef', '_LineNo'}
        if label.get('name') != name or not standard.union(fields).issubset(columns):
            return False
        # Standard register/owner columns have system names in SQL, not
        # custom metadata labels. Their physical presence is checked above.
        if any(label.get('columns', {}).get(key) != value for key, value in fields.items() if key not in {'_Period', '_Active', '_OwnerIDRRef'}):
            return False
    if not any(c.get('schema') == 'dbo' and c.get('table') == '_YearOffset' and c.get('column') == 'Offset' for c in run.report.get('schema', [])):
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
    catalogue = report.get('position_catalog')
    if catalogue is not None:
        if (not isinstance(catalogue, dict) or set(catalogue) != {'position_rows', 'position_named'} or
                any(type(value) is not int or not 0 <= value <= 1000000000 for value in catalogue.values()) or
                catalogue['position_named'] > catalogue['position_rows']):
            raise ValidationError('Некорректная сводка справочника должностей ЗУП. Изменения не внесены.')
    normalized, seen = [], set()
    coverage = {'all': {key: 0 for key in PROBES}, 'active': {key: 0 for key in PROBES}}
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
        for source, target in [('full_name','zup_full_name'),('first_name', 'first_name'), ('last_name', 'last_name'), ('middle_name', 'middle_name'),
                               ('employee_number', 'employee_number'), ('organisation', 'zup_organisation'),
                               ('department', 'zup_department'), ('position', 'zup_position_name')]:
            value = item.get(source)
            if value is None:value = ''
            if not isinstance(value, str) or len(value.strip()) > Employee._meta.get_field(target).max_length:
                raise ValidationError('Некорректные или слишком длинные данные сотрудника. Изменения не внесены.')
            values[target] = value.strip()
        if item.get('archived') not in (0, 1, False, True):
            raise ValidationError('Некорректный признак архива сотрудника.')
        values['is_active'] = not bool(item['archived'])
        values['zup_card'] = card_values(item)
        row_probes = {}
        for probe in PROBES:
            value = item.get('probe_'+probe, 0)
            if value not in (0, 1, False, True):
                raise ValidationError('Некорректная сводка кадровых источников ЗУП.')
            row_probes[probe] = int(value)
            coverage['all'][probe] += int(value)
            if values['is_active']:
                coverage['active'][probe] += int(value)
        references = {}
        for name in ['staffing', 'position', 'department']:
            value = item.get(f'trace_{name}_ref')
            if value in (None, ''):continue
            if not isinstance(value, str) or not re.fullmatch('[0-9a-fA-F]{32}', value):
                raise ValidationError('Некорректная техническая ссылка должности ЗУП. Изменения не внесены.')
            if int(value, 16):references[name] = value.lower()
        if references or any('probe_' + probe in item for probe in PROBES) or 'position_state' in item:
            values['zup_card']['position_diagnostics'] = {'probes': row_probes, 'references': references}
        normalized.append((ref, values))
    source = {'company_id': connection.company_id, 'zup_server': run.server.casefold(), 'zup_database': run.database.casefold()}
    existing = {e.zup_ref: e for e in Employee.objects.select_for_update().filter(**source).exclude(zup_ref='')}
    positions = {}
    for position in Position.objects.filter(company_id=connection.company_id).select_related('planning_mapping__brigade'):
        mapping = getattr(position, 'planning_mapping', None)
        name = mapping.brigade.name if mapping and mapping.company_id == connection.company_id and mapping.brigade.company_id == connection.company_id else position.name
        positions.setdefault(name.strip().casefold(), []).append(position)
    counts = {'created': 0, 'updated': 0, 'unchanged': 0, 'archived': sum(not values['is_active'] for _, values in normalized),
              'without_position':sum(not (values['zup_position_name'] or values['zup_card'].get('staffing_position')) for _,values in normalized),
              'staffing_fallback':sum(not values['zup_position_name'] and bool(values['zup_card'].get('staffing_position')) for _,values in normalized),
              'incomplete_names':sum(not values['first_name'] or not values['last_name'] for _,values in normalized),
              'unnamed':sum(not any(values[name] for name in ['zup_full_name','first_name','last_name','middle_name']) for _,values in normalized)}
    counts['position_diagnostics'] = {'version': 1, 'employees': len(normalized),
        'active_employees': sum(values['is_active'] for _, values in normalized), 'coverage': coverage}
    if catalogue is not None:counts['position_diagnostics']['catalogue'] = catalogue
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
