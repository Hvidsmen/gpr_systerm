"""Verified SQL sources in the decoded UAT 1C configuration."""

WAYBILL = {
    'key': 'dbo._Document173',
    'name': 'Документ.уатПутевойЛист',
    'columns': {
        '_IDRRef', '_Date_Time', '_Number', '_Posted', '_Marked', '_Fld995RRef',
        '_Fld996RRef', '_Fld1004', '_Fld1005', '_Fld1009', '_Fld1019RRef',
        '_Fld1023RRef', '_Fld1036', '_Fld1037', '_Fld1039RRef', '_Fld11747RRef',
    },
}

REPAIR_SHEET = {
    'key': 'dbo._Document178',
    'name': 'Документ.уатРемонтныйЛист',
    'columns': {
        '_IDRRef', '_Date_Time', '_Number', '_Posted', '_Marked', '_Fld1230RRef',
        '_Fld1234', '_Fld1237', '_Fld1238', '_Fld1242', '_Fld1246RRef', '_Fld1252RRef',
        '_Fld1255', '_Fld1256RRef', '_Fld1257RRef', '_Fld1258RRef', '_Fld1260RRef',
        '_Fld1264RRef', '_Fld6808', '_Fld11816RRef', '_Fld11822RRef',
    },
}

PARTS = (
    ('dbo._Document173_VT1056', 'Задания путевого листа', {'_Document173_IDRRef', '_LineNo1057', '_Fld1058_S', '_Fld1058_RRRef', '_Fld1059_S', '_Fld1059_RRRef', '_Fld1062', '_Fld1063', '_Fld1064', '_Fld1065', '_Fld1073', '_Fld1074', '_Fld11758RRef'}),
    ('dbo._Document173_VT1102', 'Расход ГСМ', {'_Document173_IDRRef', '_LineNo1103', '_Fld1107', '_Fld1108RRef', '_Fld1109', '_Fld1110', '_Fld1111', '_Fld1112', '_Fld1113', '_Fld1114'}),
    ('dbo._Document178_VT1294', 'Работы по ремонту', {'_Document178_IDRRef', '_LineNo1295', '_Fld1297', '_Fld1298RRef', '_Fld1300', '_Fld1302', '_Fld7743'}),
    ('dbo._Document178_VT1281', 'Материалы', {'_Document178_IDRRef', '_LineNo1282', '_Fld1284', '_Fld1285RRef', '_Fld1287', '_Fld1289'}),
    ('dbo._Document178_VT1276', 'Исполнители', {'_Document178_IDRRef', '_LineNo1277', '_Fld1278', '_Fld1279', '_Fld1280RRef'}),
)


def status(report):
    """Confirm that the saved structure is the exact supported UAT layout."""
    schema = {}
    for column in report.get('schema', []):
        key = f"{column.get('schema', 'dbo')}.{column.get('table', '')}"
        schema.setdefault(key, set()).add(column.get('column', ''))
    labels = report.get('metadata_labels', {})
    missing = []
    sources = []
    for source, label in ((WAYBILL, 'Путевые листы'), (REPAIR_SHEET, 'Ремонтные листы')):
        key = source['key']
        absent = sorted(source['columns'] - schema.get(key, set()))
        decoded = labels.get(key, {}).get('name', '')
        if key not in schema:
            missing.append(f'{label}: нет таблицы {key}')
        elif decoded != source['name']:
            missing.append(f'{label}: не подтверждено имя 1С')
        elif absent:
            missing.append(f'{label}: нет полей {", ".join(absent)}')
        sources.append({'label': label, 'key': key, 'name': decoded or source['name'], 'ready': not absent and decoded == source['name']})
    for key, label, required in PARTS:
        if key not in schema:
            missing.append(f'{label}: нет таблицы {key}')
        else:
            absent = sorted(required - schema[key])
            if absent:
                missing.append(f'{label}: нет полей {", ".join(absent)}')
    return {'ready': not missing, 'missing': missing, 'sources': sources}
