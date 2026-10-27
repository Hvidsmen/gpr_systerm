"""Import the first Summary sheet; collisions stay editable in a dispatcher queue."""
import hashlib,json,re,unicodedata
from datetime import date,datetime
from zipfile import ZipFile
from openpyxl import load_workbook
from django.core.exceptions import ValidationError,PermissionDenied
from django.db import transaction
from django.db.models import F
from django.utils import timezone
from core.fact_history import with_history_actor
from core.fact_edit_window import require_fact_day
from core.permissions import require_roles,role_code
from apps.accounts.models import Company
from apps.personnel.models import Camp
from apps.production import transport
from apps.production.transport_models import EquipmentCampStay,EquipmentTransportEvent,EquipmentDayRecord
from apps.production.transport_details_models import EquipmentDayDetails,DETAIL_FIELDS
from .models import Equipment,EquipmentType
from .transport_import_models import TransportImportBatch,TransportImportRow
from .fleet_import import index,key

HEADERS=['№','Год выпуска','Марка/модель','Инвентарный номер','Гос.рег.знак','VIN№(заводской номер)','Участок','Категория',
    '1 смена','2 смена','В ГПР','Тех. состояние','Состояние','Краткое описание неисправностей, поломок','Дата постановки на ремонт',
    'Номер и дата заявки','Статус заявок (оплачено, не оплачено), дата оплаты','Состояние поставки запчастей','Плановая дата поставки ЗЧ в Дудинку',
    'Проведение ТО весна-лето 2026г. (да,нет), готовность ТС к эксплуатации..','Примечание']
FIELDS=['number','year','model','inventory','plate','vin','site','planning_type','shift_one','shift_two','in_gpr','condition','repair_status',
    'defects','repair_date','request_details','request_status','parts_status','expected_parts_date','maintenance','comment']
PLACEHOLDERS={'','-','—','б/н','нет','н/д','отсутствует'}


def text(value):
    if value is None:return ''
    if isinstance(value,(date,datetime)):return value.isoformat()
    return str(value).strip()


def normalized(value):return re.sub(r'[\s\-]+','',unicodedata.normalize('NFKC',text(value))).upper().translate(str.maketrans('АВЕКМНОРСТУХ','ABEKMHOPCTYX'))
def usable(value):return key(value) not in PLACEHOLDERS

def fingerprint(company):
    # Only the master data used to resolve incoming rows. Existing actual records
    # are never overwritten by this importer.
    models=[Equipment,EquipmentType,Camp]
    payload={m._meta.label:list(m.objects.filter(company=company).order_by('pk').values()) for m in models}
    return hashlib.sha256(json.dumps(payload,default=str,sort_keys=True).encode()).hexdigest()


def read_files(files):
    rows=[]
    for upload in files:
        if not upload.name.lower().endswith(('.xlsx','.xlsm')) or upload.size>5*1024*1024:raise ValidationError('Нужны .xlsx / .xlsm до 5 МБ каждый.')
        try:
            with ZipFile(upload) as archive:
                if sum(i.file_size for i in archive.infolist())>50*1024*1024:raise ValidationError('Слишком большой файл после распаковки.')
            upload.seek(0);book=load_workbook(upload,read_only=True,data_only=False)
        except ValidationError:raise
        except Exception:raise ValidationError(f'Не удалось прочитать {upload.name}.')
        try:
            sheet=book.worksheets[0]
            if sheet.title.strip().casefold()!='сводка':raise ValidationError(f'{upload.name}: первый лист должен называться «Сводка».')
            if sheet.max_row>5002 or sheet.max_column>100:raise ValidationError('Слишком много строк или столбцов.')
            headers=[text(c.value) for c in next(sheet.iter_rows(max_row=1))]
            if len(headers)<21:raise ValidationError(f'{upload.name}: нужны все 21 колонка сводки.')
            if headers[:8]!=HEADERS[:8]:raise ValidationError(f'{upload.name}: не найдены основные 8 колонок сводки.')
            if headers[8:19]!=HEADERS[8:19] or not headers[19].startswith('Проведение ТО') or headers[20]!='Примечание':raise ValidationError(f'{upload.name}: структура дополнительных колонок отличается от сводки. Данные не загружены.')
            for number,cells in enumerate(sheet.iter_rows(min_row=2),2):
                values=[c.value for c in cells]
                if number==2 and values[:21]==list(range(1,22)):continue
                if not any(v is not None for v in values):continue
                if not values[2] and not any(values[3:6]):continue
                original={label or f'Столбец {n+1}':text(values[n]) for n,label in enumerate(headers)}
                data=dict(zip(FIELDS,[text(v) for v in values[:21]]));errors=[]
                if key(data['year']) in PLACEHOLDERS:data['year']=''
                if any(c.data_type=='f' for c in cells):errors.append('Формулы не загружаются как реквизиты. Замените их значениями.')
                rows.append({'source_file':upload.name[:255],'source_sheet':sheet.title,'source_row':number,'original':original,'data':data,'error':' '.join(errors)})
        finally:book.close()
    if not rows:raise ValidationError('На первых листах «Сводка» нет машин.')
    return rows


def validate(data):
    errors=[]
    for field,label,limit in [('model','Марка / модель',150),('planning_type','Техника для планирования',100),('site','ВЖГ',150)]:
        if not data.get(field):errors.append(f'Не заполнено: {label}.')
        elif len(data[field])>limit:errors.append(f'{label}: слишком длинное значение.')
    for field,limit in [('plate',50),('inventory',50),('vin',100)]:
        if len(data.get(field,''))>limit:errors.append(f'{field}: слишком длинное значение.')
    if data.get('year') and (not data['year'].isdigit() or not 1900<=int(data['year'])<=2100):errors.append('Некорректный год выпуска.')
    if not any(usable(data.get(k)) and (k!='inventory' or key(data[k])!='сск') for k in ['vin','inventory','plate']):errors.append('Нет надёжного номера машины.')
    if len(data.get('planning_type','')+' / '+data.get('model',''))>150:errors.append('Название техники и модели длиннее 150 символов.')
    condition=key(data.get('condition',''))
    if condition not in {'испр','не испр','исправна','в ремонте','под списание','на запчасти'}:errors.append('Укажите техническое состояние: Испр., Не испр. или Под списание.')
    status=key(data.get('repair_status',''))
    if status not in {'в работе','в простое','в ремонте','в ожидании р','в ожидании зч','под списание','на запчасти'}:errors.append('Неизвестная отметка работы / ремонта за день.')
    if condition in {'испр','исправна'} and status not in {'в работе','в простое'}:errors.append('Исправная машина имеет отметку ремонта. Уточните состояние.')
    if condition not in {'испр','исправна'} and status=='в работе':errors.append('Неисправная машина отмечена в работе. Уточните состояние.')
    if key(data.get('in_gpr','')) not in {'','да','нет'}:errors.append('В ГПР: укажите Да, Нет или оставьте пустым.')
    for name,label in DETAIL_FIELDS:
        limit=400 if name in {'shift_one','shift_two'} else 200 if name in {'repair_date','expected_parts_date','repair_status'} else 10000
        if name!='in_gpr' and len(data.get(name,''))>limit:errors.append(f'{label}: слишком длинное значение.')
    return errors


def collisions(company,rows):
    seen={name:{} for name in ['vin','inventory','plate']};result=[]
    for unit in Equipment.objects.filter(company=company).order_by('pk'):
        for field,attr in [('vin','vin'),('plate','plate_number'),('inventory','inventory_number')]:
            value=getattr(unit,attr)
            if usable(value) and not (field=='inventory' and key(value)=='сск'):seen[field].setdefault(normalized(value),(unit.pk,f'машина №{unit.pk}'))
    for row in rows:
        data=row['data'];errors=validate(data)
        if row.get('error'):errors.insert(0,row['error'])
        duplicate=None;candidate_ids=set()
        for field in ['vin','inventory','plate']:
            value=data.get(field,'')
            if not usable(value) or field=='inventory' and key(value)=='сск':continue
            found=seen[field].get(normalized(value))
            if found:
                errors.append(f'{dict(vin="VIN / заводской номер",inventory="Инвентарный номер",plate="Госномер")[field]} {value}: уже есть {found[1]}.')
                if found[0]:candidate_ids.add(found[0])
        duplicate=next(iter(candidate_ids)) if len(candidate_ids)==1 else None
        result.append({**row,'error':' '.join(errors),'duplicate_of_id':duplicate})
        if not errors:
            for field in seen:
                value=data.get(field,'')
                if usable(value) and not (field=='inventory' and key(value)=='сск'):
                    seen[field][normalized(value)]=(None,f'{row["source_file"]}, строка {row["source_row"]}')
    return result


@with_history_actor
@transaction.atomic
def preview(user,files,day):
    require_roles(user,transport.OPERATORS);require_fact_day(user,day)
    transport._company_mutex(user)
    rows=collisions(user.company,read_files(files))
    batch=TransportImportBatch.objects.create(company=user.company,date=day,files=[f.name for f in files],fingerprint=fingerprint(user.company),created_by=user)
    for row in rows:
        TransportImportRow.objects.create(company=user.company,batch=batch,date=day,status='PENDING' if row['error'] else 'READY',**row)
    return batch


def _camp(user,site):
    candidates=[c for c in Camp.objects.filter(company=user.company) if key(c.name)==key(site)]
    if len(candidates)>1:raise ValidationError('Неоднозначное название ВЖГ. Уточните справочник ВЖГ.')
    camp=candidates[0] if candidates else None
    if camp is None:
        if role_code(user)!='ADMIN':raise ValidationError('ВЖГ не найден. Администратор должен создать городок и связать его с объектами.')
        camp=Camp.objects.create(company=user.company,name=site)
    transport.authorize(user,camp)
    return camp


def _apply_row(user,row):
    data=row.data;errors=validate(data)
    if errors:raise ValidationError(errors)
    camp=_camp(user,data['site'])
    roots=index(EquipmentType.objects.filter(company=user.company,merge_source__isnull=True,parent=None),True)
    root=roots.get(key(data['planning_type']))
    if root is None:root=EquipmentType.objects.create(company=user.company,name=data['planning_type'])
    if not root.is_active:raise ValidationError('Техника для планирования неактивна.')
    name=data['planning_type']+' / '+data['model']
    variants=index(EquipmentType.objects.filter(company=user.company,parent=root,merge_source__isnull=True),True)
    model=variants.get(key(name))
    if model is None:model=EquipmentType.objects.create(company=user.company,parent=root,name=name,brand_model=data['model'],category=root.category)
    if not model.is_active:raise ValidationError('Модель неактивна.')
    machine=Equipment(company=user.company,type=model,name=data['model'],brand_model=data['model'],manufacture_year=int(data['year']) if data['year'] else None,
        inventory_number=data['inventory'],plate_number=data['plate'] if usable(data['plate']) else '',vin=data['vin'] if usable(data['vin']) else '',site_name=camp.name)
    machine.full_clean();machine.save()
    # New machines only: importing a snapshot never overwrites an existing actual.
    EquipmentCampStay.objects.create(company=user.company,equipment=machine,camp=camp,arrived_on=row.date)
    condition='READY' if key(data['condition']) in {'испр','исправна'} else 'SCRAP' if key(data['condition']) in {'под списание','на запчасти'} else 'REPAIR'
    EquipmentTransportEvent.objects.create(company=user.company,equipment=machine,date=row.date,kind='ARRIVAL',to_camp=camp,recorded_by=user,comment=f'Импорт: {row.source_file}, строка {row.source_row}')
    EquipmentTransportEvent.objects.create(company=user.company,equipment=machine,date=row.date,kind='CONDITION',to_camp=camp,condition=condition,recorded_by=user)
    if condition=='READY':EquipmentDayRecord.objects.create(company=user.company,equipment=machine,camp=camp,date=row.date,status='WORKED' if key(data['repair_status'])=='в работе' else 'IDLE',recorded_by=user)
    values={name:data.get(name,'') for name,_ in DETAIL_FIELDS};values['in_gpr']={'да':True,'нет':False}.get(key(data.get('in_gpr','')))
    details=EquipmentDayDetails(company=user.company,equipment=machine,camp=camp,date=row.date,recorded_by=user,**values);details.full_clean();details.save()
    row.status='APPLIED';row.error='';row.equipment=machine;row.camp=camp;row.resolved_by=user;row.resolved_at=timezone.now();row.save()
    return machine


@with_history_actor
@transaction.atomic
def apply(user,batch):
    require_roles(user,transport.OPERATORS);require_fact_day(user,batch.date)
    transport._company_mutex(user);Company.objects.select_for_update().get(pk=user.company_id)
    batch=TransportImportBatch.objects.select_for_update().get(pk=batch.pk,company=user.company)
    if batch.applied_at:raise ValidationError('Этот импорт уже применён.')
    if fingerprint(user.company)!=batch.fingerprint:raise ValidationError('Справочники изменились. Создайте новый предпросмотр.')
    for row in batch.rows.select_for_update().order_by('pk'):
        issues=collisions(user.company,[{'data':row.data,'source_file':row.source_file,'source_row':row.source_row,'error':''}])[0]
        if row.status!='READY' or issues['error']:
            row.status='PENDING';row.error=row.error or issues['error'];row.duplicate_of_id=issues['duplicate_of_id'];row.save();continue
        try:
            with transaction.atomic():_apply_row(user,row)
        except (ValidationError,PermissionDenied) as exc:
            row.refresh_from_db();row.status='PENDING';row.error='; '.join(exc.messages) if isinstance(exc,ValidationError) else str(exc);row.save()
    batch.applied_at=timezone.now();batch.save();require_fact_day(user,batch.date)
    return batch


@with_history_actor
@transaction.atomic
def resolve(user,row,data,version,*,close_duplicate=False):
    require_roles(user,transport.OPERATORS)
    transport._company_mutex(user);Company.objects.select_for_update().get(pk=user.company_id)
    row=TransportImportRow.objects.select_for_update().select_related('batch').get(pk=row.pk,company=user.company)
    if row.updated_at.isoformat()!=version or row.status!='PENDING' or not row.batch.applied_at:raise ValidationError('Запись уже изменена или импорт ещё не применён. Обновите страницу.')
    if close_duplicate:
        if not row.duplicate_of_id:raise ValidationError('Сначала укажите существующую машину, совпадающую с этой записью.')
        row.status='DUPLICATE';row.resolved_by=user;row.resolved_at=timezone.now();row.save();return row
    require_fact_day(user,row.date)
    checked=collisions(user.company,[{'data':data,'source_file':row.source_file,'source_row':row.source_row,'error':''}])[0]
    if checked['error']:raise ValidationError(checked['error'])
    row.data=data;_apply_row(user,row);require_fact_day(user,row.date)
    return row
