"""Verified SQL sources in the decoded UAT 1C configuration."""

WAYBILL = {
    'key': 'dbo._Document173X1',
    'metadata_key': 'dbo._Document173',
    'name': 'Документ.уатПутевойЛист',
    'columns': {
        '_IDRRef', '_Date_Time', '_Number', '_Posted', '_Marked', '_Fld995RRef',
        '_Fld996RRef', '_Fld1004', '_Fld1005', '_Fld1009', '_Fld1019RRef',
        '_Fld1023RRef', '_Fld1036', '_Fld1037', '_Fld1039RRef', '_Fld11747RRef',
    },
}

REPAIR_SHEET = {
    'key': 'dbo._Document178X1',
    'metadata_key': 'dbo._Document178',
    'name': 'Документ.уатРемонтныйЛист',
    'columns': {
        '_IDRRef', '_Date_Time', '_Number', '_Posted', '_Marked', '_Fld1230RRef',
        '_Fld1234', '_Fld1237', '_Fld1238', '_Fld1242', '_Fld1246RRef', '_Fld1252RRef',
        '_Fld1255', '_Fld1256RRef', '_Fld1257RRef', '_Fld1258RRef', '_Fld1260RRef',
        '_Fld1264RRef', '_Fld6808', '_Fld11816RRef', '_Fld11822RRef',
    },
}

PARTS = (
    ('dbo._Document173_VT1056X1', 'Задания путевого листа', {'_Document173_IDRRef', '_LineNo1057', '_Fld1058_S', '_Fld1058_RRRef', '_Fld1059_S', '_Fld1059_RRRef', '_Fld1062', '_Fld1063', '_Fld1064', '_Fld1065', '_Fld1073', '_Fld1074', '_Fld11758RRef'}),
    ('dbo._Document173_VT1102X1', 'Расход ГСМ', {'_Document173_IDRRef', '_LineNo1103', '_Fld1107', '_Fld1108RRef', '_Fld1109', '_Fld1110', '_Fld1111', '_Fld1112', '_Fld1113', '_Fld1114'}),
    ('dbo._Document178_VT1294X1', 'Работы по ремонту', {'_Document178_IDRRef', '_LineNo1295', '_Fld1297', '_Fld1298RRef', '_Fld1300', '_Fld1302', '_Fld7743'}),
    ('dbo._Document178_VT1281X1', 'Материалы', {'_Document178_IDRRef', '_LineNo1282', '_Fld1284', '_Fld1285RRef', '_Fld1287', '_Fld1289'}),
    ('dbo._Document178_VT1276X1', 'Исполнители', {'_Document178_IDRRef', '_LineNo1277', '_Fld1278', '_Fld1279', '_Fld1280RRef'}),
)

LOOKUPS = (
    ('dbo._Reference107X1', 'Транспортные средства', {'_IDRRef', '_Description', '_Fld4334'}),
    ('dbo._Reference61X1', 'Сотрудники УАТ', {'_IDRRef', '_Description'}),
    ('dbo._Reference55X1', 'Подразделения УАТ', {'_IDRRef', '_Description'}),
    ('dbo._Reference46X1', 'Номенклатура УАТ', {'_IDRRef', '_Code', '_Description'}),
    ('dbo._Reference71X1', 'Виды ремонта', {'_IDRRef', '_Description'}),
    ('dbo._Reference60X1', 'Склады УАТ', {'_IDRRef', '_Description'}),
    ('dbo._Reference49', 'Организации УАТ', {'_IDRRef', '_Description'}),
    ('dbo._Reference4650', 'Объекты строительства УАТ', {'_IDRRef', '_Description'}),
    ('dbo._Reference90', 'Пункты назначения УАТ', {'_IDRRef', '_Description'}),
    ('dbo._Reference91', 'Работы по ремонту УАТ', {'_IDRRef', '_Description', '_Fld4205'}),
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
        decoded = labels.get(source.get('metadata_key', key), {}).get('name', '')
        if key not in schema:
            missing.append(f'{label}: нет таблицы {key}')
        elif decoded != source['name']:
            missing.append(f'{label}: не подтверждено имя 1С')
        elif absent:
            missing.append(f'{label}: нет полей {", ".join(absent)}')
        sources.append({'label': label, 'key': key, 'name': decoded or source['name'], 'ready': not absent and decoded == source['name']})
    for key, label, required in (*PARTS, *LOOKUPS):
        if key not in schema:
            missing.append(f'{label}: нет таблицы {key}')
        else:
            absent = sorted(required - schema[key])
            if absent:
                missing.append(f'{label}: нет полей {", ".join(absent)}')
    return {'ready': not missing, 'missing': missing, 'sources': sources}
