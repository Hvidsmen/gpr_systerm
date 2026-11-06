"""Daily production crews managed inside the production fact workflow."""
import json
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Max
from django.http import JsonResponse
from django.views.decorators.http import require_http_methods
from core.fact_edit_window import require_fact_day
from core.permissions import require_roles
from .forms import ObjectDateForm
from .dispatch_models import (EmployeeActivity, EmployeeWorkLink, ProductionDailyCrew,
    ProductionDailyCrewMember)
from .models import DailyFact
from .crew_links import attach_member,detach_member,delete_crew_assignments,promote_to_crew


EDIT_ROLES={'ADMIN','DISPATCHER','ALL_FACT_DISPATCHER'}


def _scope(request,data):
    require_roles(request.user,EDIT_ROLES)
    selector=ObjectDateForm(data,user=request.user)
    if not selector.is_valid():raise ValidationError('Выберите доступный объект и корректную дату.')
    return selector.cleaned_data['construction_object'],selector.cleaned_data['date']


def _link_facts(obj,day,activities):
    facts=list(DailyFact.objects.filter(company=obj.company,date=day,project_work__section__construction_object=obj,actual_quantity__gt=0).select_related('project_work','work_item'))
    by_key={(fact.project_work_id,fact.work_item_id):fact for fact in facts if not fact.operational_key}
    by_activity={row.pk:set() for row in activities}
    for link in EmployeeWorkLink.objects.filter(company=obj.company,activity__in=activities).select_related('fact'):
        fact=link.fact if link.fact_id else by_key.get((link.work_id,link.work_item_id))
        if fact:by_activity.setdefault(link.activity_id,set()).add(fact.pk)
    return facts,by_activity


def _recommendations(obj,day,people,memberships):
    free=[row for row in people if row.pk not in memberships and not row.general]
    facts,by_activity=_link_facts(obj,day,free);fact_map={fact.pk:fact for fact in facts};groups={}
    for shift in {row.shift for row in free}:
        shift_people=[row for row in free if row.shift==shift]
        memberships_by_fact={}
        for row in shift_people:
            for fact_id in by_activity.get(row.pk,()):memberships_by_fact.setdefault(fact_id,set()).add(row.pk)
        for fact_id,ids in memberships_by_fact.items():
            if len(ids)>=2:groups.setdefault((shift,tuple(sorted(ids))),[]).append(fact_id)
    names={row.pk:str(row.employee) for row in free};result=[]
    for (shift,ids),fact_ids in sorted(groups.items(),key=lambda item:(-len(item[0][1]),-len(item[1]),item[0]))[:20]:
        labels=[]
        for fact_id in sorted(fact_ids):
            fact=fact_map[fact_id];labels.append(fact.operational_name or str(fact.work_item or fact.project_work))
        result.append({'shift':shift,'activities':list(ids),'facts':sorted(fact_ids),'people':[names[pk] for pk in ids],'works':labels})
    return result


def _state(obj,day,message=''):
    people=list(EmployeeActivity.objects.filter(company=obj.company,construction_object=obj,date=day,entry_direction='works').select_related('employee','position').order_by('shift','employee__last_name','employee__first_name','pk'))
    memberships={row.activity_id:row.crew_id for row in ProductionDailyCrewMember.objects.filter(company=obj.company,activity__in=people)}
    crews=[]
    for crew in ProductionDailyCrew.objects.filter(company=obj.company,construction_object=obj,date=day,is_active=True).prefetch_related('members','work_assignments').order_by('shift','number','pk'):
        crews.append({'id':crew.pk,'number':crew.number,'shift':crew.shift,'name':crew.name,'members':[row.activity_id for row in crew.members.all()],'facts':[row.fact_id for row in crew.work_assignments.all()]})
    return {'crews':crews,'people':[{'id':row.pk,'name':str(row.employee),'position':str(row.position or row.employee.position or ''),'shift':row.shift,'crew':memberships.get(row.pk)} for row in people],'recommendations':_recommendations(obj,day,people,memberships),'message':message}


@login_required
@require_http_methods(['GET','POST'])
def crews(request):
    try:
        data=request.GET if request.method=='GET' else json.loads(request.body or '{}')
        obj,day=_scope(request,data)
        if request.method=='GET':return JsonResponse(_state(obj,day))
        require_fact_day(request.user,day)
        action=data.get('action')
        with transaction.atomic():
            if action=='create':
                shift=int(data.get('shift',1));name=str(data.get('name','')).strip()[:160]
                if shift not in {1,2}:raise ValidationError('Выберите дневную или ночную смену.')
                # Inactive rows remain as number tombstones, so an automatic number is never reused.
                number=(ProductionDailyCrew.objects.select_for_update().filter(company=obj.company,construction_object=obj,date=day,shift=shift).aggregate(value=Max('number'))['value'] or 0)+1
                crew=ProductionDailyCrew(company=obj.company,construction_object=obj,date=day,shift=shift,number=number,name=name,reported_by=request.user)
                crew.full_clean();crew.save();message=f'Создана бригада №{number}.'
            elif action=='create_recommended':
                activity_ids=data.get('activities',[])
                if not isinstance(activity_ids,list) or not 2<=len(activity_ids)<=2000:raise ValidationError('В рекомендации должно быть не менее двух сотрудников.')
                activities=list(EmployeeActivity.objects.select_for_update().filter(pk__in=activity_ids,company=obj.company,construction_object=obj,date=day,entry_direction='works'))
                if len(activities)!=len({int(pk) for pk in activity_ids}) or len({row.shift for row in activities})!=1:raise ValidationError('Состав рекомендации уже изменился. Обновите данные.')
                if ProductionDailyCrewMember.objects.filter(company=obj.company,activity__in=activities).exists():raise ValidationError('Часть сотрудников уже вошла в бригаду. Обновите рекомендации.')
                facts,by_activity=_link_facts(obj,day,activities);common=set.intersection(*(by_activity.get(row.pk,set()) for row in activities))
                if not common:raise ValidationError('У выбранных сотрудников больше нет общих связей.')
                shift=activities[0].shift;number=(ProductionDailyCrew.objects.select_for_update().filter(company=obj.company,construction_object=obj,date=day,shift=shift).aggregate(value=Max('number'))['value'] or 0)+1
                crew=ProductionDailyCrew.objects.create(company=obj.company,construction_object=obj,date=day,shift=shift,number=number,reported_by=request.user)
                fact_map={fact.pk:fact for fact in facts}
                for activity in activities:
                    ProductionDailyCrewMember.objects.create(company=obj.company,crew=crew,activity=activity)
                    for fact_id in common:promote_to_crew(activity,crew,fact_map[fact_id])
                message=f'Создана бригада №{number}: сотрудников {len(activities)}, общих работ {len(common)}.'
            elif action=='rename':
                crew=ProductionDailyCrew.objects.select_for_update().get(pk=data.get('crew'),company=obj.company,construction_object=obj,date=day,is_active=True)
                crew.name=str(data.get('name','')).strip()[:160];crew.full_clean();crew.save(update_fields=['name']);message='Название сохранено.'
            elif action=='delete':
                crew=ProductionDailyCrew.objects.select_for_update().get(pk=data.get('crew'),company=obj.company,construction_object=obj,date=day,is_active=True)
                delete_crew_assignments(crew);crew.members.all().delete();crew.is_active=False;crew.save(update_fields=['is_active']);message=f'Бригада №{crew.number} удалена. Бригадные связи сняты, сотрудники перемещены в список «Без бригады».'
            elif action=='move':
                activity=EmployeeActivity.objects.select_for_update().get(pk=data.get('activity'),company=obj.company,construction_object=obj,date=day,entry_direction='works')
                crew_id=data.get('crew')
                previous=ProductionDailyCrewMember.objects.filter(company=obj.company,activity=activity).select_related('crew').first()
                if crew_id in (None,''):
                    if previous:detach_member(previous.crew,activity);previous.delete()
                    message='Сотрудник перемещён в список «Без бригады». Бригадные связи сняты.'
                else:
                    crew=ProductionDailyCrew.objects.get(pk=crew_id,company=obj.company,construction_object=obj,date=day,shift=activity.shift,is_active=True)
                    if previous and previous.crew_id!=crew.pk:detach_member(previous.crew,activity)
                    membership,_=ProductionDailyCrewMember.objects.update_or_create(activity=activity,defaults={'company':obj.company,'crew':crew})
                    membership.full_clean();attach_member(crew,activity);message=f'Сотрудник перемещён в бригаду №{crew.number}. Бригадные связи применены.'
            elif action=='bulk_move':
                activity_ids=data.get('activities',[])
                if not isinstance(activity_ids,list) or not activity_ids or len(activity_ids)>2000:raise ValueError
                activities=list(EmployeeActivity.objects.select_for_update().filter(pk__in=activity_ids,company=obj.company,construction_object=obj,date=day,entry_direction='works'))
                if len(activities)!=len({int(pk) for pk in activity_ids}):raise ValidationError('Часть сотрудников уже удалена из факта. Обновите список.')
                crew_id=data.get('crew')
                if crew_id in (None,''):
                    for membership in ProductionDailyCrewMember.objects.filter(company=obj.company,activity__in=activities).select_related('crew','activity'):
                        detach_member(membership.crew,membership.activity)
                    ProductionDailyCrewMember.objects.filter(company=obj.company,activity__in=activities).delete()
                    message=f'Из бригад удалено сотрудников: {len(activities)}.'
                else:
                    crew=ProductionDailyCrew.objects.get(pk=crew_id,company=obj.company,construction_object=obj,date=day,is_active=True)
                    if any(activity.shift!=crew.shift for activity in activities):raise ValidationError('В бригаду можно добавить сотрудников только той же смены.')
                    for activity in activities:
                        previous=ProductionDailyCrewMember.objects.filter(company=obj.company,activity=activity).select_related('crew').first()
                        if previous and previous.crew_id!=crew.pk:detach_member(previous.crew,activity)
                        ProductionDailyCrewMember.objects.update_or_create(activity=activity,defaults={'company':obj.company,'crew':crew})
                        attach_member(crew,activity)
                    message=f'В бригаду №{crew.number} добавлено сотрудников: {len(activities)}.'
            elif action=='copy_previous':
                if ProductionDailyCrew.objects.filter(company=obj.company,construction_object=obj,date=day,is_active=True).exists():
                    return JsonResponse(_state(obj,day,'На выбранный день бригады уже созданы. Удалите их перед копированием, если хотите заменить состав.'))
                previous=ProductionDailyCrew.objects.filter(company=obj.company,construction_object=obj,date__lt=day,is_active=True).order_by('-date').values_list('date',flat=True).first()
                if not previous:return JsonResponse(_state(obj,day,'Ранее созданных бригад нет.'))
                current={(row.employee_id,row.shift):row for row in EmployeeActivity.objects.select_for_update().filter(company=obj.company,construction_object=obj,date=day,entry_direction='works')}
                copied=skipped=0
                for source in ProductionDailyCrew.objects.filter(company=obj.company,construction_object=obj,date=previous,is_active=True).prefetch_related('members__activity'):
                    number=(ProductionDailyCrew.objects.filter(company=obj.company,construction_object=obj,date=day,shift=source.shift).aggregate(value=Max('number'))['value'] or 0)+1
                    target=ProductionDailyCrew.objects.create(company=obj.company,construction_object=obj,date=day,shift=source.shift,number=number,name=source.name,reported_by=request.user)
                    for member in source.members.all():
                        activity=current.get((member.activity.employee_id,member.activity.shift))
                        if activity:
                            ProductionDailyCrewMember.objects.update_or_create(activity=activity,defaults={'company':obj.company,'crew':target});copied+=1
                        else:skipped+=1
                message=f'Бригады скопированы с {previous:%d.%m.%Y}: сотрудников {copied}.'+(f' Не перенесены отсутствующие на шаге «Люди»: {skipped}.' if skipped else '')
            else:raise ValueError
            if action in {'create_recommended','delete','move','bulk_move'}:
                from .collaboration import automatic_readiness
                automatic_readiness(obj,day,request.user)
        return JsonResponse(_state(obj,day,message))
    except ProductionDailyCrew.DoesNotExist:return JsonResponse({'error':'Бригада уже изменена или удалена. Обновите данные.'},status=409)
    except EmployeeActivity.DoesNotExist:return JsonResponse({'error':'Сотрудник уже удалён из факта. Обновите данные.'},status=409)
    except (ValueError,TypeError,json.JSONDecodeError):return JsonResponse({'error':'Некорректная операция с бригадой.'},status=400)
    except ValidationError as exc:return JsonResponse({'error':'; '.join(exc.messages)},status=400)
