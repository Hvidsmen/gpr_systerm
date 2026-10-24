"""Explicit catalogue import of the agreed seven-column schedule format."""
from collections import OrderedDict
from decimal import Decimal, InvalidOperation
from io import BytesIO
from zipfile import ZipFile, BadZipFile
from xml.etree.ElementTree import ParseError
from openpyxl.utils.exceptions import InvalidFileException
from django import forms
from django.core import signing
from django.core.exceptions import ValidationError
from django.contrib import messages
from django.db import transaction
from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect
from openpyxl import load_workbook
from core.permissions import PLAN_ROLES, require_roles, scope_queryset
from apps.projects.models import Section, ConstructionObject
from apps.planning.models import LoadProfile
from apps.audit.models import AuditLog
from .models import ComplexObject, ProjectWork, ProjectWorkItem, MeasurementUnit

HEADERS=['Участок производства работ','Группа работ','Раздел РД','Пункт в РДЦ','Вид работ','Ед. изм.','Проектное количество']
SALT='works.schedule-catalogue.v1'

def text(value):
    return ' '.join(str(value).split()) if value is not None else ''

def parse_schedule(upload):
    raw=upload.read(5*1024*1024+1)
    if len(raw)>5*1024*1024:raise ValidationError('Размер файла не должен превышать 5 МБ.')
    try:
        with ZipFile(BytesIO(raw)) as archive:
            if len(archive.infolist())>5000 or sum(entry.file_size for entry in archive.infolist())>32*1024*1024:
                raise ValidationError('Слишком большой распакованный файл Excel.')
        book=load_workbook(BytesIO(raw),data_only=False)
    except (BadZipFile,ValueError,KeyError,OSError,ParseError,InvalidFileException) as exc:raise ValidationError('Не удалось прочитать XLSX.') from exc
    rows=OrderedDict();ignored=0;matched=0
    try:
        for sheet in book:
            if sheet.max_row>5000 or sheet.max_column>100:raise ValidationError('Лист превышает предел: 5000 строк и 100 столбцов.')
            header=next((n for n in range(1,min(sheet.max_row,30)+1) if [text(sheet.cell(n,c).value).casefold() for c in range(1,8)]==[h.casefold() for h in HEADERS]),None)
            if not header:continue
            matched+=1
            merges=[area for area in sheet.merged_cells.ranges if area.min_col<=7]
            def cell(n,c):
                result=sheet.cell(n,c)
                if result.value is None:
                    for area in merges:
                        if area.min_row<=n<=area.max_row and area.min_col<=c<=area.max_col:
                            result=sheet.cell(area.min_row,area.min_col);break
                return result
            for n in range(header+1,sheet.max_row+1):
                label=text(sheet.cell(n,10).value).casefold().replace(' ','')
                if label=='факт':ignored+=1;continue
                if label!='план':
                    if any(sheet.cell(n,c).value is not None for c in range(1,8)):
                        raise ValidationError(f'{sheet.title}, строка {n}: ожидается «План» или «Факт» в столбце J.')
                    continue
                cells=[cell(n,c) for c in range(1,8)]
                if any(c.data_type=='f' for c in cells):raise ValidationError(f'{sheet.title}, строка {n}: в первых семи столбцах нужны значения, а не формулы.')
                values=[text(c.value) for c in cells]
                for offset in (0,1,4,5):
                    if not values[offset]:raise ValidationError(f'{sheet.title}, строка {n}: не заполнен столбец «{HEADERS[offset]}».')
                if any(len(value)>255 for value in values[:5]) or len(values[5])>50:raise ValidationError(f'{sheet.title}, строка {n}: слишком длинное название или единица измерения.')
                try:quantity=Decimal(values[6].replace(' ','').replace(',','.'))
                except InvalidOperation:raise ValidationError(f'{sheet.title}, строка {n}: неверный норматив.')
                if not quantity.is_finite() or quantity<=0 or quantity>=Decimal('10000000') or quantity!=quantity.quantize(Decimal('.001')):
                    raise ValidationError(f'{sheet.title}, строка {n}: норматив должен быть положительным, до 7 целых и 3 дробных знаков.')
                row=dict(zip(['object','package','rd_section','rdc_point','work','unit'],values[:6]),quantity=str(quantity.quantize(Decimal('.001'))),source=f'{sheet.title}:{n}')
                key=(row['object'].casefold(),row['package'].casefold(),row['work'].casefold())
                if key in rows and any(rows[key][field]!=row[field] for field in ('rd_section','rdc_point','unit','quantity')):
                    raise ValidationError(f'{row["source"]}: повтор работы с различающимися реквизитами или нормативом.')
                rows.setdefault(key,row)
                if len(rows)>2000:raise ValidationError('Можно импортировать до 2000 работ за раз.')
        if not matched or not rows:raise ValidationError('Не найдены строки «План» с согласованными заголовками A–G.')
        return list(rows.values()),ignored
    finally:book.close()

class ImportForm(forms.Form):
    construction_object=forms.ModelChoiceField(queryset=ConstructionObject.objects.none(),label='Строительный объект',empty_label='Выберите объект')
    section=forms.ModelChoiceField(queryset=Section.objects.none(),label='Раздел строительного объекта')
    load_profile=forms.ModelChoiceField(queryset=LoadProfile.objects.none(),label='Профиль нагрузки вложенных работ',help_text='В файле профилей нет. Этот профиль будет назначен только новым работам.')
    file=forms.FileField(label='График XLSX')
    def __init__(self,*args,user,**kwargs):
        super().__init__(*args,**kwargs)
        objects=scope_queryset(ConstructionObject.objects.select_related('project'),user)
        sections=scope_queryset(Section.objects.select_related('construction_object'),user)
        self.fields['construction_object'].queryset=objects
        self.fields['construction_object'].label_from_instance=lambda row:f'{row.project.name} / {row.name}'
        object_id=self.data.get(self.add_prefix('construction_object')) if self.is_bound else self.initial.get('construction_object')
        try:object_id=int(object_id)
        except (TypeError,ValueError):object_id=None
        self.fields['section'].queryset=sections.filter(construction_object_id=object_id,construction_object__in=objects) if object_id else sections.none()
        self.fields['section'].empty_label='Выберите раздел' if object_id else 'Сначала выберите объект'
        self.fields['section'].label_from_instance=lambda row:row.name
        self.section_options=list(sections.filter(construction_object__in=objects).order_by('name','pk').values('id','construction_object_id','name'))
        self.fields['load_profile'].queryset=LoadProfile.objects.filter(company=user.company)


def inspect_rows(company,section,rows):
    """Check all conflicts before any write. Existing quantities are never overwritten."""
    result=[];packages={};objects={}
    for row in rows:
        obj_key=row['object'].casefold()
        if obj_key not in objects:
            found=list(ComplexObject.objects.filter(company=company,construction_object=section.construction_object,name__iexact=row['object']))
            if len(found)>1:raise ValidationError('Неоднозначный комплексный объект: '+row['object'])
            objects[obj_key]=found[0] if found else None
        obj=objects[obj_key]
        if obj and obj.section_id!=section.pk:raise ValidationError('Комплексный объект уже закреплён за другим разделом: '+obj.name)
        key=(obj_key,row['package'].casefold())
        if key not in packages:
            found=list(ProjectWork.objects.filter(company=company,section=section,complex_object=obj,name__iexact=row['package'])) if obj else []
            if len(found)>1:raise ValidationError('Неоднозначная комплексная работа: '+row['package'])
            packages[key]=(found[0] if found else None,row['rd_section'],row['rdc_point'])
        package,rd,point=packages[key]
        if (rd,point)!=(row['rd_section'],row['rdc_point']):raise ValidationError('У одного комплекса различаются Раздел РД или Пункт в РДЦ: '+row['package'])
        if package and (package.kind!=ProjectWork.Kind.COMPOSITE or (package.rd_section,package.rdc_point)!=(rd,point)):
            raise ValidationError('Существующий комплекс имеет другой вид или реквизиты РД: '+row['package'])
        items=list(package.items.filter(name__iexact=row['work'],parent__isnull=True)) if package else []
        if len(items)>1:raise ValidationError('Неоднозначная работа: '+row['work'])
        item=items[0] if items else None
        if item and (item.unit!=row['unit'] or item.quantity_per_unit!=Decimal(row['quantity'])):
            raise ValidationError('У существующей работы другая единица или норматив: '+row['work'])
        result.append({**row,'existing':bool(item)})
    return result

@transaction.atomic
def apply_rows(user,section,profile,rows):
    # Serialize imports to the same construction object, including first writes.
    from apps.projects.models import ConstructionObject
    ConstructionObject.objects.select_for_update().get(pk=section.construction_object_id,company=user.company)
    inspected=inspect_rows(user.company,section,rows);created=0
    for row in inspected:
        if row['existing']:continue
        obj=ComplexObject.objects.filter(company=user.company,construction_object=section.construction_object,name__iexact=row['object']).first()
        if not obj:
            obj=ComplexObject(company=user.company,construction_object=section.construction_object,section=section,name=row['object']);obj.full_clean();obj.save()
        package=ProjectWork.objects.filter(company=user.company,section=section,complex_object=obj,name__iexact=row['package']).first()
        if not package:
            package=ProjectWork(company=user.company,section=section,complex_object=obj,name=row['package'],kind='COMPOSITE',unit='',rd_section=row['rd_section'],rdc_point=row['rdc_point'])
            package.full_clean();package.save()
        MeasurementUnit.objects.get_or_create(company=user.company,symbol=row['unit'],defaults={'name':row['unit']})
        item=ProjectWorkItem(company=user.company,project_work=package,name=row['work'],unit=row['unit'],quantity_per_unit=Decimal(row['quantity']),sequence=package.items.count()+1,load_profile=profile)
        item.full_clean();item.save();created+=1
    AuditLog.objects.create(user=user,action='CREATE',entity_type='works.schedule_import',entity_id=str(section.pk),new_value={'created_items':created,'skipped_items':len(rows)-created},comment='Импорт состава работ из графика Excel')
    return created

@login_required
def schedule_import(request):
    require_roles(request.user,PLAN_ROLES)
    form=ImportForm(request.POST or None,request.FILES or None,user=request.user)
    context={'form':form}
    if request.method=='POST':
        try:
            if request.POST.get('action')=='confirm':
                data=signing.loads(request.POST.get('token',''),salt=SALT,max_age=900)
                if data['user']!=request.user.pk or data['company']!=request.user.company_id:raise ValidationError('Предпросмотр принадлежит другому пользователю.')
                section=scope_queryset(Section.objects.all(),request.user).filter(pk=data['section']).first()
                profile=LoadProfile.objects.filter(company=request.user.company,pk=data['profile']).first()
                if not section or not profile:raise ValidationError('Раздел или профиль больше недоступен. Повторите предпросмотр.')
                count=apply_rows(request.user,section,profile,data['rows'])
                messages.success(request,f'Добавлено работ: {count}. Уже существовали: {len(data["rows"])-count}.')
                return redirect('works:work_list')
            elif form.is_valid():
                rows,ignored=parse_schedule(form.cleaned_data['file'])
                preview=inspect_rows(request.user.company,form.cleaned_data['section'],rows)
                context.update(rows=preview,ignored=ignored,section=form.cleaned_data['section'],new_count=sum(not row['existing'] for row in preview),token=signing.dumps({'user':request.user.pk,'company':request.user.company_id,'section':form.cleaned_data['section'].pk,'profile':form.cleaned_data['load_profile'].pk,'rows':rows},salt=SALT,compress=True))
        except signing.BadSignature:context['error']='Предпросмотр недействителен или истёк. Загрузите файл заново.'
        except ValidationError as exc:context['error']='; '.join(exc.messages)
    return render(request,'works/schedule_import.html',context)
