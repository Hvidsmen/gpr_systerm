"""Readable 1C schema catalogue. Object names are never inferred from data matches."""
import re
from collections import Counter
from django.core.exceptions import ValidationError

TYPES=[('reference','Справочники'),('document','Документы'),('enum','Перечисления'),('information','Регистры сведений'),('accumulation','Регистры накопления'),('accounting','Регистры бухгалтерии'),('calculation','Регистры расчёта'),('characteristic','Планы видов характеристик'),('accounts','Планы счетов'),('calculation_types','Планы видов расчёта'),('business_process','Бизнес-процессы'),('exchange','Планы обмена'),('task','Задачи'),('constant','Константы'),('journal','Журналы документов'),('sequence','Последовательности'),('service','Служебные таблицы'),('other','Другие таблицы')]
PREFIXES=[('Reference','reference'),('DocumentJournal','journal'),('Document','document'),('Enum','enum'),('InfoRg','information'),('AccumRg','accumulation'),('AccRg','accounting'),('CalcRg','calculation'),('Chrc','characteristic'),('Acc','accounts'),('Calc','calculation_types'),('BusinessProcess','business_process'),('BPr','business_process'),('BP','business_process'),('Node','exchange'),('Task','task'),('Const','constant'),('Sequence','sequence')]
STANDARD={'_IDRRef':'Идентификатор ссылки','_Description':'Наименование','_Code':'Код','_Marked':'Пометка удаления','_ParentIDRRef':'Родитель','_OwnerIDRRef':'Владелец','_Folder':'Группа / элемент','_Date_Time':'Дата документа','_Number':'Номер документа','_Posted':'Проведён','_Period':'Период','_Active':'Активность записи','_RecorderRRef':'Регистратор','_RecorderTRef':'Тип регистратора','_RecordKind':'Вид движения','_Version':'Версия данных'}

def table_type(name):
    for prefix,kind in PREFIXES:
        if re.match(r'^_'+prefix+r'(?:[A-Za-z]+)?\d+(?:_|$)',name,re.I):return kind
    if name.casefold() in {'config','configsave','params','dbschema','ibversion','files','users','v8users','schemastorage','dbschema'}:return 'service'
    return 'other'

def catalogue(report,labels=None,query='',kind=''):
    labels=labels or {};tables={}
    for column in report.get('schema',[]):
        schema=str(column.get('schema','dbo'));table=str(column.get('table',''));key=schema+'.'+table
        if not table:continue
        if key not in tables:
            mapping=labels.get(key,{})
            tables[key]={'schema':schema,'table':table,'key':key,'name':mapping.get('name',''),'source':mapping.get('source',''),'type':table_type(table),'table_part':bool(re.search(r'_VT\d+',table,re.I)),'columns':[]}
        mapping=labels.get(key,{}).get('columns',{})
        name=column.get('column','')
        tables[key]['columns'].append({**column,'name':mapping.get(name,STANDARD.get(name,'Номер строки' if re.fullmatch(r'_LineNo\d+',name) else '')),'mapped':name in mapping})
    for key,row in tables.items():
        mapping=labels.get(key,{})
        for column in row['columns']:
            name=column['column']
            destinations=mapping.get('references',{}).get(name,[])
            if name=='_ParentIDRRef':destinations=mapping.get('self_targets',[])
            elif name=='_OwnerIDRRef':destinations=mapping.get('owner_targets',[])
            elif re.fullmatch(r'_[A-Za-z]+\d+_IDRRef',name) and row['table_part']:destinations=mapping.get('self_targets',[])
            column['targets']=[{'schema':tables[target]['schema'],'table':tables[target]['table'],'name':tables[target]['name'] or target} for target in destinations if target in tables]
            column['unresolved_references']=mapping.get('unresolved_references',{}).get(name,0)
            column['is_reference']=bool(column['targets'] or column['unresolved_references'] or re.search(r'(?:RRef|RRRef|TRef)$',name))
    counts=Counter(row['type'] for row in tables.values())
    terms=query.casefold().split()
    rows=[row for row in tables.values() if (not kind or row['type']==kind) and all(term in (row['name']+' '+row['key']).casefold() for term in terms)]
    order={code:i for i,(code,_) in enumerate(TYPES)}
    rows.sort(key=lambda row:(order[row['type']],(row['name'] or row['table']).casefold(),row['schema']))
    return rows,[(code,label,counts[code]) for code,label in TYPES if counts[code]],len(tables)

def parse_mapping(data,report):
    if not isinstance(data,dict) or not isinstance(data.get('tables'),list):raise ValidationError('Нужен JSON с массивом tables. Пример можно скачать на странице.')
    available={}
    for row in report.get('schema',[]):available.setdefault(str(row.get('schema','dbo'))+'.'+str(row.get('table','')),set()).add(row.get('column'))
    if len(data['tables'])>30000:raise ValidationError('Слишком много таблиц в файле.')
    result={}
    for row in data['tables']:
        if not isinstance(row,dict):raise ValidationError('Неверная запись таблицы.')
        key=str(row.get('schema','dbo'))+'.'+str(row.get('table',''))
        if key not in available:raise ValidationError('Таблица отсутствует в последней диагностике: '+key)
        if key in result:raise ValidationError('Таблица повторяется: '+key)
        name=row.get('name','')
        if not isinstance(name,str) or not name.strip() or len(name)>500:raise ValidationError('Для каждой таблицы нужно читаемое имя до 500 символов.')
        columns=row.get('columns',{})
        if not isinstance(columns,dict):raise ValidationError('columns должен быть объектом: техническое поле → имя поля.')
        for physical,label in columns.items():
            if physical not in available[key] or not isinstance(label,str) or not label.strip() or len(label)>500:raise ValidationError('Неизвестное поле или некорректное имя: '+key+'.'+str(physical))
        result[key]={'name':name.strip(),'columns':{k:v.strip() for k,v in columns.items()},'source':'Импорт соответствий метаданных 1С'}
    if not result:raise ValidationError('В файле нет соответствий.')
    return result
