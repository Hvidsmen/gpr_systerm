"""Period-sized projections of immutable snapshots, without Python decoding the full plan."""
import logging
from time import perf_counter
from django.db import connection
from .models import GlobalPlanVersion, ReportSnapshotProjection


def period_snapshot(version, start, end, *, member=None, source_key="version"):
    if connection.vendor != 'sqlite' or (member is None and version.status not in ('APPROVED', 'COMPLETED')):
        return member.snapshot if member is not None else version.snapshot
    started = perf_counter()
    log = logging.getLogger("gpr.report")
    log.warning("GPR projection: lookup version=%s period=%s — %s", version.pk, start, end)
    revision = version.approval_round
    cached = ReportSnapshotProjection.objects.filter(version_id=version.pk, approval_round=revision,
                                                     source_key=source_key, start=start, end=end).first()
    if cached:
        log.warning("GPR projection: cached version=%s %.1fs", version.pk, perf_counter()-started)
        return cached.snapshot
    # SQLite performs JSON projection in its native engine. The 10M-row source is
    # never transferred to or deserialized by Python. Keep all work metadata so
    # unplanned actuals and composite norms still use their frozen specification.
    table = connection.ops.quote_name(type(member)._meta.db_table if member is not None else GlobalPlanVersion._meta.db_table)
    work_arrays = []
    parameters = []
    for field in ('daily', 'plans'):
        work_arrays.append("'$.%s', json(COALESCE((SELECT json_group_array(json(d.value)) FROM json_each(w.value, '$.%s') d WHERE json_extract(d.value, '$.date') BETWEEN %%s AND %%s), '[]'))" % (field, field))
        parameters.extend((start.isoformat(), end.isoformat()))
    work_sql = ', '.join(work_arrays)
    resource_sql = []
    for kind in ('labor', 'equipment', 'fuel'):
        resource_sql.append("'$.resources.%s', json(COALESCE((SELECT json_group_array(json(r.value)) FROM json_each(p.snapshot, '$.resources.%s') r WHERE json_extract(r.value, '$.date') BETWEEN %%s AND %%s), '[]'))" % (kind, kind))
        parameters.extend((start.isoformat(), end.isoformat()))
    sql = "SELECT json_set(json_remove(p.snapshot, '$.monthly_inputs.works'), '$.works', json(COALESCE((SELECT json_group_array(json(json_set(json_remove(w.value, '$.context_plans'), " + work_sql + "))) FROM json_each(p.snapshot, '$.works') w), '[]')), " + ', '.join(resource_sql) + ') FROM ' + table + ' p WHERE p.id = %s'
    parameters.append(member.pk if member is not None else version.pk)
    target = connection.ops.quote_name(ReportSnapshotProjection._meta.db_table)
    insert = f"INSERT OR IGNORE INTO {target} (version_id, approval_round, source_key, start, end, snapshot) "
    sql = sql.replace("SELECT json_set", "SELECT %s, %s, %s, %s, %s, json_set", 1)
    log.warning("GPR projection: building in SQLite version=%s", version.pk)
    with connection.cursor() as cursor:
        cursor.execute(insert + sql, [version.pk, revision, source_key, start.isoformat(), end.isoformat(), *parameters])
    log.warning("GPR projection: stored version=%s %.1fs", version.pk, perf_counter()-started)
    return ReportSnapshotProjection.objects.get(
        version_id=version.pk, approval_round=revision, source_key=source_key, start=start, end=end).snapshot


def work_snapshot_spec(source, work_id):
    """Load one frozen work for historical detail, not the entire plan."""
    if connection.vendor != 'sqlite':
        return next((spec for spec in source.snapshot.get('works', []) if spec['id'] == work_id), None)
    import json
    record = source.member if source.member is not None else source.version
    table = connection.ops.quote_name(type(record)._meta.db_table)
    with connection.cursor() as cursor:
        cursor.execute(f"SELECT w.value FROM {table} p, json_each(p.snapshot, '$.works') w WHERE p.id = %s AND json_extract(w.value, '$.id') = %s LIMIT 1", [record.pk, work_id])
        row = cursor.fetchone()
    return json.loads(row[0]) if row else None
