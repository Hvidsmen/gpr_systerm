"""Typed daily fields, while keeping the workbook's exact source values."""
import re
from datetime import date,datetime
from collections import defaultdict

REPAIR_STAGES=[('','Не требуется / не уточнено'),('WAIT_REPAIR','Ожидает ремонта'),('WAIT_PARTS','Ожидает запчастей'),('REPAIR','В ремонте')]
STATUS_KINDS=[('REQUEST','Заявки'),('PARTS','Поставка запчастей'),('MAINTENANCE','ТО и готовность')]
STATUS_SEEDS={'REQUEST':['На согласовании','Согласована','На исполнении','Оплачена','Не оплачена','Выполнена'],
 'PARTS':['Не заказаны','Заказаны','В пути','Доставлены','Ожидание перемещения'],
 'MAINTENANCE':['ТО запланировано','ТО проведено · готова','ТО проведено · не готова','ТО не проведено · не готова']}
SOURCE_DETAIL_FIELDS=[('shift_one','1-я смена'),('shift_two','2-я смена'),('in_gpr','В ГПР'),('repair_status','Состояние'),('defects','Неисправности'),('repair_date','Дата постановки на ремонт'),('request_details','Номер и дата заявки'),('request_status','Статус заявки'),('parts_status','Поставка запчастей'),('expected_parts_date','Дата поставки запчастей'),('maintenance','ТО и готовность'),('comment','Примечание')]
DETAIL_FIELDS=[('shift_one_employee','1-я смена · водитель / машинист'),('shift_two_employee','2-я смена · водитель / машинист'),('in_gpr','Участвует в ГПР'),('repair_stage','Этап ремонта'),('defects','Неисправности и поломки'),('repair_started_on','Дата постановки на ремонт'),('request_details','Номер заявки / ссылки'),('request_date','Дата заявки'),('request_stage','Статус заявки'),('parts_stage','Поставка запчастей'),('parts_expected_on','Плановая дата поставки запчастей'),('maintenance_stage','ТО и готовность'),('comment','Примечание')]
ROSTER_DETAIL_FIELDS=('shift_one_employee','shift_two_employee','in_gpr')
REPAIR_FIELD_GROUPS=(
    ('Ремонт',('repair_stage','repair_started_on','defects')),
    ('Заявки и запчасти',('request_details','request_date','request_stage','parts_stage','parts_expected_on')),
    ('ТО и примечания',('maintenance_stage','comment')),
)


def parse_date(value):
    if isinstance(value,datetime):return value.date()
    if isinstance(value,date):return value
    value=str(value or '').strip()
    if re.fullmatch(r'\d{4}-\d{2}-\d{2}(?:T00:00:00)?',value):
        try:return date.fromisoformat(value[:10])
        except ValueError:return None
    match=re.fullmatch(r'(\d{1,2})[./](\d{1,2})[./](\d{2}|\d{4})\s*(?:г\.?|года)?\s*',value,re.I)
    if match:
        day,month,year=map(int,match.groups());year+=2000 if year<100 else 0
        try:return date(year,month,day)
        except ValueError:return None
    return None


def lookup_context(company):
    from apps.resources.models import Employee
    from .transport_inline import ensure_statuses
    ensure_statuses(company)
    from .transport_details_models import EquipmentServiceStatus
    people=defaultdict(list)
    for employee in Employee.objects.filter(company=company,is_active=True):people[' '.join(employee.display_name.split()).casefold()].append(employee)
    options={(o.kind,o.name):o for o in EquipmentServiceStatus.objects.filter(company=company,is_active=True)}
    return people,options


def from_source(company,data,lookups=None):
    people,options=lookups or lookup_context(company)
    result={'source_details':{name:data.get(name,'') for name,_ in SOURCE_DETAIL_FIELDS},
        'in_gpr':{'да':True,'нет':False}.get(str(data.get('in_gpr','')).strip().casefold()),
        'defects':data.get('defects',''),'request_details':data.get('request_details',''),'comment':data.get('comment',''),
        'repair_started_on':parse_date(data.get('repair_date')),'parts_expected_on':parse_date(data.get('expected_parts_date')),
        'repair_stage':{'в ожидании р':'WAIT_REPAIR','в ожидании зч':'WAIT_PARTS','в ремонте':'REPAIR'}.get(str(data.get('repair_status','')).strip().casefold(),'')}
    for source,target in [('shift_one','shift_one_employee'),('shift_two','shift_two_employee')]:
        matches=people.get(' '.join(str(data.get(source,'')).split()).casefold(),[])
        result[target]=matches[0] if len(matches)==1 else None
    raw=str(data.get('request_status','')).strip().casefold()
    request=next((label for token,label in [('не оплач','Не оплачена'),('оплачен','Оплачена'),('не соглас','На согласовании'),('на рассмотр','На согласовании'),('согласован','Согласована'),('на исполн','На исполнении')] if token in raw),None)
    raw=str(data.get('parts_status','')).strip().casefold()
    parts=next((label for token,label in [('не поступ','Заказаны'),('поступ','Доставлены'),('достав','Доставлены'),('в пути','В пути'),('перемещ','Ожидание перемещения')] if token in raw),None)
    raw=str(data.get('maintenance','')).strip().casefold()
    ready=bool(re.search(r'^да\b',raw));not_ready=bool(re.search(r'^нет\b',raw))
    maintenance='ТО проведено · готова' if ready and 'не готов' not in raw else 'ТО проведено · не готова' if ready else 'ТО не проведено · не готова' if not_ready else None
    for field,kind,name in [('request_stage','REQUEST',request),('parts_stage','PARTS',parts),('maintenance_stage','MAINTENANCE',maintenance)]:result[field]=options.get((kind,name))
    # A cell containing multiple requests cannot have one inferred request date.
    dates=re.findall(r'\b\d{1,2}\.\d{1,2}\.\d{2,4}\b',str(data.get('request_details','')))
    result['request_date']=parse_date(dates[0]) if len(dates)==1 else None
    return result
