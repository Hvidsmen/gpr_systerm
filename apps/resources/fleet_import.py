"""Reviewed import of the fleet and its operators from the agreed Summary sheet."""
import hashlib,json,re
from collections import Counter,defaultdict
from zipfile import ZipFile
from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core import signing
from django.core.exceptions import ValidationError
from django.db import transaction,IntegrityError
from django.shortcuts import render,redirect
from django.views.decorators.http import require_http_methods
from openpyxl import load_workbook
from core.permissions import require_roles,PLAN_ROLES
from apps.accounts.models import Company
from apps.audit.models import AuditLog
from .models import EquipmentCategory,EquipmentType,Equipment,Brigade,EquipmentCrewPosition,Position,PositionPlanningBrigade
from .position_catalog import position_for

HEADERS=['№','Год выпуска','Марка/модель','Инвентарный номер','Гос.рег.знак','VIN№(заводской номер)','Участок','Категория','Должность']
SALT='resources.summary-fleet.v1'
MODELS=[EquipmentCategory,EquipmentType,Equipment,Brigade,Position,PositionPlanningBrigade,EquipmentCrewPosition]

def text(value):return re.sub(r'\s+',' ',str(value or '')).strip()
def key(value):return text(value).rstrip('.').casefold()
def digest(value):return hashlib.sha256(json.dumps(value,sort_keys=True,default=str,ensure_ascii=False).encode()).hexdigest()

class UploadForm(forms.Form):
    file=forms.FileField(label='Файл «Техника с должностями» (.xlsx или .xlsm)')
    def clean_file(self):
        upload=self.cleaned_data['file']
        if not upload.name.lower().endswith(('.xlsx','.xlsm')) or upload.size>5*1024*1024:raise ValidationError('Выберите .xlsx или .xlsm до 5 МБ.')
        return upload


def read_rows(upload):
    try:
        with ZipFile(upload) as archive:
            if sum(info.file_size for info in archive.infolist())>50*1024*1024:raise ValidationError('Файл слишком большой после распаковки.')
        upload.seek(0)
        workbook=load_workbook(upload,read_only=True,data_only=False)
    except ValidationError:raise
    except Exception:raise ValidationError('Не удалось прочитать Excel.')
    try:
        sheet=next((s for s in workbook if s.title.strip().casefold()=='сводка'),None)
        if sheet is None:raise ValidationError('Нет листа «Сводка».')
        iterator=sheet.iter_rows(max_col=9,values_only=True)
        if [text(v) for v in next(iterator)]!=HEADERS:raise ValidationError('Столбцы листа «Сводка» не соответствуют согласованному файлу.')
        rows=[];seen={};plates={}
        for number,values in enumerate(iterator,2):
            if number>5002:raise ValidationError('Допускается не более 5000 строк.')
            if number==2 and list(values)==list(range(1,10)):continue
            if not any(value is not None for value in values):continue
            if any(isinstance(value,str) and value.startswith('=') for value in values[1:]):raise ValidationError(f'Строка {number}: формулы в реквизитах не поддерживаются. Вставьте значения.')
            _,year,model,inventory,plate,vin,site,category,position=map(text,values)
            if not model or not category or not position:raise ValidationError(f'Строка {number}: нужны модель, категория и должность.')
            if year in {'','-'}:year=None
            elif year.isdigit() and 1900<=int(year)<=2100:year=int(year)
            else:raise ValidationError(f'Строка {number}: некорректный год выпуска.')
            row={'row':number,'year':year,'model':model,'inventory':inventory,'plate':plate,'vin':vin,'site':site,'category':category,'position':position.rstrip('.')}
            for field,limit in [('model',150),('inventory',50),('plate',50),('vin',100),('site',150),('category',100),('position',150)]:
                if len(row[field])>limit:raise ValidationError(f'Строка {number}: слишком длинное значение «{field}».')
            if len(category+' / '+model)>150:raise ValidationError(f'Строка {number}: название категории и модели длиннее 150 символов.')
            usable_inventory=key(inventory) not in {'','-','сск'}
            identity=('inventory',key(inventory)) if usable_inventory else ('plate',key(plate)) if plate else None
            if identity is None:raise ValidationError(f'Строка {number}: нет надёжного инвентарного или государственного номера.')
            row['identity']=digest(identity)
            if row['identity'] in seen:raise ValidationError(f'Строки {seen[row["identity"]]} и {number}: совпадает идентификатор машины.')
            if plate and key(plate) in plates:raise ValidationError(f'Строки {plates[key(plate)]} и {number}: совпадает государственный номер.')
            if plate:plates[key(plate)]=number
            seen[row['identity']]=number;rows.append(row)
        if not rows:raise ValidationError('Лист «Сводка» пуст.')
        return rows
    finally:workbook.close()


def fingerprint(company):
    return digest({m._meta.label:list(m.objects.filter(company=company).order_by('pk').values()) for m in MODELS})


def index(records,aliases=False):
    result={}
    for record in records:
        for name in [record.name,*(getattr(record,'excel_aliases',[]) if aliases else [])]:
            normalized=key(name)
            if normalized in result and result[normalized].pk!=record.pk:raise ValidationError(f'Неоднозначное название в справочнике: {name}')
            result[normalized]=record
    return result


def inspect(company,rows):
    roles=index(Brigade.objects.filter(company=company,merge_source__isnull=True),True)
    categories=index(EquipmentCategory.objects.filter(company=company))
    types=index(EquipmentType.objects.filter(company=company,merge_source__isnull=True),True)
    units=list(Equipment.objects.filter(company=company).select_related('type','operator_position'))
    by_import={u.import_key:u for u in units if u.import_key}
    by_plate=defaultdict(list)
    for unit in units:
        if unit.plate_number:by_plate[key(unit.plate_number)].append(unit)
    by_inventory=defaultdict(list)
    for unit in units:
        if key(unit.inventory_number) not in {'','-','сск'}:by_inventory[key(unit.inventory_number)].append(unit)
    warnings=[];new_units=0;new_roles=set();new_types=set();new_categories=set();result=[];positions=defaultdict(set)
    vins=Counter(key(row['vin']) for row in rows if row['vin'])
    for vin,count in vins.items():
        if count>1:warnings.append(f'Повторяющийся VIN/заводской номер ({count} строк). Машины сохранятся отдельно по инвентарному/государственному номеру: {vin}')
    for row in rows:
        role=roles.get(key(row['position']))
        if role is None:new_roles.add(key(row['position']))
        elif not role.is_active:raise ValidationError(f'Должность «{role.name}» неактивна. Проверьте справочник перед загрузкой.')
        if key(row['category']) not in categories:new_categories.add(key(row['category']))
        variant=row['category']+' / '+row['model']
        for name in [row['category'],variant]:
            found=types.get(key(name))
            if found is None:new_types.add(key(name))
            elif not found.is_active:raise ValidationError(f'Вид техники «{name}» неактивен.')
        root=types.get(key(row['category']));model=types.get(key(variant))
        if root and root.parent_id:raise ValidationError(f'Название категории занято моделью: {row["category"]}')
        if model and (not root or model.parent_id!=root.pk):raise ValidationError(f'Модель «{variant}» имеет другую привязку к категории.')
        positions[key(variant)].add(key(row['position']))
        candidates={u.pk:u for u in [by_import.get(row['identity']),*by_plate.get(key(row['plate']),[]),*by_inventory.get(key(row['inventory']),[])] if u}
        if len(candidates)>1:raise ValidationError(f'Строка {row["row"]}: номера указывают на разные существующие машины.')
        unit=next(iter(candidates.values()),None)
        if unit:
            expected=(row['model'],row['year'],row['inventory'],row['plate'],row['vin'],row['site'])
            actual=(unit.brand_model or unit.name,unit.manufacture_year,unit.inventory_number,unit.plate_number,unit.vin,unit.site_name)
            same_role=unit.operator_position and key(unit.operator_position.name)==key(row['position'][:100])
            if expected!=actual or not model or unit.type_id!=model.pk or not same_role:raise ValidationError(f'Строка {row["row"]}: машина уже существует с другими реквизитами. Данные не перезаписываются.')
        else:new_units+=1
        result.append({**row,'existing':unit is not None})
    for model,roles_for_model in positions.items():
        if len(roles_for_model)>1:warnings.append(f'Для модели «{model}» несколько должностей. Должность сохраняется у каждой машины; общий экипаж модели автоматически не назначается.')
    return {'rows':result,'new_units':new_units,'new_roles':len(new_roles),'new_types':len(new_types),'new_categories':len(new_categories),'warnings':warnings}


@transaction.atomic
def apply(user,rows,signature):
    require_roles(user,PLAN_ROLES)
    Company.objects.select_for_update().get(pk=user.company_id)
    for model in MODELS:list(model.objects.select_for_update().filter(company=user.company).values_list('pk',flat=True))
    if fingerprint(user.company)!=signature:raise ValidationError('Справочники изменились. Выполните предпросмотр повторно.')
    report=inspect(user.company,rows)
    roles=index(Brigade.objects.filter(company=user.company,merge_source__isnull=True),True)
    categories=index(EquipmentCategory.objects.filter(company=user.company))
    types=index(EquipmentType.objects.filter(company=user.company,merge_source__isnull=True),True)
    position_sets=defaultdict(set)
    root_positions=defaultdict(set)
    for unit in Equipment.objects.filter(company=user.company,operator_position__isnull=False).select_related('type'):
        position_sets[unit.type_id].add(unit.operator_position_id)
        if unit.type.parent_id:root_positions[unit.type.parent_id].add(unit.operator_position_id)
    for row in rows:
        role=roles.get(key(row['position']))
        if role is None:
            role=Brigade.objects.create(company=user.company,name=row['position']);roles[key(row['position'])]=role
        position=position_for(role)
        category=categories.get(key(row['category']))
        if category is None:category=EquipmentCategory.objects.create(company=user.company,name=row['category']);categories[key(row['category'])]=category
        root=types.get(key(row['category']))
        if root is None:root=EquipmentType.objects.create(company=user.company,name=row['category'],category=category);types[key(row['category'])]=root
        name=row['category']+' / '+row['model'];variant=types.get(key(name))
        if variant is None:variant=EquipmentType.objects.create(company=user.company,name=name,parent=root,category=category,brand_model=row['model']);types[key(name)]=variant
        position_sets[variant.pk].add(position.pk)
        root_positions[root.pk].add(position.pk)
    for row in report['rows']:
        if row['existing']:continue
        role=roles[key(row['position'])];variant=types[key(row['category']+' / '+row['model'])]
        Equipment.objects.create(company=user.company,type=variant,name=row['model'],brand_model=row['model'],manufacture_year=row['year'],inventory_number=row['inventory'],plate_number=row['plate'],vin=row['vin'],site_name=row['site'],operator_position=position_for(role),import_key=row['identity'])
    for variant,positions in [*position_sets.items(),*root_positions.items()]:
        if len(positions)==1 and not EquipmentCrewPosition.objects.filter(equipment_type_id=variant).exists():
            EquipmentCrewPosition.objects.create(company=user.company,equipment_type_id=variant,position_id=next(iter(positions)),count=1)
    AuditLog.objects.create(user=user,action='CREATE',entity_type='resources.FleetImport',entity_id='summary',new_value={k:v for k,v in report.items() if k not in {'rows','warnings'}},comment='Импорт техники и должностей с листа Сводка')
    return report


@login_required
@require_http_methods(['GET','POST'])
def fleet_import(request):
    require_roles(request.user,PLAN_ROLES)
    from django.http import HttpResponseGone
    return HttpResponseGone('Импорт техники и должностей с листа «Сводка» удалён. Используйте загрузку ежедневных сводок техники или импорт групп существующих записей.')
