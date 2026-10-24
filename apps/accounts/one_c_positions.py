"""First, name-only import from the confirmed ZUP position catalogue."""
from django.core.exceptions import ValidationError
from django.db import transaction
from apps.resources.models import Brigade
from apps.audit.models import AuditLog


def supported(table):
    columns = {col['column']: col for col in table['columns']}
    return (table['schema'] == 'dbo' and table['table'] == '_Reference92'
            and table['name'] == 'Справочник.Должности'
            and all(name in columns for name in ['_Description', '_Marked', '_Fld25711'])
            and columns['_Fld25711']['name'] == 'ИсключенаИзШтатногоРасписания')


@transaction.atomic
def apply_names(run, report):
    names = report.get('position_names')
    if not isinstance(names, list) or len(names) > 100000:
        raise ValidationError('Некорректный список должностей из 1С.')
    normalized = {}
    empty = 0
    for row in names:
        name = row.get('name') if isinstance(row, dict) else None
        if not isinstance(name, str):
            raise ValidationError('Некорректное наименование должности.')
        name = name.strip()
        if not name:
            empty += 1
            continue
        if len(name) > Brigade._meta.get_field('name').max_length:
            raise ValidationError(f'Название длиннее 150 символов: {name[:70]}. Загрузка отменена целиком.')
        normalized.setdefault(name.casefold(), name)
    # Serialize imports for this company, including other worker processes.
    from .models import Company
    Company.objects.select_for_update().get(pk=run.connection.company_id)
    existing = {name.strip().casefold() for name in Brigade.objects.filter(company_id=run.connection.company_id).values_list('name', flat=True)}
    created = 0
    for key, name in normalized.items():
        if key in existing:
            continue
        Brigade.objects.create(company_id=run.connection.company_id, name=name)
        created += 1
    result = {'created': created, 'existing': len(set(normalized) & existing),
              'duplicates': len(names) - empty - len(normalized), 'empty': empty}
    AuditLog.objects.create(user=run.created_by, action='CREATE', entity_type='resources.Brigade',
                            entity_id=str(run.pk), new_value=result,
                            comment='Загрузка актуальных наименований должностей из 1С')
    return result
