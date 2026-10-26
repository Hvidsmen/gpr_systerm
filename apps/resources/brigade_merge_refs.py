"""Move live position references while preserving captured and approved history."""
from copy import deepcopy
from decimal import Decimal
from django.core.exceptions import ValidationError
from django.db.models import Q
from django.forms.models import model_to_dict
from .models import Employee,EquipmentCrewPosition,PositionPlanningBrigade


def remap_crew(rows,aliases):
    result=[];indices={}
    for original in rows:
        row=deepcopy(original)
        target=aliases.get(row.get('position_id'))
        if target:
            row.update(position_id=target['position_id'],brigade_id=target['brigade_id'],name=target['name'])
        pk=row.get('position_id')
        if pk in indices:
            existing=result[indices[pk]]
            if Decimal(str(existing.get('hourly_rate',0)))!=Decimal(str(row.get('hourly_rate',0))) or existing.get('is_replacement',False)!=row.get('is_replacement',False):
                raise ValidationError('В зафиксированном экипаже разные ставки или метки замещения у объединяемых должностей. Сначала согласуйте экипаж.')
            existing['count']+=row['count']
        else:indices[pk]=len(result);result.append(row)
    return result


def position_aliases(company):
    from .models import BrigadeMerge
    result={}
    for merge in BrigadeMerge.objects.filter(company=company).select_related('source__position_mapping','target__position_mapping__position'):
        source=getattr(merge.source,'position_mapping',None);target=getattr(merge.target,'position_mapping',None)
        if source and target:
            result[source.position_id]={'position_id':target.position_id,'brigade_id':merge.target_id,'name':merge.target.name}
    return result


def merge_position_references(company,source,target,audit):
    from .position_catalog import position_for
    from apps.planning.models import WorkResourceAssignment
    from apps.planning.work_resources import sync_schedule
    source_position=position_for(source);target_position=position_for(target)
    alias={source_position.pk:{'position_id':target_position.pk,'brigade_id':target.pk,'name':target.name}}
    # Check every captured crew, including frozen ones which future copies use.
    assignments=list(WorkResourceAssignment.objects.filter(company=company).select_related('version','schedule'))
    for row in assignments:
        if any(m.get('position_id')==source_position.pk for m in row.crew_snapshot):remap_crew(row.crew_snapshot,alias)
    people=list(Employee.objects.filter(company=company,position=source_position).values_list('pk',flat=True))
    if people:
        audit.append({'model':'resources.employee','ids':people,'position_before':source_position.pk,'position_after':target_position.pk})
        Employee.objects.filter(company=company,pk__in=people).update(position=target_position)
    for crew in EquipmentCrewPosition.objects.select_for_update().filter(company=company,position=source_position):
        audit.append({'model':'resources.equipmentcrewposition','source':model_to_dict(crew)})
        existing=EquipmentCrewPosition.objects.select_for_update().filter(company=company,equipment_type=crew.equipment_type,position=target_position).first()
        if existing:
            if existing.is_replacement!=crew.is_replacement:raise ValidationError('У должностей в одном экипаже разные метки замещения.')
            audit[-1]['target_before']=model_to_dict(existing)
            existing.count+=crew.count;existing.save(update_fields=['count','updated_at']);crew.delete()
        else:crew.position=target_position;crew.save(update_fields=['position','updated_at'])
    schedules={}
    for row in assignments:
        owner=row.version if row.version_id else row.schedule
        if owner.status not in ['DRAFT','REJECTED']:continue
        changed=row.position_id==source_position.pk or any(m.get('position_id')==source_position.pk for m in row.crew_snapshot)
        if not changed:continue
        audit.append({'model':'planning.workresourceassignment','source':model_to_dict(row)})
        if row.position_id==source_position.pk:row.position=target_position
        row.crew_snapshot=remap_crew(row.crew_snapshot,alias)
        row.save(update_fields=['position','crew_snapshot','updated_at'])
        if row.schedule_id:schedules[row.schedule_id]=row.schedule
    for schedule in schedules.values():sync_schedule(schedule)
