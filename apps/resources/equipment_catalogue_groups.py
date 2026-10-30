"""Preview group changes from the planning catalogue export without reparenting."""
from collections import defaultdict
import hashlib,json
from django.core import signing
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from core.permissions import EQUIPMENT_CATALOG_EDIT_ROLES,require_roles
from .models import Equipment,EquipmentType,EquipmentCategory

SALT='resources.equipment-catalogue-groups.v1'
MAX_ROWS=5000


def label(value):
    return ' '.join(str(value or '').split())


def key(value):
    return label(value).casefold()


def preview(user,rows,*,lock=False):
    """Resolve existing records only. A root has exactly one target group per file."""
    require_roles(user,EQUIPMENT_CATALOG_EDIT_ROLES)
    query=EquipmentType.objects.filter(company=user.company,merge_source__isnull=True)
    group_query=EquipmentCategory.objects.filter(company=user.company)
    machine_query=Equipment.objects.filter(company=user.company)
    if lock:query=query.select_for_update();group_query=group_query.select_for_update();machine_query=machine_query.select_for_update()
    types=list(query.order_by('pk'));groups=list(group_query.order_by('pk'));machines=list(machine_query.order_by('pk'))
    by_id={t.pk:t for t in types};by_name=defaultdict(list);roots=defaultdict(list);group_names=defaultdict(list)
    for t in types:
        by_name[key(t.name)].append(t)
        if t.parent_id is None:roots[key(t.name)].append(t)
    for g in groups:group_names[key(g.name)].append(g)
    selected={};warnings=[];errors=[];blank=0
    for row in rows:
        line=f"Строка {row['line']}";name=label(row.get('name'));planning=label(row.get('planning'));group=label(row.get('group'))
        if not group:blank+=1;continue
        candidates=by_name.get(key(name),[])
        root=None
        if planning:
            found=roots.get(key(planning),[])
            if not found:warnings.append(line+f': техника для планирования «{planning}» не найдена; строка пропущена.');continue
            if len(found)>1:errors.append(line+f': несколько плановых позиций «{planning}». Уточните названия в справочнике.');continue
            root=found[0]
            eligible=[t for t in candidates if (t.parent_id or t.pk)==root.pk]
            if candidates and not eligible:errors.append(line+f': «{name}» не относится к «{planning}». Перенос моделей этой загрузкой не выполняется.');continue
            candidates=eligible
        if not candidates:warnings.append(line+f': запись «{name}» не найдена; строка пропущена.');continue
        if len(candidates)>1:errors.append(line+f': название «{name}» неоднозначно. Уточните колонку «Техника для планирования».');continue
        item=candidates[0];root=root or by_id.get(item.parent_id or item.pk)
        if root is None or root.parent_id:errors.append(line+': нарушена связь с техникой для планирования.');continue
        group_key=key(group);clear=group_key=='без группы'
        found=[] if clear else group_names.get(group_key,[])
        if len(found)>1:errors.append(line+f': несколько групп «{group}» отличаются только пробелами или регистром. Уточните группы.');continue
        existing=found[0] if found else None
        entry={'id':root.pk,'name':root.name,'before_id':root.category_id,'before':next((g.name for g in groups if g.pk==root.category_id),'Без группы'),'after_id':existing.pk if existing else None,'after':existing.name if existing else 'Без группы' if clear else group,'group_key':group_key,'create_group':not clear and existing is None,'clear':clear}
        if root.pk in selected and selected[root.pk]['group_key']!=group_key:
            errors.append(line+f': для «{root.name}» указаны разные группы. Укажите одну группу во всех строках этой плановой позиции.');continue
        selected[root.pk]=entry
    if errors:raise ValidationError(errors[:100])
    children_by_root=defaultdict(list);machines_by_root=defaultdict(list)
    for item in types:
        if item.parent_id:children_by_root[item.parent_id].append(item)
    for machine in machines:
        item=by_id.get(machine.type_id)
        if item:machines_by_root[item.parent_id or item.pk].append(machine)
    snapshots=[];changed=[];unchanged=0
    for root_id,entry in sorted(selected.items()):
        children=children_by_root[root_id];units=machines_by_root[root_id];target=entry['after_id']
        root=by_id[root_id]
        model_changes=[t for t in children if entry['create_group'] or t.category_id!=target]
        machine_changes=[m for m in units if entry['create_group'] or m.catalogue_group_id!=target]
        changed_root=entry['create_group'] or root.category_id!=target
        entry.update(models=len(children),machines=len(units),changed_models=len(model_changes),changed_machines=len(machine_changes),changed_root=changed_root)
        snapshots.append([entry,[root.pk,root.name,root.category_id,str(root.updated_at)],[[t.pk,t.parent_id,t.category_id,str(t.updated_at)] for t in children],[[m.pk,m.type_id,m.catalogue_group_id,str(m.updated_at)] for m in units]])
        if changed_root or model_changes or machine_changes:changed.append(entry)
        else:unchanged+=1
    created_labels={}
    for entry in changed:
        if entry['create_group']:
            entry['after']=created_labels.setdefault(entry['group_key'],entry['after'])
    new_groups=sorted(created_labels.values())
    stamp=hashlib.sha256(json.dumps(snapshots,ensure_ascii=False,sort_keys=True).encode()).hexdigest()
    return {'entries':changed,'unchanged':unchanged,'warnings':warnings,'skipped':len(warnings)+blank,'blank':blank,'new_groups':new_groups,'stamp':stamp,'source_rows':len(rows),'positions':len(selected),'models':sum(e['changed_models'] for e in changed),'machines':sum(e['changed_machines'] for e in changed)}


def token(user,rows,report):
    return signing.dumps({'user':user.pk,'company':user.company_id,'rows':rows,'stamp':report['stamp']},salt=SALT,compress=True)


@transaction.atomic
def confirm(user,signed):
    require_roles(user,EQUIPMENT_CATALOG_EDIT_ROLES)
    try:payload=signing.loads(signed,salt=SALT,max_age=1800)
    except signing.BadSignature:raise ValidationError('Предпросмотр устарел. Загрузите файл заново.')
    if not isinstance(payload,dict) or (payload.get('user'),payload.get('company'))!=(user.pk,user.company_id):raise ValidationError('Предпросмотр относится к другому пользователю или компании.')
    rows=payload.get('rows')
    if not isinstance(rows,list) or not 0<len(rows)<=MAX_ROWS:raise ValidationError('Нет строк для обновления.')
    from apps.accounts.models import Company
    from apps.audit.models import AuditLog
    Company.objects.select_for_update().get(pk=user.company_id)
    report=preview(user,rows,lock=True)
    if report['stamp']!=payload.get('stamp'):raise ValidationError('Справочник или состав машин изменился после предпросмотра. Загрузите файл заново и проверьте изменения.')
    created={};now=timezone.now()
    for entry in report['entries']:
        target=entry['after_id']
        if entry['create_group']:
            group=created.get(entry['group_key'])
            if group is None:
                group=EquipmentCategory(company=user.company,name=entry['after']);group.full_clean();group.save();created[entry['group_key']]=group
                AuditLog.objects.create(user=user,action='CREATE',entity_type='resources.EquipmentCategory',entity_id=str(group.pk),new_value={'name':group.name},comment='Группа из подтверждённого Excel справочника техники')
            target=group.pk
        root=EquipmentType.objects.get(company=user.company,pk=entry['id'])
        children=EquipmentType.objects.filter(company=user.company,parent=root);ids=[root.pk,*children.values_list('pk',flat=True)]
        machine_query=Equipment.objects.filter(company=user.company,type_id__in=ids)
        old_models={str(pk):group for pk,group in children.values_list('pk','category_id') if group!=target}
        old_machines={str(pk):group for pk,group in machine_query.values_list('pk','catalogue_group_id') if group!=target}
        before=root.category_id
        root.category_id=target;root.save(update_fields=['category','updated_at'])
        # Also repair legacy overrides when the root itself already has this group.
        children.update(category_id=target,updated_at=now);machine_query.update(catalogue_group_id=target,updated_at=now)
        AuditLog.objects.create(user=user,action='UPDATE',entity_type='resources.EquipmentType',entity_id=str(root.pk),old_value={'category_id':before,'models':old_models,'machines':old_machines},new_value={'category_id':target,'models':{pk:target for pk in old_models},'machines':{pk:target for pk in old_machines}},comment='Группа плановой позиции и связанных моделей/машин из подтверждённого Excel')
    return {**report,'updated':len(report['entries']),'created_groups':len(created)}
