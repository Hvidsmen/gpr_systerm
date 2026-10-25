"""Portable structure-only report for analysing a company's 1C database."""
from django.contrib.auth.decorators import login_required
from django.http import Http404, JsonResponse
from django.utils import timezone
from django.views.decorators.http import require_GET
from core.permissions import require_roles
from .models import OneCConnection
from .one_c_catalog import catalogue, TYPES


def structure_report(connection, run):
    labels = dict(run.report.get('metadata_labels', {}))
    if (connection.metadata_server, connection.metadata_database) == (run.server, run.database):
        for key, manual in connection.metadata_labels.items():
            prior = labels.get(key, {})
            labels[key] = {**prior, **manual, 'columns': {**prior.get('columns', {}), **manual.get('columns', {})}}
    rows, _, _ = catalogue(run.report, labels)
    tables = []
    for row in rows:
        columns = []
        for col in row['columns']:
            # Never export row samples, SQL logins or arbitrary worker report keys.
            columns.append({key: col.get(key) for key in (
                'column', 'name', 'type', 'max_length', 'is_nullable', 'mapped',
                'is_reference', 'targets', 'unresolved_references')})
        mapping = labels.get(row['key'], {})
        tables.append({
            'schema': row['schema'], 'table': row['table'], 'name': row['name'],
            'type': row['type'], 'type_label': dict(TYPES).get(row['type'], row['type']),
            'table_part': row['table_part'], 'source': row['source'], 'columns': columns,
            'references': mapping.get('references', {}),
            'self_targets': mapping.get('self_targets', []),
            'owner_targets': mapping.get('owner_targets', []),
        })
    column_count = sum(len(t['columns']) for t in tables)
    named_tables = sum(bool(t['name']) for t in tables)
    named_columns = sum(bool(c['name']) for t in tables for c in t['columns'])
    return {
        'format': 'gpr-1c-structure', 'format_version': 1,
        'generated_at': timezone.now().isoformat(),
        'diagnostic': {'id': str(run.pk), 'created_at': run.created_at.isoformat(),
                       'finished_at': run.finished_at.isoformat() if run.finished_at else None,
                       'server': run.server, 'database': run.database,
                       'metadata_status': run.report.get('metadata_status', '')},
        'summary': {'tables': len(tables), 'columns': column_count,
                    'tables_with_names': named_tables, 'tables_without_names': len(tables)-named_tables,
                    'columns_with_names': named_columns, 'columns_without_names': column_count-named_columns},
        'notes': [
            'Contains database structure only; no employee records or sample values.',
            'Empty names mean metadata has not been decoded; names are not guessed.',
            'Only references resolved by metadata are listed. Missing targets do not prove absence of a relationship.',
            'No uniqueness or business rules are inferred from field names. Employee-number scope must be verified before synchronisation.',
        ],
        'tables': tables,
    }


@login_required
@require_GET
def download_report(request):
    require_roles(request.user, {'ADMIN'})
    connection = OneCConnection.objects.filter(company_id=request.user.company_id).first()
    if connection is None:
        raise Http404('Сначала настройте подключение и выполните диагностику 1С.')
    run = connection.runs.filter(status='DONE', server=connection.server, database=connection.database).exclude(
        report__has_key='positions_import').order_by('-created_at', '-pk').first()
    if run is None or not run.report.get('schema'):
        raise Http404('Нет структуры для текущего подключения. Выполните диагностику 1С и повторите скачивание.')
    response = JsonResponse(structure_report(connection, run), json_dumps_params={'ensure_ascii': False, 'indent': 2})
    response['Content-Disposition'] = f'attachment; filename="1c-structure-{run.pk.hex}.json"'
    response['Cache-Control'] = 'private, no-store'
    response['X-Content-Type-Options'] = 'nosniff'
    return response
