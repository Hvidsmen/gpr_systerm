"""Import the maintained arrival/departure workbook into the review inbox."""
import re
from datetime import date,datetime,time
from django.core.exceptions import ValidationError
from django.db import transaction
from apps.projects.models import ConstructionObject
from apps.resources.models import Employee
from .models import MovementReviewRequest,Person
from . import housing


def normalized(value):
    return ' '.join(str(value or '').replace('ё','е').replace('Ё','Е').split()).casefold()


def header(value):
    return re.sub(r'[^а-яa-z0-9]+',' ',normalized(value)).strip()


def column(headers,*needles):
    for index,value in enumerate(headers):
        if any(needle in value for needle in needles):return index
    return None


def value(row,index):
    return row[index] if index is not None and index<len(row) else None


def as_date(raw):
    if isinstance(raw,datetime):return raw.date()
    if isinstance(raw,date):return raw
    text=str(raw or '').strip()
    for pattern in ('%d.%m.%Y','%Y-%m-%d'):
        try:return datetime.strptime(text,pattern).date()
        except ValueError:pass
    return None


def as_time(raw):
    if isinstance(raw,datetime):return raw.time().replace(second=0,microsecond=0)
    if isinstance(raw,time):return raw.replace(second=0,microsecond=0)
    text=str(raw or '').strip()
    for pattern in ('%H:%M','%H.%M'):
        try:return datetime.strptime(text,pattern).time()
        except ValueError:pass
    return None


def employment(raw):
    text=normalized(raw)
    if 'нов' in text:return 'NEW'
    if 'перев' in text:return 'TRANSFER'
    if 'команд' in text:return 'BUSINESS_TRIP'
    return 'STAFF' if text else ''


def parse(raw):
    if len(raw)>12*1024*1024:raise ValidationError('Файл больше 12 МБ.')
    try:
        from openpyxl import load_workbook
        from io import BytesIO
        book=load_workbook(BytesIO(raw),read_only=True,data_only=True,keep_vba=True)
    except Exception as exc:raise ValidationError('Не удалось прочитать Excel. Загрузите исходный файл .xlsx или .xlsm.') from exc
    rows=[];ignored=[]
    for sheet in book.worksheets:
        if sheet.sheet_state!='visible':continue
        title=normalized(sheet.title)
        kind='ARRIVAL' if title.startswith('заезд') else 'DEPARTURE' if title.startswith('выезд') else None
        if not kind:ignored.append(sheet.title);continue
        iterator=sheet.iter_rows(values_only=True)
        headings=[header(cell) for cell in next(iterator,())]
        columns={
            'name':column(headings,'фамилия имя отчество','фио'),
            'position':column(headings,'должность'),
            'site':column(headings,'участок'),
            'city':column(headings,'базовый город'),
            'phone':column(headings,'номер телефона','телефон'),
            'number':column(headings,'заявк'),
            'date':column(headings,'дата заезда' if kind=='ARRIVAL' else 'дата выезда'),
            'time':column(headings,'время прилета','время прилёта','время вылета'),
            'status':column(headings,'статус'),
            'note':column(headings,'комментар','примечан'),
            'employment':column(headings,'штат новый перевод'),
        }
        if columns['name'] is None or columns['date'] is None:
            ignored.append(sheet.title);continue
        for number,row in enumerate(iterator,2):
            name=' '.join(str(value(row,columns['name']) or '').split());movement_date=as_date(value(row,columns['date']))
            if not name and not movement_date:continue
            if not name or not movement_date:
                rows.append({'sheet':sheet.title,'row':number,'error':'Не указаны ФИО или дата.'});continue
            rows.append({'sheet':sheet.title,'row':number,'kind':kind,'name':name,'movement_date':movement_date,
                'employment_kind':employment(value(row,columns['employment'])) if kind=='ARRIVAL' else '',
                'planning_position_name':' '.join(str(value(row,columns['position']) or '').split()),
                'site':' '.join(str(value(row,columns['site']) or '').split()),
                'base_city':' '.join(str(value(row,columns['city']) or '').split()),
                'phone':' '.join(str(value(row,columns['phone']) or '').split())[:80],
                'request_number':' '.join(str(value(row,columns['number']) or '').split())[:50],
                'travel_time':as_time(value(row,columns['time'])),
                'travel_status':' '.join(str(value(row,columns['status']) or '').split())[:80],
                'note':' '.join(str(value(row,columns['note']) or '').split()),
            })
    if not rows:raise ValidationError('На видимых листах не найдены строки заезда или выезда.')
    return rows,ignored


@transaction.atomic
def apply(user,raw):
    parsed,ignored=parse(raw);company=user.company
    employee_map={}
    for employee in Employee.objects.filter(company=company).select_related('position'):
        employee_map.setdefault(normalized(employee.display_name),[]).append(employee)
    person_map={}
    for person in Person.objects.filter(company=company).select_related('employee__position'):
        person_map.setdefault(normalized(person.display_name),[]).append(person)
    objects={}
    for obj in ConstructionObject.objects.filter(company=company).select_related('parent'):
        objects.setdefault(normalized(obj.name),[]).append(obj)
    result={'created':0,'duplicates':0,'unmatched':[],'invalid':[],'ignored_sheets':ignored,'total':len(parsed)}
    for data in parsed:
        if data.get('error'):
            result['invalid'].append(f"{data['sheet']}, строка {data['row']}: {data['error']}");continue
        matches=person_map.get(normalized(data['name']),[])
        if len(matches)==1:person=matches[0]
        elif not matches and len(employee_map.get(normalized(data['name']),[]))==1:
            employee=employee_map[normalized(data['name'])][0]
            try:person=housing.person_for(employee)
            except ValidationError as exc:
                result['unmatched'].append(f"{data['sheet']}, строка {data['row']}: {data['name']} — {exc}");continue
            person_map.setdefault(normalized(data['name']),[]).append(person)
        else:
            cause='найдено несколько сотрудников' if len(matches)>1 or len(employee_map.get(normalized(data['name']),[]))>1 else 'нет в справочнике сотрудников'
            result['unmatched'].append(f"{data['sheet']}, строка {data['row']}: {data['name']} — {cause}");continue
        lookup={'company':company,'person':person,'kind':data['kind'],'movement_date':data['movement_date'],'request_number':data['request_number']}
        if MovementReviewRequest.objects.filter(**lookup).exists():result['duplicates']+=1;continue
        object_matches=objects.get(normalized(data['site']),[]);site_object=object_matches[0] if len(object_matches)==1 else None
        employee=person.employee
        from .models import CampObjectLink
        camp_ids=list(CampObjectLink.objects.filter(company=company,construction_object=site_object,camp__is_active=True).values_list('camp_id',flat=True).distinct()) if site_object else []
        camp_id=camp_ids[0] if len(camp_ids)==1 else None
        row=MovementReviewRequest(**lookup,requested_by=user,source='EXCEL',status='OPEN',employment_kind=data['employment_kind'],
            planning_position_name=data['planning_position_name'],position_name=employee.display_position if employee else '',
            construction_object=site_object.parent if site_object and site_object.parent_id else site_object,site_object=site_object,
            site=data['site'],base_city=data['base_city'],phone=data['phone'] or ((employee.phone if employee else '') or person.phone),
            to_camp_id=camp_id if data['kind']=='ARRIVAL' else None,from_camp_id=camp_id if data['kind']!='ARRIVAL' else None,
            travel_time=data['travel_time'],travel_status=data['travel_status'] or ('Ожидается'),note=data['note'])
        row.full_clean();row.save();result['created']+=1
    from apps.audit.models import AuditLog
    AuditLog.objects.create(user=user,action='CREATE',entity_type='personnel.MovementReviewRequest',entity_id='',
        new_value={key:(len(value) if isinstance(value,list) else value) for key,value in result.items()},
        comment='Импорт видимых листов Excel перевахты')
    return result
