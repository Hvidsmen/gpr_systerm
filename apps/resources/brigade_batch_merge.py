"""Review and merge all equal-name job groups into their lowest ID."""
import hashlib
import json
from collections import defaultdict
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core import signing
from django.core.exceptions import ValidationError
from django.db import transaction
from django.shortcuts import render,redirect
from django.views.decorators.http import require_http_methods
from core.permissions import require_roles,PLAN_ROLES
from apps.accounts.models import Company
from .models import Brigade,Employee,EquipmentCrewPosition
from .position_catalog import position_key
from .brigade_merge import merge_brigades
from .brigade_merge_refs import remap_crew

SALT='resources.brigade-first-merge.v1'


def preview(company):
    from apps.production.models import LaborPlan,LaborFact
    from apps.planning.models import ResourceMonthAllocation,WorkResourceAssignment
    records=list(Brigade.objects.filter(company=company,merge_source__isnull=True).select_related('group','macro_group','position_mapping__position').order_by('pk'))
    grouped=defaultdict(list)
    for b in records:grouped[position_key(b.name)].append(b)
    assignments=list(WorkResourceAssignment.objects.filter(company=company).values('pk','position_id','crew_snapshot','updated_at','version__status','schedule__status'))
    crews=list(EquipmentCrewPosition.objects.filter(company=company).values('pk','equipment_type_id','position_id','count','is_replacement'))
    people=list(Employee.objects.filter(company=company).values_list('pk','position_id'))
    models=[(LaborPlan,['construction_object_id','date'],'hourly_rate'),(LaborFact,['construction_object_id','date'],'hourly_rate'),(ResourceMonthAllocation,['version_id','month','kind'],'rate')]
    inputs=[]
    for model,identity,rate in models:
        qs=model.objects.filter(company=company)
        if model is ResourceMonthAllocation:qs=qs.filter(version__status__in=['DRAFT','REJECTED'])
        fields=['pk','brigade_id',rate,*identity]+(['load_profile_id'] if model is ResourceMonthAllocation else [])
        inputs.append((identity,rate,list(qs.values(*fields))))
    groups=[]
    for items in grouped.values():
        if len(items)<2:continue
        target=items[0];sources=items[1:];ids={b.pk for b in items};errors=[]
        if not target.is_active:errors.append('Первая запись неактивна. Выберите оставляемую должность вручную.')
        if len({b.unit.strip().casefold() for b in items})>1:errors.append('Разные единицы измерения.')
        positions={b.pk:getattr(b,'position_mapping',None) for b in items}
        position_ids={m.position_id for m in positions.values() if m}
        target_mapping=positions[target.pk]
        if target_mapping:
            aliases={m.position_id:{'position_id':target_mapping.position_id,'brigade_id':target.pk,'name':target.name} for b,m in positions.items() if m and b!=target.pk}
            for a in assignments:
                if any(m.get('position_id') in aliases for m in a['crew_snapshot']):
                    try:remap_crew(a['crew_snapshot'],aliases)
                    except ValidationError as exc:errors.extend(exc.messages)
        by_equipment=defaultdict(set)
        for row in crews:
            if row['position_id'] in position_ids:by_equipment[row['equipment_type_id']].add(row['is_replacement'])
        if any(len(values)>1 for values in by_equipment.values()):errors.append('Разные метки замещения в одном экипаже.')
        for identity,rate,rows in inputs:
            collisions=defaultdict(list)
            for row in rows:
                if row['brigade_id'] in ids:collisions[tuple(row[k] for k in identity)].append(row)
            for rows in collisions.values():
                if len({r[rate] for r in rows if r[rate] is not None})>1:errors.append('Разные ставки в совпадающих строках плана или факта.')
                if any('load_profile_id' in r for r in rows) and len({r.get('load_profile_id') for r in rows})>1:errors.append('Разные профили месячного распределения.')
        groups.append({'target':target,'sources':sources,'source_ids':[b.pk for b in sources],'errors':list(dict.fromkeys(errors)),
            'employees':sum(p in position_ids for _,p in people),'crews':sum(c['position_id'] in position_ids for c in crews),
            'assignments':sum(a['position_id'] in position_ids or any(m.get('position_id') in position_ids for m in a['crew_snapshot']) for a in assignments)})
    fingerprint={'records':[(b.pk,b.name,b.unit,b.is_active,b.group_id,b.macro_group_id,str(b.updated_at),
        str(b.position_mapping.position.default_hourly_rate) if getattr(b,'position_mapping',None) else None) for b in records],
        'assignments':assignments,'crews':crews,'people':people,'inputs':inputs}
    return {'groups':groups,'eligible':[g for g in groups if not g['errors']],
        'blocked':sum(bool(g['errors']) for g in groups),'sources':sum(len(g['sources']) for g in groups if not g['errors']),
        'fingerprint':hashlib.sha256(json.dumps(fingerprint,sort_keys=True,default=str).encode()).hexdigest()}


@login_required
@require_http_methods(['GET','POST'])
def batch_merge(request):
    require_roles(request.user,PLAN_ROLES)
    error=None
    if request.method=='POST':
        try:
            signed=signing.loads(request.POST.get('selection',''),salt=SALT,max_age=1800)
            if signed.get('company')!=request.user.company_id or signed.get('user')!=request.user.pk:raise signing.BadSignature
            if request.POST.get('confirm')!='on':raise ValidationError('Подтвердите объединение по первой записи.')
            with transaction.atomic():
                Company.objects.select_for_update().get(pk=request.user.company_id)
                plan=preview(request.user.company)
                if signed.get('fingerprint')!=plan['fingerprint']:raise ValidationError('Справочник или его связи изменились. Проверьте обновлённый список.')
                if not plan['eligible']:raise ValidationError('Нет доступных групп для объединения.')
                merged=sum(merge_brigades(request.user,g['target'].pk,g['source_ids']) for g in plan['eligible'])
                from .zup_positions import link_existing_employees
                linked=link_existing_employees(request.user.company_id)
                from apps.audit.models import AuditLog
                AuditLog.objects.create(user=request.user,action='UPDATE',entity_type='resources.Brigade',entity_id=str(request.user.company_id),
                    new_value={'groups':len(plan['eligible']),'merged':merged,'linked_employees':linked,'blocked_groups':plan['blocked']},comment='Объединение дублей должностей по первой записи')
            messages.success(request,f'Объединено дублей: {merged}, групп: {len(plan["eligible"])}. Дополнительно связано сотрудников: {linked}. Групп с конфликтами оставлено: {plan["blocked"]}.')
            return redirect('resources:brigade_list')
        except signing.BadSignature:error='Подтверждение устарело. Проверьте список заново.'
        except ValidationError as exc:error=' '.join(exc.messages)+' Изменения не внесены.'
    plan=preview(request.user.company)
    selection=signing.dumps({'company':request.user.company_id,'user':request.user.pk,'fingerprint':plan['fingerprint']},salt=SALT)
    return render(request,'resources/brigade_batch_merge.html',{'preview':plan,'selection':selection,'error':error},status=400 if error else 200)
