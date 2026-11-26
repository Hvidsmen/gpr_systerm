"""Import the recruiting workbook into unified candidate cards."""
from collections import defaultdict
from datetime import date,datetime,time
import re
from django.core.exceptions import ValidationError
from django.db import transaction
from apps.projects.models import ConstructionObject
from apps.resources.models import Position
from .models import BaseCity,RecruitmentSpecialist,TalentPoolCandidate
from .recruitment_models import (POOL_UNAVAILABLE_STAGES,RECRUITMENT_STAGES,RECRUITMENT_SYSTEM_STAGES,RECRUITMENT_TERMINAL_STAGES,
    normalize_recruitment_specialist_name)
from .recruitment_stage_flow import stage_from_confirmation_values

STORE_SHEET='Магазин 2026'
PLAN_SHEET='План найма'
TRANSFERRED_SHEET='Поставлены на заезд 2026'
APPROVED_SHEET='Не заехалиНе потребовались'
REJECTED_SHEET='Отказники'
SOURCE_SHEETS=(STORE_SHEET,PLAN_SHEET,TRANSFERRED_SHEET,APPROVED_SHEET,REJECTED_SHEET)

HEADERS=['№ п.п.','ФИО','Должность','Строительный участок','Дата рождения','Серия номер паспорта','Номер телефона',
    'Согласован с непосредственным руководителем','Согласование СБ','СКЗИ (действует до)','Медицинский осмотр',
    'Документы направлены на пропуск','Базовый город','Готовность к заезду','Дата прибытия в Дудинку',
    'Ответственный HR','Название агентства','Примечания','ФИО (дублирование без пробелов)']
KEYS=['number','full_name','position','construction_site','birth_date','passport','phone','manager_approval','security_approval',
    'skzi_until','medical_exam','pass_documents','base_city','readiness','dudinka_arrival','responsible_hr','agency','notes','duplicate_name']
REJECTED_KEYS=['number','full_name','position','phone','manager_approval','security_approval','skzi_until','medical_exam',
    'pass_documents','pass_until','base_city','readiness','dudinka_arrival','responsible_hr','agency','notes']
PLAN_KEYS=['number','project','construction_site','op','block','department_1','department_2','position','vacancy_count',
    'for_summary','required','employed','remaining','full_name','phone','planned_mobilization','need_month','work_kind',
    'schedule','location','position_category','employee_category','priority','request_owner','responsible_hr','answer_sheet',
    'request_date','sent_to_customer','customer_approval','security_sent','security_result','medical_sent','medical_result',
    'pass_submitted','pass_received','notice_period','tickets_requested','rejection','planned_arrival','actual_arrival',
    'actual_arrival_flag','employment_date','vacancy_days','source','notes']
PROFILE_KEYS={'position','construction_site','birth_date','passport','phone','manager_approval','security_approval','skzi_until',
    'medical_exam','pass_documents','base_city','readiness','dudinka_arrival','responsible_hr','agency','notes'}
STAGE_ORDER={'NEW':0,'SCREENING':1,'HR_INTERVIEW':2,'MANAGER_APPROVAL':3,'SECURITY_CHECK':4,
    'MEDICAL_DOCUMENTS':5,'PASS_PROCESSING':6,'SELECTED':7,'READY':8,'TRANSFERRED':9,
    'ARRIVAL_APPROVED':10,'ARRIVED':11,'EMPLOYED':12,'DISMISSED':13,'RESERVE':7,'REJECTED':14,'WITHDRAWN':14,
    'NOT_REQUIRED':14,'NO_SHOW':14,'BLACKLIST':14}
STAGE_LABELS=dict(RECRUITMENT_STAGES)


def _text(value):
    if value is None:return ''
    if isinstance(value,datetime):return value.date().isoformat()
    if isinstance(value,date):return value.isoformat()
    if isinstance(value,time):return value.strftime('%H:%M')
    if isinstance(value,float) and value.is_integer():return str(int(value))
    return str(value).strip()


def _name(value):return ' '.join(_text(value).split())
def _phone(value):return ''.join(character for character in _text(value) if character.isdigit())
def _valid_name(value):return bool(value and value.casefold() not in {'вакансия','фио кандидата','#ref!','#n/a','нет'})


def _specialist(company,name):
    name=normalize_recruitment_specialist_name(name)
    if not name:return None
    existing=RecruitmentSpecialist.objects.filter(company=company,name__iexact=name).first()
    return existing or RecruitmentSpecialist.objects.create(company=company,name=name)


def _catalogue_match(model,company,name):
    name=_name(name)
    if not name:return None
    if model is BaseCity:
        from . import base_cities
        city=base_cities.find(company,name,active=True)
        if city:return city
        # Companies created after the catalogue migration may not contain all
        # Russian cities. Keep a valid Excel value linked to the card instead
        # of leaving it only in source_data.
        if base_cities.find(company,name,active=False):return None
        from .russian_cities import RUSSIAN_CITIES,normalize_city_name
        normalized=normalize_city_name(name)
        canonical=next((city for city in RUSSIAN_CITIES if city.casefold()==normalized.casefold()),None)
        return base_cities.create(company=company,name=canonical) if canonical else None
    queryset=model.objects.filter(company=company)
    matches=list(queryset.filter(name__iexact=name)[:2])
    if len(matches)==1:return matches[0]
    # Excel often appends an object or work area in parentheses to a valid
    # catalogue position.  Use the base only when it resolves unambiguously.
    base=re.sub(r'(?:\s*\([^()]*(?:\)|$))+\s*$','',name).strip(' ,;-/')
    if not base or base.casefold()==name.casefold():return None
    matches=list(queryset.filter(name__iexact=base)[:2])
    return matches[0] if len(matches)==1 else None


def _date(value):
    text=_text(value)[:10]
    for pattern in ('%Y-%m-%d','%d.%m.%Y'):
        try:return datetime.strptime(text,pattern).date()
        except ValueError:pass
    return None


def _profile_date(source,*keys):
    values=[_date(source.get(key)) for key in keys]
    for event in source.get('funnel_sources',[]):
        values.extend(_date((event.get('data') or {}).get(key)) for key in keys)
    values=[value for value in values if value]
    return max(values) if values else None


def _latest_date(data,keys):
    values=[data.get(key,'')[:10] for key in keys if len(data.get(key,''))>=10 and data.get(key,'')[4:5]=='-']
    return max(values,default='')


def _plan_stage(data):
    rejection=data.get('rejection','').casefold()
    details=' '.join((rejection,data.get('notes','').casefold(),data.get('customer_approval','').casefold(),data.get('security_result','').casefold()))
    if rejection:
        if any(word in details for word in ('отказ от заезда','отказался','передумал','не выходит','не хочет','другая работ')):return 'WITHDRAWN'
        return 'REJECTED'
    if data.get('actual_arrival'):return 'ARRIVED'
    if data.get('planned_arrival') or data.get('tickets_requested'):return 'TRANSFERRED'
    if data.get('pass_received') or data.get('pass_submitted'):return 'PASS_PROCESSING'
    if any(data.get(key) for key in ('medical_result','medical_sent')):return 'MEDICAL_DOCUMENTS'
    if any(data.get(key) for key in ('security_result','security_sent')):return 'SECURITY_CHECK'
    if any(data.get(key) for key in ('customer_approval','sent_to_customer')):return 'MANAGER_APPROVAL'
    return 'NEW'


def _rejected_stage(data):
    details=' '.join((data.get('readiness',''),data.get('notes',''))).casefold()
    return 'WITHDRAWN' if any(word in details for word in ('отказался','передумал','не хочет','другая организац','другую работ','жена не отпустила')) else 'REJECTED'


def _record(sheet,row_number,data,stage,date_keys):
    data={**data,'responsible_hr':normalize_recruitment_specialist_name(data.get('responsible_hr'))}
    details=next((data.get(key,'') for key in ('rejection','notes','readiness','customer_approval','security_result') if data.get(key)), '')
    return {'sheet':sheet,'row':row_number,'stage':stage,'stage_label':STAGE_LABELS[stage],'event_date':_latest_date(data,date_keys),
        'position':data.get('position',''),'construction_site':data.get('construction_site',''),
        'phone':data.get('phone',''),'details':details,'data':data}


def _common_records(sheet,stage):
    iterator=sheet.iter_rows(values_only=True)
    try:headers=[_text(value) for value in next(iterator)[:len(HEADERS)]]
    except StopIteration:raise ValidationError(f'Лист «{sheet.title}» пуст.')
    if headers!=HEADERS:raise ValidationError(f'Структура листа «{sheet.title}» не совпадает с ожидаемой. Проверьте заголовки.')
    for row_number,values in enumerate(iterator,2):
        data={key:_text(value) for key,value in zip(KEYS,values[:len(KEYS)])};data['full_name']=_name(data['full_name'])
        if not _valid_name(data['full_name']):continue
        yield _record(sheet.title,row_number,data,stage,('security_approval','medical_exam','pass_documents','dudinka_arrival'))


def _plan_records(sheet):
    iterator=sheet.iter_rows(min_row=5,values_only=True)
    try:headers=[_text(value).replace('\n',' / ') for value in next(iterator)]
    except StopIteration:raise ValidationError(f'Лист «{PLAN_SHEET}» пуст.')
    if len(headers)<40 or headers[7]!='Должность' or headers[13]!='ФИО кандидата':
        raise ValidationError(f'Структура листа «{PLAN_SHEET}» не совпадает с ожидаемой. Проверьте заголовки.')
    for row_number,values in enumerate(iterator,6):
        data={key:_text(value) for key,value in zip(PLAN_KEYS,values[:len(PLAN_KEYS)])};data['full_name']=_name(data['full_name'])
        if not _valid_name(data['full_name']):continue
        yield _record(PLAN_SHEET,row_number,data,_plan_stage(data),
            ('request_date','sent_to_customer','security_sent','medical_sent','pass_submitted','pass_received',
             'tickets_requested','planned_arrival','actual_arrival','employment_date'))


def _rejected_records(sheet):
    iterator=sheet.iter_rows(values_only=True)
    try:next(iterator)
    except StopIteration:raise ValidationError(f'Лист «{REJECTED_SHEET}» пуст.')
    for row_number,values in enumerate(iterator,2):
        data={key:_text(value) for key,value in zip(REJECTED_KEYS,values[:len(REJECTED_KEYS)])};data['full_name']=_name(data['full_name'])
        if not _valid_name(data['full_name']):continue
        yield _record(REJECTED_SHEET,row_number,data,_rejected_stage(data),
            ('security_approval','medical_exam','pass_documents','pass_until','dudinka_arrival'))


def _records(workbook):
    readers={
        STORE_SHEET:lambda sheet:_common_records(sheet,'SELECTED'),
        PLAN_SHEET:_plan_records,
        TRANSFERRED_SHEET:lambda sheet:_common_records(sheet,'TRANSFERRED'),
        APPROVED_SHEET:lambda sheet:_common_records(sheet,'ARRIVAL_APPROVED'),
        REJECTED_SHEET:_rejected_records,
    }
    for sheet_name,reader in readers.items():
        if sheet_name in workbook.sheetnames:yield from reader(workbook[sheet_name])


def _current_record(records):
    return max(records,key=lambda item:(item['event_date'],STAGE_ORDER.get(item['stage'],0),SOURCE_SHEETS.index(item['sheet']),item['row']))


@transaction.atomic
def import_sheet(user,file):
    try:
        from openpyxl import load_workbook
        workbook=load_workbook(file,read_only=True,data_only=True)
    except Exception as exc:raise ValidationError('Не удалось прочитать XLSX-файл.') from exc
    if STORE_SHEET not in workbook.sheetnames:raise ValidationError(f'В файле нет листа «{STORE_SHEET}».')
    grouped=defaultdict(list)
    for record in _records(workbook):grouped[record['data']['full_name'].casefold()].append(record)
    existing=defaultdict(list)
    rows=TalentPoolCandidate.objects.select_for_update().filter(company=user.company).select_related(
        'responsible_hr','position','construction_object','base_city').prefetch_related('vacancy_candidates')
    for row in rows:existing[_name(row.full_name).casefold()].append(row)
    created=updated=skipped=0
    for key,records in grouped.items():
        name=records[-1]['data']['full_name'];phones={_phone(record['phone']) for record in records if _phone(record['phone'])}
        matches=existing.get(key,[])
        if len(matches)>1:
            exact=[row for row in matches if _phone(row.phone) in phones]
            if len(exact)!=1:raise ValidationError(f'В портале найдено несколько карточек с ФИО «{name}». Объедините их перед импортом.')
            row=exact[0]
        else:row=matches[0] if matches else None
        current=_current_record(records);profile={}
        for record in sorted(records,key=lambda item:(item['event_date'],SOURCE_SHEETS.index(item['sheet']),item['row'])):
            profile.update({field:value for field,value in record['data'].items() if field in PROFILE_KEYS and value})
        source=dict(row.source_data or {}) if row else {}
        source.update(profile);source['responsible_hr']=normalize_recruitment_specialist_name(source.get('responsible_hr'))
        source.update({'full_name':name,'source_sheet':current['sheet'],'funnel_stage':current['stage'],
            'funnel_sources':sorted(records,key=lambda item:(item['event_date'],item['sheet'],item['row']),reverse=True)})
        operational=bool(row and list(row.vacancy_candidates.all()))
        stage=row.stage if operational else current['stage']
        phone_value=profile.get('phone') or (row.phone if row else '')
        notes=profile.get('notes') or current['details'] or (row.notes if row else '')
        active=row.is_active if operational else stage not in POOL_UNAVAILABLE_STAGES
        responsible_hr=_specialist(user.company,source.get('responsible_hr')) if source.get('responsible_hr') else (row.responsible_hr if row else None)
        position=(row.position if row and row.position_id else _catalogue_match(Position,user.company,source.get('position')))
        construction_object=(row.construction_object if row and row.construction_object_id else
            _catalogue_match(ConstructionObject,user.company,source.get('construction_site')))
        base_city=(row.base_city if row and row.base_city_id else _catalogue_match(BaseCity,user.company,source.get('base_city')))
        if base_city:source['base_city']=base_city.name
        ready_on=(row.ready_for_arrival_on if row and row.ready_for_arrival_on else _profile_date(source,'ready_for_arrival_on','planned_mobilization','readiness'))
        manager_on=(row.manager_approved_on if row and row.manager_approved_on else _profile_date(source,'manager_approved_on','manager_approval','customer_approval'))
        security_on=(row.security_approved_on if row and row.security_approved_on else _profile_date(source,'security_approved_on','security_approval','security_result'))
        medical_on=(row.medical_exam_on if row and row.medical_exam_on else _profile_date(source,'medical_exam_on','medical_exam','medical_result'))
        pass_documents_on=(row.pass_documents_sent_on if row and row.pass_documents_sent_on else
            _profile_date(source,'pass_documents_sent_on','pass_submitted','pass_documents'))
        pass_ready_on=(row.pass_ready_on if row and row.pass_ready_on else
            _profile_date(source,'pass_ready_on','pass_received','pass_approved_on'))
        if stage in {'MANAGER_APPROVAL','SECURITY_CHECK','MEDICAL_DOCUMENTS','PASS_PROCESSING','READY'}:
            stage=stage_from_confirmation_values(manager_on,security_on,medical_on,pass_documents_on,pass_ready_on)
        if stage=='READY':ready_on=ready_on or pass_ready_on
        defaults={'phone':phone_value,'notes':notes,'source_data':source,'stage':stage,'is_active':active,'responsible_hr':responsible_hr,
            'position':position,'construction_object':construction_object,'base_city':base_city,
            'ready_for_arrival':bool(ready_on),'ready_for_arrival_on':ready_on,
            'manager_approved':bool(manager_on),'manager_approved_on':manager_on,
            'security_approved':bool(security_on),'security_approved_on':security_on,
            'medical_exam_passed':bool(medical_on),'medical_exam_on':medical_on,
            'pass_documents_sent':bool(pass_documents_on),'pass_documents_sent_on':pass_documents_on,
            'pass_ready':bool(pass_ready_on),'pass_ready_on':pass_ready_on}
        if row:
            changed=[]
            for field,value in defaults.items():
                if getattr(row,field)!=value:setattr(row,field,value);changed.append(field)
            if changed:row.save(update_fields=[*changed,'updated_at']);updated+=1
            else:skipped+=1
        else:
            row=TalentPoolCandidate.objects.create(company=user.company,full_name=name,created_by=user,**defaults)
            existing[key].append(row);created+=1
    return {'created':created,'updated':updated,'skipped':skipped}
