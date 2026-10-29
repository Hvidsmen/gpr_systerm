"""Explicit parent-work identities and aggregate comparison for sub-object SMG."""
from collections import defaultdict
from copy import deepcopy
from decimal import Decimal
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from apps.projects.models import ConstructionObject,Section
from apps.works.models import ProjectWork,ProjectWorkItem,ComplexObject
from apps.works.progress import work_specification,quantity_from_totals
from core.permissions import scope_queryset,require_planning_object
from .models import DailySchedule,DailyScheduleEntry,GlobalPlanVersion,SubobjectWorkOrigin

ZERO=Decimal(0)


def ancestors(obj):
    rows={row.pk:row for row in ConstructionObject.objects.filter(company_id=obj.company_id,project_id=obj.project_id)}
    result=[];current=obj
    while current and current.pk not in result:
        result.append(current.pk);current=rows.get(current.parent_id)
    return result


def valid_reference(obj,source):
    return source.company_id==obj.company_id and source.construction_object_id in ancestors(obj)


def parent_reference(schedule):
    return bool(schedule.source_id and schedule.source.construction_object_id!=schedule.construction_object_id)


def reference_choices(user,obj=None):
    objects=[obj] if obj else list(scope_queryset(ConstructionObject.objects.all(),user))
    tree={row['id']:row for row in ConstructionObject.objects.filter(company=user.company).values('id','parent_id','project_id')}
    ids=set()
    for record in objects:
        node=tree.get(record.pk);seen=set()
        while node and node['id'] not in seen and node['project_id']==record.project_id:
            seen.add(node['id']);ids.add(node['id']);node=tree.get(node['parent_id'])
    return GlobalPlanVersion.objects.filter(company=user.company,construction_object_id__in=ids,status__in=['APPROVED','COMPLETED']).select_related('construction_object').defer('snapshot')


def frozen_specs(schedule):
    from .report_projection import period_snapshot
    from .daily_schedule import bounds
    key=(schedule.source_id,schedule.month)
    cached=getattr(schedule,'_smg_frozen_specs',None)
    if not cached or cached[0]!=key:
        cached=(key,{s['id']:s for s in period_snapshot(schedule.source,*bounds(schedule.month)).get('works',[])})
        schedule._smg_frozen_specs=cached
    return cached[1]


def origin(work,schedule):
    cached=getattr(work,'_smg_gpr_origin',None)
    if cached:return cached
    if not parent_reference(schedule):return None
    saved=SubobjectWorkOrigin.objects.filter(company_id=schedule.company_id,local_work=work,source_work__section__construction_object_id=schedule.source.construction_object_id).first()
    if saved:return {'work_id':saved.source_work_id,'items':saved.item_mapping}
    previous=DailyScheduleEntry.objects.filter(schedule__company_id=schedule.company_id,schedule__construction_object_id=schedule.construction_object_id,
        gpr_work__section__construction_object_id=schedule.source.construction_object_id,key='work:'+str(work.pk)).order_by('-pk').first()
    if previous:return {'work_id':previous.gpr_work_id,'items':previous.metadata.get('gpr_item_mapping',{})}
    # Reuse explicit distributions made by the earlier sub-object planning UI.
    from .models import SmgWorkMapping
    link=SmgWorkMapping.objects.filter(company_id=schedule.company_id,smg_work=work,workspace__construction_object_id=schedule.construction_object_id,workspace__parent_gpr__construction_object_id=schedule.source.construction_object_id).first()
    if link:return {'work_id':link.gpr_work_id,'items':{str(source):int(local) for local,source in link.item_mapping.items()}}
    return None


@transaction.atomic
def local_work(user,schedule,source):
    require_planning_object(user,schedule.construction_object_id)
    if source.company_id!=schedule.company_id:raise ValidationError('Работа другой компании.')
    if source.section.construction_object_id==schedule.construction_object_id:return source
    if source.section.construction_object_id not in ancestors(schedule.construction_object):
        raise ValidationError('Выберите работу субобъекта или его родительского объекта.')
    ConstructionObject.objects.select_for_update().get(pk=schedule.construction_object_id,company=user.company)
    captured=frozen_specs(schedule).get(source.pk) if parent_reference(schedule) and source.section.construction_object_id==schedule.source.construction_object_id else None
    spec=captured or work_specification(source)
    expected=spec.get('valuation_items',spec['items'])
    candidates=[(row.local_work_id,row.item_mapping) for row in SubobjectWorkOrigin.objects.filter(company=user.company,source_work=source,local_work__section__construction_object_id=schedule.construction_object_id).order_by('-pk')]
    candidates += [(entry.metadata.get('id'),entry.metadata.get('gpr_item_mapping',{})) for entry in DailyScheduleEntry.objects.filter(schedule__company_id=schedule.company_id,schedule__construction_object_id=schedule.construction_object_id,gpr_work=source).order_by('-pk')]
    from apps.works.unit_aliases import canonical
    for work_id,mapping in candidates:
        work=ProjectWork.objects.filter(pk=work_id,company=user.company,section__construction_object_id=schedule.construction_object_id,merged_source__isnull=True).first()
        if not work or work.kind!=spec['kind']:continue
        if work.unit!=canonical(user.company,spec['unit']):continue
        current={str(i.pk):i for i in work.items.all()}
        if any(str(i['id']) not in mapping or str(mapping[str(i['id'])]) not in current or current[str(mapping[str(i['id'])])].quantity_per_unit!=Decimal(i['norm']) or current[str(mapping[str(i['id'])])].unit!=canonical(user.company,i['unit']) or current[str(mapping[str(i['id'])])].weight!=Decimal(i['weight']) for i in expected):continue
        work._smg_gpr_origin={'work_id':source.pk,'items':mapping};return work
    section=Section.objects.filter(company=user.company,construction_object_id=schedule.construction_object_id,name=source.section.name).order_by('pk').first()
    if not section:section=Section.objects.create(company=user.company,construction_object_id=schedule.construction_object_id,name=source.section.name)
    complex_object=None
    if source.complex_object_id:
        complex_object=ComplexObject.objects.filter(company=user.company,construction_object_id=schedule.construction_object_id,name=source.complex_object.name).first()
        if not complex_object:complex_object=ComplexObject.objects.create(company=user.company,construction_object_id=schedule.construction_object_id,section=section,name=source.complex_object.name)
        if not complex_object.section_id:complex_object.section=section;complex_object.save(update_fields=['section'])
        section=complex_object.section
    work=ProjectWork(company=user.company,section=section,complex_object=complex_object,name=spec['name'],kind=spec['kind'],unit=canonical(user.company,spec['unit']),unit_price=Decimal(spec['unit_price']),allow_fractional=spec['allow_fractional'],load_profile=source.load_profile,work_group=source.work_group,rd_section=source.rd_section,rdc_point=source.rdc_point)
    work.is_legacy_structure=source.is_legacy_structure
    work._price_actor=user;work.save()
    old={item.pk:item for item in source.items.all()};mapping={};parents=[]
    for frozen in expected:
        original=old.get(frozen['id'])
        if not original:raise ValidationError('Состав работы общего ГПР изменён. Проверьте справочник.')
        item=deepcopy(original);item.pk=None;item._state.adding=True;item.code='';item.project_work=work;item.parent=None
        item.name=frozen['name'];item.unit=canonical(user.company,frozen['unit']);item.quantity_per_unit=Decimal(frozen['norm']);item.weight=Decimal(frozen['weight']);item.save()
        mapping[str(original.pk)]=item.pk;parents.append((item,original.parent_id))
    for item,parent in parents:
        if parent and str(parent) in mapping:item.parent_id=mapping[str(parent)];item.save(update_fields=['parent'])
    link=SubobjectWorkOrigin(company=user.company,source_work=source,local_work=work,item_mapping=mapping);link.full_clean();link.save()
    work._smg_gpr_origin={'work_id':source.pk,'items':mapping};return work


def annotate_entry(entry,work):
    link=origin(work,entry.schedule)
    if link:
        entry.gpr_work_id=link['work_id'];entry.metadata['gpr_source_work_id']=link['work_id'];entry.metadata['gpr_item_mapping']=link['items']


def add_parent_structure(user,schedule):
    """Copy exactly the monthly composition, retaining full valuation norms."""
    from .daily_schedule_table import add_work_branch
    before=schedule.entries.filter(kind='work').count()
    for work_id,spec in frozen_specs(schedule).items():
        if not spec.get('plans'):continue
        selected=[item['id'] for item in spec['items']] if spec['kind']=='COMPOSITE' else None
        add_work_branch(user,schedule,'package:'+str(work_id),selected_items=selected)
    return schedule.entries.filter(kind='work').count()-before


@transaction.atomic
def map_entry(user,schedule,entry_id,source_id):
    from core.permissions import require_roles,PLAN_ROLES
    require_roles(user,PLAN_ROLES);require_planning_object(user,schedule.construction_object_id)
    schedule=DailySchedule.objects.select_for_update().get(pk=schedule.pk,company=user.company)
    if schedule.status!='DRAFT' or not parent_reference(schedule):raise ValidationError('Связи с общим ГПР меняются в черновике СМГ субобъекта.')
    entry=DailyScheduleEntry.objects.select_for_update().get(pk=entry_id,schedule=schedule,kind='work')
    if schedule.locked_before and any(row['date']<schedule.locked_before.isoformat() and Decimal(str(row['quantity'])) for row in entry.rows):
        raise ValidationError('У работы есть закрытые дни. Их связь с общим ГПР сохраняется в истории.')
    source=ProjectWork.objects.get(pk=source_id,company=user.company,section__construction_object_id=schedule.source.construction_object_id,merged_source__isnull=True)
    work=ProjectWork.objects.get(pk=entry.metadata['id'],company=user.company,section__construction_object_id=schedule.construction_object_id)
    captured=frozen_specs(schedule).get(source.pk) if parent_reference(schedule) and source.section.construction_object_id==schedule.source.construction_object_id else None
    spec=captured or work_specification(source)
    from apps.works.unit_aliases import canonical
    if work.kind!=spec['kind'] or canonical(user.company,work.unit)!=canonical(user.company,spec['unit']):raise ValidationError('Вид работы и единица измерения должны совпадать с выбранной работой общего ГПР.')
    mapping={};claimed=set()
    for item in work.items.all():
        matches=[i for i in spec.get('valuation_items',spec['items']) if i['name']==item.name and canonical(user.company,i['unit'])==canonical(user.company,item.unit) and Decimal(i['norm'])==item.quantity_per_unit and i['id'] not in claimed]
        if len(matches)==1:mapping[str(matches[0]['id'])]=item.pk;claimed.add(matches[0]['id'])
    if work.kind=='COMPOSITE' and {i['id'] for i in entry.metadata.get('items',[])}-set(mapping.values()):raise ValidationError('Состав и нормативы отличаются от общего ГПР. Добавьте нужные работы из каталога родительского объекта.')
    existing=SubobjectWorkOrigin.objects.filter(local_work=work).first()
    if existing and existing.source_work_id!=source.pk:raise ValidationError('Эта работа уже связана с другой работой общего ГПР. Добавьте нужную позицию из каталога родителя.')
    if existing:existing.item_mapping=mapping;existing.full_clean();existing.save()
    else:
        existing=SubobjectWorkOrigin(company=user.company,source_work=source,local_work=work,item_mapping=mapping);existing.full_clean();existing.save()
    entry.gpr_work=source;entry.metadata['gpr_source_work_id']=source.pk;entry.metadata['gpr_item_mapping']=mapping;entry.revision+=1;entry.save(update_fields=['gpr_work','metadata','revision'])
    from apps.audit.models import AuditLog
    AuditLog.objects.create(user=user,action='UPDATE',entity_type='planning.dailyscheduleentry',entity_id=str(entry.pk),new_value={'gpr_work_id':source.pk,'item_mapping':mapping},comment='Явно сопоставлена работа субобъекта с работой общего ГПР')
    return entry


def latest_schedules(schedule):
    candidates=DailySchedule.objects.filter(company_id=schedule.company_id,source_id=schedule.source_id,month=schedule.month).exclude(status='ARCHIVED').select_related('construction_object').order_by('construction_object_id','-revision','-pk')
    selected={}
    for row in candidates:
        if row.construction_object_id!=schedule.source.construction_object_id and valid_reference(row.construction_object,schedule.source):selected.setdefault(row.construction_object_id,row)
    selected[schedule.construction_object_id]=schedule
    return sorted(selected.values(),key=lambda s:s.construction_object.name)


def comparison(schedule):
    if not parent_reference(schedule):return None
    specs=dict(frozen_specs(schedule));schedules=latest_schedules(schedule)
    totals=defaultdict(lambda:defaultdict(lambda:defaultdict(Decimal)));contexts=defaultdict(lambda:defaultdict(lambda:defaultdict(Decimal)));unmapped=[]
    from apps.works.models import WorkMergeSource
    aliases={a.source_work_id:a for a in WorkMergeSource.objects.filter(company_id=schedule.company_id).select_related('item')}
    for entry in DailyScheduleEntry.objects.filter(schedule__in=schedules,kind='work').select_related('schedule__construction_object','gpr_work__section'):
        root=entry.gpr_work_id;merged=aliases.get(root) if root not in specs else None
        if merged and merged.item.project_work_id in specs:root=merged.item.project_work_id
        if root not in specs and entry.gpr_work_id and entry.gpr_work.section.construction_object_id==schedule.source.construction_object_id:
            specs[root]=work_specification(entry.gpr_work)
        if root not in specs:
            if any(Decimal(str(r['quantity'])) for r in entry.rows):unmapped.append({'schedule':entry.schedule,'name':entry.label})
            continue
        reverse={int(local):int(parent) for parent,local in entry.metadata.get('gpr_item_mapping',{}).items()}
        for row in entry.rows:
            item=merged.item_id if merged else reverse.get(row.get('item_id')) if row.get('item_id') else None
            totals[root][entry.schedule_id][item]+=Decimal(str(row['quantity']))
        for row in entry.metadata.get('history',[]):
            if row['date']<schedule.month.isoformat():
                item=merged.item_id if merged else reverse.get(row.get('item_id')) if row.get('item_id') else None
                contexts[root][entry.schedule_id][item]+=Decimal(str(row['quantity']))
    rows=[]
    for pk,spec in specs.items():
        parent_totals=defaultdict(Decimal)
        for row in spec.get('plans',[]):parent_totals[row.get('item_id')]+=Decimal(str(row['quantity']))
        positions=[{'id':None,'name':spec['name'],'label':spec['name'],'unit':spec['unit']}] if spec['kind']=='SIMPLE' else [{'id':i['id'],'name':spec['name']+' → '+i['name'],'label':i['name'],'unit':i['unit']} for i in spec['items']]
        for position in positions:
            cells=[totals[pk][s.pk][position['id']] for s in schedules];overall=sum(cells,ZERO);baseline=parent_totals[position['id']]
            rows.append({'name':position['name'],'label':position['label'],'work_id':pk,'item_id':position['id'],'unit':position['unit'],'gpr':baseline,'total':overall,'own':totals[pk][schedule.pk][position['id']],'delta':overall-baseline,'cells':cells,'kind':'work'})
        if spec['kind']=='COMPOSITE' and spec.get('items'):
            cells=[]
            for s in schedules:
                prior=contexts[pk][s.pk];current=totals[pk][s.pk]
                combined={i:prior[i]+current[i] for i in prior.keys()|current.keys()}
                cells.append(quantity_from_totals(spec,combined)-quantity_from_totals(spec,prior))
            baseline=sum((Decimal(str(r['quantity'])) for r in spec.get('daily',[])),ZERO);overall=sum(cells,ZERO)
            rows.append({'name':spec['name']+' · готовые комплексы','label':'Готовые комплексы','work_id':pk,'package_total':True,'unit':spec['unit'],'gpr':baseline,'total':overall,'own':cells[schedules.index(schedule)] if schedule in schedules else ZERO,'delta':overall-baseline,'cells':cells,'kind':'work'})
    rows+=resource_comparison(schedule,schedules)
    from .subobject_comparison_layout import hierarchy
    return {'source':schedule.source,'schedules':schedules,'rows':rows,'unmapped':unmapped,
            'tree':hierarchy(rows,specs,schedule.company,len(schedules)),
            'own_index':schedules.index(schedule)}


def resource_comparison(schedule,schedules):
    from .report_projection import period_snapshot
    from .daily_schedule import bounds,row_value
    from apps.resources.models import BrigadeMerge,Brigade,EquipmentType
    snapshot=period_snapshot(schedule.source,*bounds(schedule.month))
    brigades={b.pk:b for b in Brigade.objects.filter(company_id=schedule.company_id).select_related('group')}
    gear={e.pk:e for e in EquipmentType.objects.filter(company_id=schedule.company_id).select_related('category','parent__category')}
    aliases=dict(BrigadeMerge.objects.filter(company_id=schedule.company_id).values_list('source_id','target_id'))
    def identity(kind,row):
        key=row.get('brigade_id') if kind=='labor' else row.get('equipment_type_id') if kind=='equipment' else row.get('fuel_type','')
        seen=set()
        while kind=='labor' and key in aliases and key not in seen:seen.add(key);key=aliases[key]
        while kind=='equipment' and key in gear and gear[key].parent_id and key not in seen:seen.add(key);key=gear[key].parent_id
        return (kind,key)
    fields={'labor':[('planned_count','Средняя численность','чел.'),('planned_hours','Трудозатраты','ч')],
            'equipment':[('planned_count','Среднее количество','ед.'),('planned_machine_hours','Машино-часы','ч')],
            'fuel':[('planned_liters','Расход','л'),('planned_balance','Остаток','л')]}
    values=defaultdict(lambda:defaultdict(lambda:defaultdict(Decimal)));labels={};last_dates={}
    def add(owner,kind,rows):
        latest={}
        for row in rows:
            key=identity(kind,row);labels.setdefault(key,row.get('label') or str(key[1]));latest[key]=max(latest.get(key,''),row['date'])
            for field,_,_ in fields[kind]:
                if field!='planned_balance':values[key][owner][field]+=Decimal(str(row_value(kind,row,field)))
        for key,last in latest.items():
            if kind=='fuel':
                previous=last_dates.get((key,owner),'')
                amount=sum((Decimal(str(row_value(kind,row,'planned_balance'))) for row in rows if identity(kind,row)==key and row['date']==last),ZERO)
                if last>previous:values[key][owner]['planned_balance']=amount;last_dates[key,owner]=last
                elif last==previous:values[key][owner]['planned_balance']+=amount
    for kind in fields:add(None,kind,snapshot.get('resources',{}).get(kind,[]))
    for entry in DailyScheduleEntry.objects.filter(schedule__in=schedules,kind__in=fields):add(entry.schedule_id,entry.kind,entry.rows)
    days=Decimal(bounds(schedule.month)[1].day);result=[]
    for (kind,key),by_schedule in sorted(values.items(),key=lambda pair:(list(fields).index(pair[0][0]),str(pair[0][1]))):
        label=brigades[key].name if kind=='labor' and key in brigades else gear[key].name if kind=='equipment' and key in gear else labels[kind,key]
        group_key='none';group_label='Без группы' if kind=='labor' else 'Без категории'
        if kind=='labor' and key in brigades and brigades[key].group_id and brigades[key].group.company_id==schedule.company_id:
            group_key=str(brigades[key].group_id);group_label=brigades[key].group.name
        elif kind=='equipment':
            from .resource_group_totals import equipment_group
            group_label,group_key=equipment_group(gear.get(key),schedule.company)
        for field,measure,unit in fields[kind]:
            divisor=days if field=='planned_count' else Decimal(1)
            cells=[by_schedule[s.pk][field]/divisor for s in schedules];baseline=by_schedule[None][field]/divisor;total=sum(cells,ZERO)
            result.append({'kind':kind,'name':label+' · '+measure,'label':label,'resource_id':key,'measure_field':field,
                'group_key':group_key,'group_label':group_label,'unit':unit,'gpr':baseline,'total':total,
                'own':by_schedule[schedule.pk][field]/divisor,'delta':total-baseline,'cells':cells})
    return result
