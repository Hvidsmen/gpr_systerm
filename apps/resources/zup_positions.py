"""Populate the shared job catalogue from verified employee job/staffing names."""
from collections import defaultdict
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db import transaction
from django.shortcuts import redirect
from django.views.decorators.http import require_POST
from core.permissions import require_roles
from apps.accounts.models import Company, OneCConnection
from .employee_staffing import staffing_parts
from .models import Brigade, Employee, Position
from .position_catalog import brigade_for, position_for


def employee_job(values):
    if values.get('zup_position_name'):return values['zup_position_name'].strip()
    label=values.get('zup_card',{}).get('staffing_position','')
    name,department=staffing_parts(label)
    # Only the confirmed /department/ format is split. Unknown slash labels
    # remain on the card but must not create a misleading catalogue entry.
    return name if department or '/' not in name else ''


@transaction.atomic
def sync_names(company_id,names):
    Company.objects.select_for_update().get(pk=company_id)
    unique={};skipped=0
    for name in names:
        if not name:continue
        if len(name)>150:skipped+=1;continue
        unique.setdefault(name.strip().casefold(),name.strip())
    existing=defaultdict(list)
    for b in Brigade.objects.filter(company_id=company_id,merge_source__isnull=True):existing[b.name.strip().casefold()].append(b)
    legacy=defaultdict(list)
    for p in Position.objects.filter(company_id=company_id):legacy[p.name.strip().casefold()].append(p)
    result={'created':0,'existing':0,'ambiguous':0,'skipped':skipped};positions={}
    for key,name in unique.items():
        matches=existing.get(key,[])
        if len(matches)>1:result['ambiguous']+=1;continue
        if matches:
            b=matches[0];result['existing']+=1
        elif len(legacy.get(key,[]))==1:
            b=Brigade.objects.get(pk=brigade_for(legacy[key][0]));result['created']+=1
        elif len(legacy.get(key,[]))>1:result['ambiguous']+=1;continue
        else:
            b=Brigade.objects.create(company_id=company_id,name=name,description='Источник: 1С ЗУП, кадровые данные сотрудников.')
            result['created']+=1
        positions[key]=position_for(b)
    return result,positions


@login_required
@require_POST
@transaction.atomic
def from_employees(request):
    require_roles(request.user,{'ADMIN'})
    connection=OneCConnection.objects.filter(company_id=request.user.company_id).first()
    if not connection:
        messages.error(request,'Сначала настройте подключение и загрузите сотрудников из ЗУП.')
        return redirect('resources:brigade_list')
    Company.objects.select_for_update().get(pk=request.user.company_id)
    employees=list(Employee.objects.filter(company_id=request.user.company_id,zup_server=connection.server.casefold(),
        zup_database=connection.database.casefold(),zup_synced_at__isnull=False).exclude(zup_ref=''))
    if not employees:
        messages.error(request,'В этом подключении ещё нет загруженных сотрудников. Выполните загрузку из ЗУП.')
        return redirect('resources:brigade_list')
    values=[{'zup_position_name':e.zup_position_name,'zup_card':e.zup_card} for e in employees]
    result,positions=sync_names(request.user.company_id,[employee_job(v) for v in values])
    linked=0
    for e,v in zip(employees,values):
        position=positions.get(employee_job(v).casefold())
        if position and e.position_id!=position.pk:
            e.position=position;e.save(update_fields=['position']);linked+=1
    from apps.audit.models import AuditLog
    AuditLog.objects.create(user=request.user,action='CREATE',entity_type='resources.Brigade',entity_id=str(connection.pk),new_value={**result,'employees_linked':linked},comment='Должности из загруженных карточек ЗУП; без денежных данных')
    messages.success(request,f"Должности ЗУП: добавлено {result['created']}, уже в справочнике {result['existing']}, неоднозначных {result['ambiguous']}, пропущено длинных названий {result['skipped']}. Связаны карточки сотрудников: {linked}.")
    return redirect('resources:brigade_list')
