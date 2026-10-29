"""Import job names from the verified ZUP staffing schedule only."""
from django.core.exceptions import ValidationError
from django.db import transaction
from apps.resources.models import Brigade
from apps.audit.models import AuditLog


def supported(table):
    columns = {col['column']: col for col in table['columns']}
    return (table['schema']=='dbo' and table['table']=='_Reference394'
            and table['name']=='Справочник.ШтатноеРасписание'
            and all(name in columns for name in ['_IDRRef','_Description','_Marked','_Fld31544','_Fld31546'])
            and columns['_Fld31544']['name']=='Утверждена'
            and columns['_Fld31546']['name']=='Закрыта')


@transaction.atomic
def apply_names(run, report):
    from .staffing_sync import snapshot_states, sync_states
    states = snapshot_states(report)
    names = report.get('position_names')
    if not isinstance(names, list) or len(names) > 100000:
        raise ValidationError('Некорректный список должностей из 1С.')
    if not names and states is None:
        source=report.get('position_source',{})
        raise ValidationError('Должности не загружены: источник вернул 0 записей после отбора. '
                              f"Всего в {source.get('table','dbo._Reference394')}: {source.get('total','неизвестно')}; "
                              f"без пометки удаления: {source.get('not_marked','неизвестно')}; "
                              f"закрыты / исключены среди них: {source.get('excluded','неизвестно')}; "
                              f"подходят отбору: {source.get('eligible','неизвестно')}. "
                              'Проверьте показатели источника в результате задания.')
    normalized = {}
    empty = 0
    for row in names:
        name = row.get('name') if isinstance(row, dict) else None
        if not isinstance(name, str):
            raise ValidationError('Некорректное наименование должности.')
        if report.get('positions_snapshot_version')==2:
            from apps.resources.employee_staffing import staffing_parts
            name=staffing_parts(name)[0]
        name = name.strip()
        if not name:
            empty += 1
            continue
        if len(name) > Brigade._meta.get_field('name').max_length:
            raise ValidationError(f'Название длиннее 150 символов: {name[:70]}. Загрузка отменена целиком.')
        normalized.setdefault(name.casefold(), name)
    if not normalized and states is None:raise ValidationError('Должности не загружены: все полученные наименования пустые.')
    # Serialize imports for this company, including other worker processes.
    from .models import Company
    Company.objects.select_for_update().get(pk=run.connection.company_id)
    repair = None
    if report.get('positions_snapshot_version') == 2:
        from apps.resources.staffing_name_repair import repair_staffing_names
        repair = repair_staffing_names(run, normalized.values())
    existing = {name.strip().casefold() for name in Brigade.objects.filter(company_id=run.connection.company_id).values_list('name', flat=True)}
    created = 0
    for key, name in normalized.items():
        if key in existing:
            continue
        Brigade.objects.create(company_id=run.connection.company_id, name=name)
        created += 1
    result = {'created': created, 'existing': len(set(normalized) & existing),
              'duplicates': len(names) - empty - len(normalized), 'empty': empty}
    staffing = sync_states(run, states if states is not None else {key:True for key in normalized}, complete=states is not None)
    if states is not None:result['staffing'] = staffing
    if repair is not None:result['staffing_name_repair'] = repair
    AuditLog.objects.create(user=run.created_by, action='CREATE', entity_type='resources.Brigade',
                            entity_id=str(run.pk), new_value={**result, 'staffing':staffing},
                            comment='Загрузка актуальных наименований должностей из 1С')
    return result


def source_confirmed(connection):
    """Only enable imports for the confirmed table in the current connection."""
    latest=connection.runs.filter(status='DONE',server=connection.server,database=connection.database).exclude(report__has_key='positions_import').exclude(report__has_key='employees_import').order_by('-created_at').first()
    if latest is None:return False
    key='dbo._Reference394'
    label=latest.report.get('metadata_labels',{}).get(key,{})
    if (connection.metadata_server,connection.metadata_database)==(connection.server,connection.database):
        manual=connection.metadata_labels.get(key,{})
        label={**label,**manual,'columns':{**label.get('columns',{}),**manual.get('columns',{})}}
    columns=[{**column,'name':label.get('columns',{}).get(column.get('column'),'')} for column in latest.report.get('schema',[]) if column.get('schema')=='dbo' and column.get('table')=='_Reference394']
    return supported({'schema':'dbo','table':'_Reference394','name':label.get('name',''),'columns':columns})


def enqueue_due_positions(now):
    from .one_c_schedules import enqueue_due_imports
    enqueue_due_imports(now, kinds=('positions',))
