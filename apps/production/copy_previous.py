"""Explicit, additive day-template copying; volumes and technical state never copy."""
import json
from datetime import date,timedelta
from uuid import UUID
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db import transaction,IntegrityError
from django.db.models import F
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.views.decorators.http import require_POST
from core.fact_edit_window import require_fact_day
from core.fact_history import history_actor
from core.permissions import require_roles
from .entry_concurrency import sqlite_serialized


def result(day):return {'source_date':str(day-timedelta(days=1)),'added':0,'skipped':0,'warnings':[]}
def warn(output,message):
    output['skipped']+=1
    if len(output['warnings'])<50:output['warnings'].append(message)


def production_copy(user,obj,day,kind,*,session,reset_epoch,keep=(),revisions=None,replace_legacy=False):
    from .dispatch_models import EmployeeActivity,EmployeeWorkLink,EmployeeFactClaim,FactSectionDay,ProductionEntryDay
    from .employee_pool import labor_direction
    from .production_entry import targets,work_catalogue
    from .production_positions import plan_positions
    from .collaboration import apply_relations,automatic_readiness
    from .day_reset import epoch
    require_fact_day(user,day)
    if kind not in {'people','links'}:raise ValidationError('Выберите людей или связи.')
    if int(reset_epoch)!=epoch(obj,day):raise ValidationError('Производственная часть была сброшена. Обновите форму.')
    output=result(day);source=EmployeeActivity.objects.filter(company=obj.company,construction_object=obj,date=day-timedelta(days=1)).exclude(labor_direction()).select_related('employee__position').prefetch_related('work_links').order_by('pk')
    source_count=source.count()
    if source_count>2000:raise ValidationError('В предыдущем дне более 2000 сотрудников. Уточните состав.')
    if kind=='people':
        from .models import LaborFact
        labor_state=FactSectionDay.objects.filter(company=obj.company,construction_object=obj,date=day,section='labor').first()
        if source_count and (not labor_state or labor_state.revision==0) and not EmployeeActivity.objects.filter(company=obj.company,construction_object=obj,date=day).exists() and LaborFact.objects.filter(company=obj.company,construction_object=obj,date=day).exists() and not replace_legacy:
            raise ValidationError('На эту дату уже есть сводный факт персонала. Подтвердите его замену детализацией в форме.')
        roles=set(plan_positions(obj,day));protected=set(keep)
        for old in source:
            employee=old.employee;key=f'employee:{employee.pk}:{old.shift}'
            if not employee.is_active or employee.company_id!=obj.company_id:warn(output,f'{employee.display_name}: сотрудник неактивен.');continue
            if key in protected or EmployeeActivity.objects.filter(company=obj.company,employee=employee,date=day,shift=old.shift).exists():output['skipped']+=1;continue
            if EmployeeFactClaim.objects.filter(company=obj.company,date=day,employee=employee,shift=old.shift,expires_at__gt=timezone.now()).exists():warn(output,f'{employee.display_name}: запись сейчас редактируется.');continue
            try:
                with transaction.atomic():
                    row=EmployeeActivity(company=obj.company,construction_object=obj,date=day,employee=employee,position=employee.position,planning_position_id=old.planning_position_id if old.planning_position_id in roles else None,shift=old.shift,activity=old.activity,entry_direction='works',assignment_reviewed=False,reported_by=user)
                    row.full_clean();row.save();output['added']+=1
            except (ValidationError,IntegrityError) as exc:warn(output,f'{employee.display_name}: '+('; '.join(exc.messages) if isinstance(exc,ValidationError) else 'состав уже изменился.'))
        if output['added']:
            from .dispatch import project_resources
            project_resources(obj,day,'labor')
            for section in ['works','labor']:
                state,_=FactSectionDay.objects.get_or_create(company=obj.company,construction_object=obj,date=day,section=section)
                FactSectionDay.objects.filter(pk=state.pk).update(revision=F('revision')+1,confirmed_at=None,confirmed_by=None)
            entry,_=ProductionEntryDay.objects.get_or_create(company=obj.company,construction_object=obj,date=day,dispatcher=user)
            ProductionEntryDay.objects.filter(pk=entry.pk).update(revision=F('revision')+1,roster_started=True,confirmed_at=None)
    else:
        from .models import DailyFact
        allowed={(item['work'],item['item']) for item in work_catalogue(obj)}
        facts=set(DailyFact.objects.filter(company=obj.company,date=day,project_work__section__construction_object=obj).values_list('project_work_id','work_item_id'))
        current={(row.employee_id,row.shift):row for row in EmployeeActivity.objects.select_for_update().filter(company=obj.company,construction_object=obj,date=day).exclude(labor_direction()).prefetch_related('work_links')}
        revisions=revisions or {}
        for old in source:
            if not old.assignment_reviewed:warn(output,f'{old.employee.display_name}: вчерашние связи не проверены.');continue
            row=current.get((old.employee_id,old.shift))
            if not row:warn(output,f'{old.employee.display_name}: сначала добавьте сотрудника в текущий день.');continue
            if row.general or revisions.get(str(row.pk))!=row.links_revision:output['skipped']+=1;continue
            selected=targets(old);existing=set(targets(row));added=0
            if old.general and old.assignment_reviewed and not existing and not row.assignment_reviewed:
                row.general=True;row.assignment_reviewed=True;row.work=None;row.work_item=None;row.links_revision+=1
                from .collaboration import RELATION_FIELDS
                row.save(update_fields=RELATION_FIELDS);output['added']+=1;continue
            if not selected:output['skipped']+=1
            for key in selected:
                if key in existing:output['skipped']+=1;continue
                if key not in facts or key not in allowed:warn(output,f'{old.employee.display_name}: работа ещё не внесена в факт текущего дня или недоступна.');continue
                link=EmployeeWorkLink(company=obj.company,activity=row,work_id=key[0],work_item_id=key[1]);link.full_clean();link.save();existing.add(key);added+=1
            if added:apply_relations(row);output['added']+=added
    require_fact_day(user,day)
    if output['added']:automatic_readiness(obj,day,user)
    from .production_entry import report_data
    output['report']=report_data(obj,day)
    return output


@login_required
@require_POST
@sqlite_serialized
def production(request):
    from .collaboration import scope
    try:
        data,obj,day=scope(request);session=UUID(str(data.get('session','')))
        keep=data.get('keep',[]);revisions=data.get('revisions',{})
        if not isinstance(keep,list) or len(keep)>2000 or not isinstance(revisions,dict) or len(revisions)>2000:raise ValueError
        with transaction.atomic(),history_actor(request.user,'Копирование из предыдущего дня'):
            type(obj).objects.filter(pk=obj.pk,company=obj.company).update(name=F('name'))
            return JsonResponse(production_copy(request.user,obj,day,data.get('kind'),session=session,reset_epoch=data.get('reset_epoch',0),keep=keep,revisions=revisions,replace_legacy=data.get('replace_legacy') is True))
    except (ValueError,TypeError):return JsonResponse({'error':'Некорректный запрос копирования.'},status=400)
    except ValidationError as exc:return JsonResponse({'error':'; '.join(exc.messages)},status=409)


@login_required
@require_POST
@sqlite_serialized
def equipment(request):
    from . import transport
    from .transport_models import EquipmentDayRecord
    from .transport_details_models import EquipmentDayDetails
    from .transport_inline import save_cell
    from apps.resources.models import Employee
    require_roles(request.user,transport.OPERATORS)
    try:
        data=json.loads(request.body);day=date.fromisoformat(data.get('date',''));require_fact_day(request.user,day)
        camp=get_object_or_404(transport.camps_for(request.user),pk=data.get('camp'));session=UUID(str(data.get('session','')))
        signed=data.get('signed');transport._payload(request.user,camp,day,signed)
        output=result(day)
        source=EquipmentDayRecord.objects.filter(company=request.user.company,camp=camp,date=day-timedelta(days=1)).select_related('equipment','construction_object','work','work_item')
        if source.count()>2000:raise ValidationError('В предыдущем дне более 2000 машин. Уточните состав.')
        details={row.equipment_id:row for row in EquipmentDayDetails.objects.filter(company=request.user.company,camp=camp,date=day-timedelta(days=1))}
        active=set(Employee.objects.filter(company=request.user.company,is_active=True).values_list('pk',flat=True))
        for old in source:
            if EquipmentDayRecord.objects.filter(company=request.user.company,equipment=old.equipment,date=day).exists():output['skipped']+=1;continue
            try:
                with transaction.atomic(),history_actor(request.user,'Копирование техники из предыдущего дня'):
                    if old.construction_object_id:
                        from .models import EquipmentFact,EquipmentActivity,FactSectionDay
                        from apps.projects.models import ConstructionObject
                        transport._company_mutex(request.user)
                        ConstructionObject.objects.select_for_update().get(company=request.user.company,pk=old.construction_object_id)
                        state=FactSectionDay.objects.filter(company=request.user.company,construction_object_id=old.construction_object_id,date=day,section='mechanization').first()
                        if (not state or state.revision==0) and not EquipmentActivity.objects.filter(company=request.user.company,construction_object_id=old.construction_object_id,date=day).exists() and EquipmentFact.objects.filter(company=request.user.company,construction_object_id=old.construction_object_id,date=day).exists():
                            raise ValidationError('На объекте уже есть сводный факт техники. Сначала проверьте его замену детализацией.')
                    transport.save_day(request.user,camp,day,[old.equipment_id],old.status,construction_object=old.construction_object,work=old.work,work_item=old.work_item,comment=old.comment,signed=signed,session=session,planning_type=old.planning_type,allow_external=not old.presence_required)
                    prior=details.get(old.equipment_id)
                    current=EquipmentDayDetails.objects.filter(company=request.user.company,equipment_id=old.equipment_id,date=day).first()
                    for field in ['shift_one_employee','shift_two_employee']:
                        employee=getattr(prior,field+'_id',None)
                        if old.status!='WORKED' or not employee or current and getattr(current,field+'_id'):continue
                        if employee not in active:
                            if len(output['warnings'])<50:output['warnings'].append(f'{old.equipment}: водитель смены неактивен, выберите другого.')
                            continue
                        fresh=transport.snapshot(request.user,camp,day)
                        save_cell(request.user,camp,day,old.equipment,field,employee,fresh,str(session))
                    output['added']+=1
            except ValidationError as exc:warn(output,f'{old.equipment}: '+ '; '.join(exc.messages))
        require_fact_day(request.user,day)
        return JsonResponse(output)
    except (ValueError,TypeError,AttributeError):return JsonResponse({'error':'Выберите ВЖГ, дату и корректный сеанс.'},status=400)
    except ValidationError as exc:return JsonResponse({'error':'; '.join(exc.messages)},status=409)
