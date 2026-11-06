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
from .dispatch_models import EmployeeActivity, ProductionDailyCrew, ProductionDailyCrewMember


EDIT_ROLES={'ADMIN','DISPATCHER','ALL_FACT_DISPATCHER'}


def _scope(request,data):
    require_roles(request.user,EDIT_ROLES)
    selector=ObjectDateForm(data,user=request.user)
    if not selector.is_valid():raise ValidationError('Выберите доступный объект и корректную дату.')
    return selector.cleaned_data['construction_object'],selector.cleaned_data['date']


def _state(obj,day,message=''):
    people=list(EmployeeActivity.objects.filter(company=obj.company,construction_object=obj,date=day,entry_direction='works').select_related('employee','position').order_by('shift','employee__last_name','employee__first_name','pk'))
    memberships={row.activity_id:row.crew_id for row in ProductionDailyCrewMember.objects.filter(company=obj.company,activity__in=people)}
    crews=[]
    for crew in ProductionDailyCrew.objects.filter(company=obj.company,construction_object=obj,date=day,is_active=True).prefetch_related('members').order_by('shift','number','pk'):
        crews.append({'id':crew.pk,'number':crew.number,'shift':crew.shift,'name':crew.name,'members':[row.activity_id for row in crew.members.all()]})
    return {'crews':crews,'people':[{'id':row.pk,'name':str(row.employee),'position':str(row.position or row.employee.position or ''),'shift':row.shift,'crew':memberships.get(row.pk)} for row in people],'message':message}


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
            elif action=='rename':
                crew=ProductionDailyCrew.objects.select_for_update().get(pk=data.get('crew'),company=obj.company,construction_object=obj,date=day,is_active=True)
                crew.name=str(data.get('name','')).strip()[:160];crew.full_clean();crew.save(update_fields=['name']);message='Название сохранено.'
            elif action=='delete':
                crew=ProductionDailyCrew.objects.select_for_update().get(pk=data.get('crew'),company=obj.company,construction_object=obj,date=day,is_active=True)
                crew.members.all().delete();crew.is_active=False;crew.save(update_fields=['is_active']);message=f'Бригада №{crew.number} удалена. Сотрудники перемещены в список «Без бригады».'
            elif action=='move':
                activity=EmployeeActivity.objects.select_for_update().get(pk=data.get('activity'),company=obj.company,construction_object=obj,date=day,entry_direction='works')
                crew_id=data.get('crew')
                if crew_id in (None,''):
                    ProductionDailyCrewMember.objects.filter(company=obj.company,activity=activity).delete();message='Сотрудник перемещён в список «Без бригады».'
                else:
                    crew=ProductionDailyCrew.objects.get(pk=crew_id,company=obj.company,construction_object=obj,date=day,shift=activity.shift,is_active=True)
                    membership,_=ProductionDailyCrewMember.objects.update_or_create(activity=activity,defaults={'company':obj.company,'crew':crew})
                    membership.full_clean();message=f'Сотрудник перемещён в бригаду №{crew.number}.'
            elif action=='bulk_move':
                activity_ids=data.get('activities',[])
                if not isinstance(activity_ids,list) or not activity_ids or len(activity_ids)>2000:raise ValueError
                activities=list(EmployeeActivity.objects.select_for_update().filter(pk__in=activity_ids,company=obj.company,construction_object=obj,date=day,entry_direction='works'))
                if len(activities)!=len({int(pk) for pk in activity_ids}):raise ValidationError('Часть сотрудников уже удалена из факта. Обновите список.')
                crew_id=data.get('crew')
                if crew_id in (None,''):
                    ProductionDailyCrewMember.objects.filter(company=obj.company,activity__in=activities).delete()
                    message=f'Из бригад удалено сотрудников: {len(activities)}.'
                else:
                    crew=ProductionDailyCrew.objects.get(pk=crew_id,company=obj.company,construction_object=obj,date=day,is_active=True)
                    if any(activity.shift!=crew.shift for activity in activities):raise ValidationError('В бригаду можно добавить сотрудников только той же смены.')
                    for activity in activities:
                        ProductionDailyCrewMember.objects.update_or_create(activity=activity,defaults={'company':obj.company,'crew':crew})
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
        return JsonResponse(_state(obj,day,message))
    except ProductionDailyCrew.DoesNotExist:return JsonResponse({'error':'Бригада уже изменена или удалена. Обновите данные.'},status=409)
    except EmployeeActivity.DoesNotExist:return JsonResponse({'error':'Сотрудник уже удалён из факта. Обновите данные.'},status=409)
    except (ValueError,TypeError,json.JSONDecodeError):return JsonResponse({'error':'Некорректная операция с бригадой.'},status=400)
    except ValidationError as exc:return JsonResponse({'error':'; '.join(exc.messages)},status=400)
