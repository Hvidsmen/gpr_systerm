"""Configure a planned machine's crew and remember its equipment catalogue default."""
import hashlib
import json
from decimal import Decimal
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.http import require_http_methods
from apps.resources.models import EquipmentType, EquipmentCrewPosition, Position
from apps.resources.position_catalog import available_positions, position_label
from .models import DailySchedule
from . import resource_editor, work_resources, resource_targets


def catalogue_rows(equipment):
    return list(equipment.crew_positions.order_by('position_id').values('position_id','count','is_replacement'))


def catalogue_revision(equipment):
    return hashlib.sha256(json.dumps(catalogue_rows(equipment),sort_keys=True).encode()).hexdigest()


def compatible(machine,people):
    return (machine.start_date,machine.end_date,machine.shifts_per_day,machine.hours_per_shift)==(people.start_date,people.end_date,people.shifts_per_day,people.hours_per_shift)


def check_open(owner,machine):
    if isinstance(owner,DailySchedule) and owner.locked_before and machine.start_date<owner.locked_before:
        raise ValidationError('Экипаж этого назначения зафиксирован в закрытых днях СМГ. Добавьте назначение на открытый период.')


def editor_data(owner,machine,equipment):
    existing={row['position_id'] for row in machine.crew_snapshot}|{row['position_id'] for row in catalogue_rows(equipment)}
    positions={p.pk:{'id':p.pk,'name':position_label(p)} for p in available_positions(owner.company)}
    for position in Position.objects.filter(company=owner.company,pk__in=existing):
        positions.setdefault(position.pk,{'id':position.pk,'name':position.name})
    return {'assignment_id':machine.pk,'revision':machine.updated_at.isoformat(),'catalogue_revision':catalogue_revision(equipment),
        'label':equipment.name,'count':machine.count,'crew':[{key:r[key] for key in ['position_id','count','is_replacement']} for r in machine.crew_snapshot] if machine.include_crew else catalogue_rows(equipment),
        'positions':sorted(positions.values(),key=lambda p:p['name']),
        'standalone':[{'id':row.pk,'revision':row.updated_at.isoformat(),'position_id':row.position_id,'label':str(row.position),'count':row.count,'compatible':compatible(machine,row),
                       'scope':'; '.join(t['label'] for t in resource_targets.describe(row)) or 'Общие ресурсы объекта'} for row in work_resources.owner_rows(owner).filter(position__isnull=False)]}


@login_required
@require_http_methods(['GET','POST'])
def crew(request,pk,kind='gpr'):
    from .resource_graph import owner_for
    owner=owner_for(request.user,kind,pk)
    try:
        payload=json.loads(request.body) if request.method=='POST' else request.GET
        if request.method=='POST' and (not isinstance(payload,dict) or set(payload)-{'assignment_id','revision','catalogue_revision','crew'}):raise ValueError
        raw=payload.get('assignment_id')
        if request.method=='POST' and type(raw) is not int:raise ValueError
        assignment_id=int(raw)
        with transaction.atomic():
            owner=type(owner).objects.select_for_update().get(pk=owner.pk,company=request.user.company)
            work_resources.editable(request.user,owner)
            machine=get_object_or_404(owner.resource_assignments.select_for_update(),pk=assignment_id,equipment_type__isnull=False)
            check_open(owner,machine)
            equipment=get_object_or_404(EquipmentType.objects.select_for_update(),pk=machine.equipment_type_id,company=owner.company,merge_source__isnull=True)
            if request.method=='GET':return JsonResponse(editor_data(owner,machine,equipment))
            if payload.get('revision')!=machine.updated_at.isoformat() or payload.get('catalogue_revision')!=catalogue_revision(equipment):
                raise ValidationError('Техника или её экипаж уже изменены. Закройте настройку и откройте её снова.',code='stale')
            rows=payload.get('crew')
            if not isinstance(rows,list) or len(rows)>100:raise ValueError
            allowed_ids=set(available_positions(owner.company).values_list('pk',flat=True))|{r['position_id'] for r in machine.crew_snapshot}|{r['position_id'] for r in catalogue_rows(equipment)}
            allowed=set(Position.objects.filter(company=owner.company,pk__in=allowed_ids).values_list('pk',flat=True))
            seen=set();sources=set();transfers=[];transfer_rates={}
            previous={r['position_id']:r['count'] for r in machine.crew_snapshot} if machine.include_crew else {}
            for row in rows:
                if not isinstance(row,dict) or set(row)-{'position_id','count','is_replacement','source_assignment_id','source_revision'}:raise ValueError
                position=row.get('position_id');count=row.get('count')
                if type(position) is not int or position not in allowed or position in seen or type(count) is not int or not 1<=count<=1000 or type(row.get('is_replacement')) is not bool:raise ValueError
                seen.add(position)
                source_id=row.get('source_assignment_id')
                if source_id is None:continue
                if type(source_id) is not int or source_id in sources:raise ValueError
                sources.add(source_id)
                source=get_object_or_404(owner.resource_assignments.select_for_update(),pk=source_id,position_id=position,equipment_type__isnull=True)
                if row.get('source_revision')!=source.updated_at.isoformat():raise ValidationError('Отдельная должность уже изменена. Откройте настройку снова.',code='stale')
                needed=(count-previous.get(position,0))*machine.count
                if needed<=0 or not compatible(machine,source) or source.count<needed:
                    raise ValidationError('Для переноса нужны совпадающие период и режим работы, а также достаточное количество отдельно запланированных людей. Укажите увеличение экипажа.')
                source_member=next((m for m in source.crew_snapshot if m['position_id']==position),None)
                old_member=next((m for m in machine.crew_snapshot if m['position_id']==position),None) if machine.include_crew else None
                if not source_member or old_member and Decimal(old_member['hourly_rate'])!=Decimal(source_member['hourly_rate']):
                    raise ValidationError('У этой должности отличаются зафиксированные ставки. Сохраните её отдельным ресурсом.')
                transfer_rates[position]=source_member['hourly_rate']
                transfers.append((source,needed))
            before=catalogue_rows(equipment)
            equipment.crew_positions.exclude(position_id__in=seen).delete()
            for row in rows:
                EquipmentCrewPosition.objects.update_or_create(company=owner.company,equipment_type=equipment,position_id=row['position_id'],defaults={'count':row['count'],'is_replacement':row['is_replacement']})
            machine.include_crew=bool(rows)
            saved=work_resources.save(request.user,owner,machine,refresh_crew=True,targets=resource_targets.identities(machine),preserve_crew_rates=True,crew_rate_overrides=transfer_rates)
            changed=[];removed=[]
            for source,needed in transfers:
                source.count-=needed
                if source.count:
                    source=work_resources.save(request.user,owner,source)
                    changed.append(resource_editor.serialize(source))
                else:
                    removed.append(source.pk);work_resources.remove(request.user,owner,source)
            from apps.audit.models import AuditLog
            AuditLog.objects.create(user=request.user,action='UPDATE',entity_type='resources.EquipmentCrewPosition',entity_id=str(equipment.pk),old_value={'crew':before},new_value={'crew':catalogue_rows(equipment),'assignment_id':saved.pk,'transferred':list(sources)},comment='Экипаж сохранён из схемы связей в плане и справочнике техники')
            return JsonResponse({'assignment':resource_editor.serialize(saved),'updated':changed,'removed':removed,'workflow':resource_editor.workflow_state(owner)})
    except (ValueError,TypeError,UnicodeDecodeError):return JsonResponse({'error':'Проверьте должности и численность экипажа.'},status=400)
    except ValidationError as exc:return JsonResponse({'error':' '.join(exc.messages)},status=409 if getattr(exc,'code',None)=='stale' else 400)
