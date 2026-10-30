"""Authoritative object catalogue sync, including descendant catalogues.

Existing plan/fact identities are retained as legacy revisions when a unit or
norm changes. No allocations, quantities, facts or frozen snapshots are edited.
"""
from collections import defaultdict
from decimal import Decimal
import hashlib
import json
from django.core.exceptions import ValidationError
from django.db import transaction
from core.permissions import PLAN_ROLES, require_roles, scope_queryset
from apps.projects.models import ConstructionObject, Section
from apps.production.entry_concurrency import sqlite_serialized
from apps.audit.models import AuditLog
from .models import ComplexObject, ProjectWork, ProjectWorkItem, MeasurementUnit


def key(value):
    return ' '.join(str(value or '').split()).casefold()


def targets(user,obj):
    available={o.pk:o for o in ConstructionObject.objects.filter(company=user.company,project_id=obj.project_id)}
    if obj.pk not in available:raise ValidationError('Объект недоступен.')
    found={obj.pk};previous=set()
    while found!=previous:
        previous=set(found)
        found.update(o.pk for o in available.values() if o.parent_id in previous)
    permitted=set(scope_queryset(ConstructionObject.objects.filter(pk__in=found),user).values_list('pk',flat=True))
    if permitted!=found:raise ValidationError('Для загрузки структуры нужны права на выбранный объект и все его субобъекты.')
    return [available[pk] for pk in sorted(found)]


def revision(company,objects,section=None):
    ids=[o.pk for o in objects]
    works=ProjectWork.objects.filter(company=company,section__construction_object_id__in=ids).order_by('pk')
    data=[[(o.pk,o.parent_id,o.project_id) for o in objects],section.pk if section else None,
        list(Section.objects.filter(company=company,construction_object_id__in=ids).order_by('pk').values_list('pk','construction_object_id','name')),
        list(ComplexObject.objects.filter(company=company,construction_object_id__in=ids).order_by('pk').values_list('pk','section_id','name')),
        list(works.values_list('pk','name','section_id','complex_object_id','kind','unit','rd_section','rdc_point','is_legacy_structure','updated_at')),
        list(ProjectWorkItem.objects.filter(company=company,project_work__in=works).order_by('pk').values_list('pk','project_work_id','parent_id','name','unit','quantity_per_unit','weight','load_profile_id','is_legacy_structure','updated_at'))]
    return hashlib.sha256(json.dumps(data,default=str,ensure_ascii=False).encode()).hexdigest()


def choose(rows,label):
    active=[row for row in rows if not row.is_legacy_structure]
    if not active and rows:return max(rows,key=lambda row:row.pk)
    candidates=active
    if len(candidates)>1:raise ValidationError('Неоднозначное совпадение в справочнике: '+label)
    return candidates[0] if candidates else None


def locked(work):
    if not work:return False
    from apps.planning.models import GlobalPlanVersion,DailyScheduleEntry
    return (work.daily_facts.exists() or work.opening_balances.filter(quantity__gt=0).exists()
        or GlobalPlanVersion.objects.filter(work_allocations__work=work,status__in=['SUBMITTED','APPROVED','COMPLETED']).exists()
        or work.monthly_plans.filter(versions__daily_plans__isnull=False).exists()
        or DailyScheduleEntry.objects.filter(schedule__company_id=work.company_id,schedule__status='APPLIED',key='work:'+str(work.pk)).exists())


def inspect_target(company,obj,rows,section=None):
    from .unit_aliases import aliases
    unit_aliases=aliases(company)
    sections=defaultdict(list)
    for record in Section.objects.filter(company=company,construction_object=obj):sections[key(record.name)].append(record)
    objects=defaultdict(list)
    for record in ComplexObject.objects.filter(company=company,construction_object=obj):objects[key(record.name)].append(record)
    works=defaultdict(list)
    catalogue=list(ProjectWork.objects.filter(company=company,section__construction_object=obj,merged_source__isnull=True).select_related('section','complex_object').prefetch_related('items'))
    for work in catalogue:
        if work.complex_object_id:works[(key(work.complex_object.name),key(work.name))].append(work)
    result=[];packages={};object_sections={}
    for raw in rows:
        row={**raw,'unit':unit_aliases.get(raw['unit'],raw['unit'])}
        name=row.get('section') or (section.name if section else '')
        if not name:raise ValidationError('В файле нет столбца «Раздел». Выберите раздел для старого формата.')
        if len(sections[key(name)])>1:raise ValidationError('Несколько разделов с названием: '+name)
        if len(objects[key(row['object'])])>1:raise ValidationError('Неоднозначный комплексный объект: '+row['object'])
        if key(row['object']) in object_sections and object_sections[key(row['object'])]!=key(name):raise ValidationError('Комплексный объект указан в разных разделах: '+row['object'])
        object_sections[key(row['object'])]=key(name)
        package_key=(key(row['object']),key(row['package']))
        if package_key not in packages:
            package=choose(works[package_key],row['object']+' / '+row['package'])
            if package and package.kind!='COMPOSITE':raise ValidationError('Совпадение с самостоятельной работой: '+row['package'])
            packages[package_key]={'work':package,'rows':[],'revision':False,'locked':locked(package)}
        group=packages[package_key];package=group['work']
        siblings=list(package.items.all()) if package else []
        item=choose([i for i in siblings if i.parent_id is None and key(i.name)==key(row['work'])],row['work'])
        modified=bool(item and (item.unit!=row['unit'] or item.quantity_per_unit!=Decimal(row['quantity'])))
        structure_change=modified or (package and not item)
        if structure_change and group['locked']:group['revision']=True
        updated=bool(package and (package.is_legacy_structure or key(package.section.name)!=key(name) or (package.rd_section,package.rdc_point)!=(row['rd_section'],row['rdc_point']))) or bool(item and item.is_legacy_structure) or modified
        row.update(section=name,existing=bool(item),action='Обновить' if updated else 'Без изменений' if item else 'Добавить',package_id=package.pk if package else None,item_id=item.pk if item else None)
        group['rows'].append(row);result.append(row)
    matched_packages={group['work'].pk for group in packages.values() if group['work']}
    matched_items={row['item_id'] for row in result if row['item_id']}
    full=all(row.get('section') for row in rows)
    archive=[]
    for work in catalogue:
        if not full and key(work.section.name)!=key(section.name):continue
        if work.pk not in matched_packages and not work.is_legacy_structure:archive.append({'kind':'Комплексная работа' if work.kind=='COMPOSITE' else 'Работа','name':work.name,'id':work.pk})
        if work.pk in matched_packages:
            for item in work.items.all():
                if item.pk not in matched_items and not item.is_legacy_structure:archive.append({'kind':'Работа в комплексе','name':work.name+' → '+item.name,'id':item.pk})
    for group in packages.values():
        if len({(r['rd_section'],r['rdc_point']) for r in group['rows']})!=1:raise ValidationError('У одного комплекса различаются реквизиты РД: '+group['rows'][0]['package'])
        if group['revision']:
            for row in group['rows']:row['action']='Новая редакция (прежняя сохранится в истории)'
            if not group['work'].is_legacy_structure:archive.append({'kind':'Прежняя редакция','name':group['work'].name,'id':group['work'].pk})
    return {'rows':result,'packages':packages,'archive':archive,'catalogue':catalogue,'full':full}


def preview_catalogue(user,obj,rows,section=None):
    objects=targets(user,obj);plans=[(o,inspect_target(user.company,o,rows,section)) for o in objects]
    primary=next(plan for o,plan in plans if o.pk==obj.pk)
    return {'rows':primary['rows'],'targets':[o for o,_ in plans],'archived_rows':primary['archive'],
        'new_count':sum(r['action']=='Добавить' for _,p in plans for r in p['rows']),
        'updated_count':sum(r['action'] not in {'Добавить','Без изменений'} for _,p in plans for r in p['rows']),
        'archived_count':sum(len(p['archive']) for _,p in plans),'revision':revision(user.company,objects,section)}


def mark(work,legacy):
    work.is_legacy_structure=legacy
    ProjectWork.objects.filter(pk=work.pk).update(is_legacy_structure=legacy)


@sqlite_serialized
@transaction.atomic
def apply_catalogue(user,obj,profile,rows,section=None,*,expected=None):
    require_roles(user,PLAN_ROLES)
    if profile.company_id!=user.company_id:raise ValidationError('Профиль другой компании.')
    if section and (section.company_id!=user.company_id or section.construction_object_id!=obj.pk):raise ValidationError('Раздел другого объекта.')
    request_key=hashlib.sha256(json.dumps([expected,rows,profile.pk],sort_keys=True).encode()).hexdigest() if expected else None
    if expected:
        receipt=AuditLog.objects.filter(user=user,entity_type='works.schedule_import',entity_id=str(obj.pk),new_value__receipt=request_key).first()
        if receipt:return receipt.new_value['counts']
    objects=targets(user,obj)
    list(ConstructionObject.objects.select_for_update().filter(pk__in=[o.pk for o in objects]).order_by('pk'))
    if expected and revision(user.company,objects,section)!=expected:raise ValidationError('Структура или состав субобъектов изменились после предпросмотра. Загрузите файл заново.')
    # Validate every descendant before changing any catalogue.
    plans=[(o,inspect_target(user.company,o,rows,section)) for o in objects]
    counts={'objects':len(objects),'created':0,'updated':0,'archived':0};saved={}
    for target,plan in plans:
        seen_works=set();seen_items=set();saved[target.pk]={}
        sections_by_name={key(sec.name):sec for sec in Section.objects.filter(company=user.company,construction_object=target)}
        objects_by_name={key(co.name):co for co in ComplexObject.objects.filter(company=user.company,construction_object=target)}
        for package_key,group in plan['packages'].items():
            source=group['work'];first=group['rows'][0]
            sec=sections_by_name.get(key(first['section']))
            if not sec:
                sec=Section.objects.create(company=user.company,construction_object=target,name=first['section']);sections_by_name[key(sec.name)]=sec
            co=objects_by_name.get(key(first['object']))
            if not co:
                co=ComplexObject.objects.create(company=user.company,construction_object=target,section=sec,name=first['object']);objects_by_name[key(co.name)]=co
            elif co.section_id!=sec.pk:
                ComplexObject.objects.filter(pk=co.pk).update(section=sec);co.section=sec
                ProjectWork.objects.filter(company=user.company,complex_object=co).update(section=sec)
            work=None if group['revision'] else source
            if work is None:
                work=ProjectWork(company=user.company,section=sec,complex_object=co,name=first['package'],kind='COMPOSITE',unit=source.unit if source else '',unit_price=source.unit_price if source else 0,allow_fractional=source.allow_fractional if source else True,work_group=source.work_group if source else None,rd_section=first['rd_section'],rdc_point=first['rdc_point'])
                work.full_clean();work.save()
                if source:
                    from .models import WorkPrice
                    for price in source.price_history.order_by('effective_from','pk'):
                        WorkPrice.objects.create(company=user.company,work=work,price=price.price,effective_from=price.effective_from,created_by=user,reason='Новая редакция справочника: '+price.reason)
                    mark(source,True);source.items.update(is_legacy_structure=True)
            else:
                work.section=sec;work.complex_object=co;work.rd_section=first['rd_section'];work.rdc_point=first['rdc_point'];work.is_legacy_structure=False;work._allow_legacy_structure_change=True
                work.full_clean();work.save()
            seen_works.add(work.pk);saved[target.pk][package_key]=(work,{})
            for number,row in enumerate(group['rows'],1):
                previous=next((i for i in source.items.all() if i.pk==row['item_id']),None) if source else None
                item=previous if previous and not group['revision'] else None
                if item is None:
                    item=ProjectWorkItem(company=user.company,project_work=work,name=row['work'],sequence=number,load_profile=previous.load_profile if previous else profile,weight=previous.weight if previous else 0)
                item.unit=row['unit'];item.quantity_per_unit=Decimal(row['quantity']);item.is_legacy_structure=False;item._allow_legacy_structure_change=True
                item.full_clean();item.save();seen_items.add(item.pk)
                MeasurementUnit.objects.get_or_create(company=user.company,symbol=row['unit'],defaults={'name':row['unit']})
                saved[target.pk][package_key][1][key(row['work'])]=item.pk
                counts['created' if not row['existing'] or group['revision'] else 'updated']+=int(row['action']!='Без изменений')
        # A file with sections is the complete selected object. Old format is
        # authoritative only for the explicitly selected fallback section.
        scope=ProjectWork.objects.filter(company=user.company,section__construction_object=target,merged_source__isnull=True)
        if not plan['full']:scope=scope.filter(section__name__iexact=section.name)
        counts['archived']+=len(plan['archive'])
        missing=scope.exclude(pk__in=seen_works)
        missing.update(is_legacy_structure=True)
        ProjectWorkItem.objects.filter(company=user.company,project_work__in=scope).exclude(pk__in=seen_items).update(is_legacy_structure=True)
    # Explicit source identities let SMG reuse these catalogue entries instead
    # of cloning a second copy when a parent's work is selected.
    from apps.planning.models import SubobjectWorkOrigin
    for target in objects:
        if target.pk==obj.pk:continue
        for package_key,(local,items) in saved[target.pk].items():
            source,source_items=saved[obj.pk][package_key]
            SubobjectWorkOrigin.objects.update_or_create(company=user.company,local_work=local,defaults={'source_work':source,'item_mapping':{str(pk):items[name] for name,pk in source_items.items()}})
    AuditLog.objects.create(user=user,action='UPDATE',entity_type='works.schedule_import',entity_id=str(obj.pk),new_value={'receipt':request_key,'revision':expected,'counts':counts,'objects':[o.pk for o in objects]},comment='Синхронизация полной структуры работ из Excel')
    return counts
