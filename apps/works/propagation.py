"""Confirmed catalogue updates down a construction-object tree.

Signed previews protect every affected identity. Frozen plans and facts retain
old catalogue revisions; no plan, quantity or actual-data record is rewritten.
"""
from collections import defaultdict, deque
from copy import copy, deepcopy
from datetime import date
from decimal import Decimal
import hashlib, json
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core import signing
from django.core.exceptions import ValidationError, PermissionDenied
from django.db import transaction
from django.db.models import F
from django.shortcuts import get_object_or_404, render, redirect
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST
from core.permissions import PLAN_ROLES, require_roles, scope_queryset
from apps.projects.models import ConstructionObject
from apps.planning.models import SubobjectWorkOrigin, SmgWorkMapping
from apps.audit.models import AuditLog
from .models import ProjectWork, ProjectWorkItem, WorkPrice
from .schedule_sync import locked, key

SALT='works.descendant-update.v1'
FIELDS={'quantity_per_unit':'Норматив','weight':'Доля стоимости, %','unit':'Единица измерения','load_profile_id':'Профиль нагрузки','name':'Название','excel_aliases':'Альтернативные названия','is_legacy_structure':'Старая структура'}
SHARED={'quantity_per_unit','weight','unit'}


def descendants(work):
    objects=list(ConstructionObject.objects.filter(company_id=work.company_id,project_id=work.section.construction_object.project_id))
    found={work.section.construction_object_id}
    while True:
        expanded=found|{obj.pk for obj in objects if obj.parent_id in found}
        if expanded==found:break
        found=expanded
    return objects,found


def graph(company):
    edges=[]
    for row in SubobjectWorkOrigin.objects.filter(company=company).order_by('pk'):
        edges.append((row.source_work_id,row.local_work_id,{str(k):str(v) for k,v in row.item_mapping.items()}))
    for row in SmgWorkMapping.objects.filter(company=company).order_by('pk'):
        edges.append((row.gpr_work_id,row.smg_work_id,{str(k):str(v) for k,v in row.item_mapping.items()}))
    return edges


def state(user,work):
    objects,subtree=descendants(work)
    catalogue=list(ProjectWork.objects.filter(company=user.company,section__construction_object__project_id=work.section.construction_object.project_id,merged_source__isnull=True).select_related('section__construction_object','complex_object').prefetch_related('items').order_by('pk'))
    by_id={w.pk:w for w in catalogue};edges=graph(user.company)
    reachable={work.pk:{str(item.pk):str(item.pk) for item in work.items.all()}}
    adjacency=defaultdict(list)
    for src,dst,mapping in edges:
        if src not in by_id or dst not in by_id:continue
        if by_id[src].company_id!=by_id[dst].company_id:continue
        # Reject corrupt cross-branch origin links rather than propagating them.
        _,allowed=descendants(by_id[src])
        if by_id[dst].section.construction_object_id not in allowed:continue
        adjacency[src].append((dst,mapping));adjacency[dst].append((src,{v:k for k,v in mapping.items()}))
    todo=deque([work.pk])
    while todo:
        current=todo.popleft()
        for other,mapping in adjacency[current]:
            translated={source:mapping[local] for source,local in reachable[current].items() if local in mapping}
            if other in reachable:
                if any(reachable[other].get(k,v)!=v for k,v in translated.items()):raise ValidationError('Противоречивые связи работ. Исправьте соответствия справочника.')
                continue
            reachable[other]=translated;todo.append(other)
    selected=[]
    for target in catalogue:
        if target.section.construction_object_id not in subtree or target.is_legacy_structure and target.pk!=work.pk:continue
        if target.pk in reachable:selected.append((target,reachable[target.pk]))
    ids=[target.pk for target,_ in selected]
    permitted=set(scope_queryset(ProjectWork.objects.filter(pk__in=ids),user).values_list('pk',flat=True))
    if permitted!=set(ids):raise PermissionDenied('Для изменения нужны права на все связанные субобъекты.')
    # Include the whole project graph: concurrent imports, edits and new branches
    # invalidate a preview rather than silently expanding the confirmed scope.
    raw=[[(obj.pk,obj.parent_id) for obj in objects],edges,
         [(w.pk,w.updated_at,str(w.unit_price),w.is_legacy_structure,[(i.pk,i.updated_at,str(i.quantity_per_unit),str(i.weight),i.unit,i.load_profile_id,i.parent_id) for i in w.items.all()]) for w in catalogue],
         list(WorkPrice.objects.filter(company=user.company,work_id__in=by_id).order_by('pk').values_list('pk','work_id','price','effective_from','corrects_id'))]
    digest=hashlib.sha256(json.dumps(raw,default=str,ensure_ascii=False).encode()).hexdigest()
    return selected,digest,edges


def normalize(changes):
    result=deepcopy(changes)
    for item,values in result.get('items',{}).items():
        if not set(values)<=set(FIELDS):raise ValidationError('Недопустимые поля состава.')
        for field in ['quantity_per_unit','weight']:
            if field in values:values[field]=str(Decimal(str(values[field])))
    return result


def prepare(user,work,changes):
    require_roles(user,PLAN_ROLES)
    if work.is_legacy_structure:raise ValidationError('Измените действующую редакцию работы. Прежняя сохранена для истории.')
    selected,digest,_=state(user,work);changes=normalize(changes);plans=[]
    source_ids={str(i.pk) for i in work.items.all()}
    if not set(changes.get('items',{}))<=source_ids:raise ValidationError('Работа в составе другого комплекса.')
    for target,mapping in selected:
        items={str(i.pk):i for i in target.items.all()};updates={};rows=[]
        for source_id,values in changes.get('items',{}).items():
            fields=values if target.pk==work.pk else {k:v for k,v in values.items() if k in SHARED}
            if not fields:continue
            local_id=mapping.get(source_id)
            if local_id not in items:raise ValidationError('В связанном субобъекте нет соответствия работы: '+str(target.section.construction_object)+' / '+target.name)
            item=items[local_id];changed={}
            for field,value in fields.items():
                before=getattr(item,field)
                if str(before)!=str(value):
                    changed[field]=value;rows.append({'item':item.name,'field':FIELDS[field],'before':before,'after':value})
            if changed:updates[local_id]=changed
        price=changes.get('price')
        if price:
            from .prices import price_on,can_backdate_price
            effective=date.fromisoformat(price['effective_from'])
            if effective<timezone.localdate() and not can_backdate_price(user) and not price.get('corrects'):raise ValidationError('Для прошлой цены нужно исправление с основанием.')
            if price.get('corrects'):
                original=WorkPrice.objects.filter(company=user.company,work=target,effective_from=effective).order_by('-pk').first()
                if not original:raise ValidationError('В субобъекте нет исправляемой цены за выбранную дату: '+str(target.section.construction_object))
            rows.append({'item':target.name,'field':'Цена с '+effective.strftime('%d.%m.%Y'),'before':price_on(target,effective),'after':price['price']})
        if rows:plans.append({'work':target,'items':updates,'rows':rows,'new_revision':bool(updates and locked(target))})
    return plans,digest,changes


def preview(request,work,changes):
    plans,digest,changes=prepare(request.user,work,changes)
    payload={'user':request.user.pk,'company':request.user.company_id,'work':work.pk,'changes':changes,'revision':digest}
    from .navigation import return_path
    route=request.resolver_match.url_name
    payload['return_to']=return_path(request,reverse('works:work_detail',args=[work.pk])) if route=='work_item_update' else ''
    payload['catalogue_return']=return_path(request) if route=='work_structure_update' else ''
    payload['anchor']='#work-structure' if route in {'work_structure_update','work_cost_shares'} else ''
    token=signing.dumps(payload,salt=SALT,compress=True)
    return render(request,'works/propagation_preview.html',{'work':work,'plans':plans,'token':token})


def clone_work(work,user):
    revised=copy(work);revised.pk=None;revised._state=copy(work._state);revised._state.adding=True;revised._prefetched_objects_cache={};revised.is_legacy_structure=False;revised.code='';revised.save()
    mapping={};waiting=list(work.items.order_by('sequence','pk'))
    while waiting:
        ready=[i for i in waiting if not i.parent_id or i.parent_id in mapping]
        if not ready:raise ValidationError('Циклическая иерархия состава работ.')
        for old in ready:
            item=copy(old);item.pk=None;item._state=copy(old._state);item._state.adding=True;item.project_work=revised;item.parent_id=mapping.get(old.parent_id);item.is_legacy_structure=False;item.save();mapping[old.pk]=item.pk;waiting.remove(old)
    prices={}
    for old in work.price_history.order_by('pk'):
        entry=WorkPrice.objects.create(company=user.company,work=revised,price=old.price,effective_from=old.effective_from,created_by=old.created_by,reason=old.reason,corrects_id=prices.get(old.corrects_id));prices[old.pk]=entry.pk
    ProjectWork.objects.filter(pk=work.pk).update(is_legacy_structure=True)
    work.items.update(is_legacy_structure=True)
    return revised,mapping


@transaction.atomic
def apply(user,payload):
    require_roles(user,PLAN_ROLES)
    if (payload['user'],payload['company'])!=(user.pk,user.company_id):raise PermissionDenied
    from apps.accounts.models import Company
    Company.objects.filter(pk=user.company_id).update(name=F('name'))
    work=get_object_or_404(scope_queryset(ProjectWork.objects.select_related('section__construction_object'),user),pk=payload['work'])
    project=work.section.construction_object.project_id
    list(ConstructionObject.objects.select_for_update().filter(company=user.company,project_id=project).order_by('pk'))
    catalogue=ProjectWork.objects.filter(company=user.company,section__construction_object__project_id=project)
    list(catalogue.select_for_update().order_by('pk'))
    list(ProjectWorkItem.objects.select_for_update().filter(company=user.company,project_work__in=catalogue).order_by('pk'))
    plans,digest,changes=prepare(user,work,payload['changes'])
    if digest!=payload['revision']:raise ValidationError('Справочник изменился после предпросмотра. Проверьте изменения заново.')
    originals=[{'source_work_id':src,'local_work_id':dst,'item_mapping':mapping} for src,dst,mapping in graph(user.company)]
    replacements={};old_values=[]
    for plan in plans:
        target=plan['work'];mapping={i.pk:i.pk for i in target.items.all()};old_id=target.pk
        if plan['new_revision']:target,mapping=clone_work(target,user)
        replacements[old_id]=(target,mapping)
        for local_id,values in plan['items'].items():
            item=ProjectWorkItem.objects.get(pk=mapping[int(local_id)],company=user.company)
            for field,value in values.items():setattr(item,field,Decimal(value) if field in {'quantity_per_unit','weight'} else value)
            excluded=[field.name for field in item._meta.concrete_fields if field.name not in values and field.attname not in values]
            if set(values)<={'weight'}:
                item._meta.get_field('weight').clean(item.weight,item)
            else:item.full_clean(exclude=excluded)
            if 'is_legacy_structure' in values:item._allow_legacy_structure_change=True
            item.save()
        if changes.get('price'):
            from .prices import change_price
            spec=changes['price'];effective=date.fromisoformat(spec['effective_from']);correction=None
            if spec.get('corrects'):correction=target.price_history.filter(effective_from=effective).order_by('-pk').first()
            change_price(user,target.pk,Decimal(spec['price']),effective,spec.get('reason',''),corrects=correction)
        old_values.append({'work':old_id,'current':target.pk,'rows':[{'item':r['item'],'field':r['field'],'before':str(r['before']),'after':str(r['after'])} for r in plan['rows']]})
    # Current catalogue links follow current revisions, while old cloned local
    # links remain available to historical identities.
    for origin in originals:
        src=origin['source_work_id'];dst=origin['local_work_id']
        if src not in replacements and dst not in replacements:continue
        source,source_map=replacements.get(src,(None,{}));local,local_map=replacements.get(dst,(None,{}))
        if not source:source=ProjectWork.objects.get(pk=src)
        if not local:local=ProjectWork.objects.get(pk=dst)
        if local.is_legacy_structure:continue
        mapped={str(source_map.get(int(a),int(a))):local_map.get(int(b),int(b)) for a,b in origin['item_mapping'].items()}
        SubobjectWorkOrigin.objects.update_or_create(company=user.company,local_work=local,defaults={'source_work':source,'item_mapping':mapped})
    AuditLog.objects.create(user=user,action='UPDATE',entity_type='works.descendant_update',entity_id=str(work.pk),new_value={'updates':old_values},comment='Подтверждены изменения работ объекта и вложенных субобъектов')
    return replacements.get(work.pk,(work,{}))[0],len(plans)


@login_required
@require_POST
def confirm(request):
    require_roles(request.user,PLAN_ROLES)
    try:
        payload=signing.loads(request.POST.get('token',''),salt=SALT,max_age=900)
        work,count=apply(request.user,payload)
    except (signing.BadSignature,ValidationError,KeyError,TypeError,ValueError) as exc:
        error='; '.join(exc.messages) if isinstance(exc,ValidationError) else 'Подтверждение устарело. Откройте работу и повторите изменение.'
        return render(request,'works/propagation_preview.html',{'error':error},status=400)
    messages.success(request,f'Изменения сохранены. Обновлено работ: {count}.')
    if payload.get('return_to'):return redirect(payload['return_to'])
    from urllib.parse import urlencode
    query='?'+urlencode({'return_to':payload['catalogue_return']}) if payload.get('catalogue_return') else ''
    return redirect(reverse('works:work_detail',args=[work.pk])+query+payload.get('anchor',''))
