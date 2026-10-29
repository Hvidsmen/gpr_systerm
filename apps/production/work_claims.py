"""Short reservations for unsaved daily work rows, scoped to object, day and browser tab."""
from datetime import timedelta
import json
from uuid import UUID
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import F
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.views.decorators.http import require_http_methods
from core.permissions import require_roles, role_code
from apps.works.models import ProjectWork, ProjectWorkItem
from .dispatch_models import WorkFactClaim, EmployeeFactClaim, EmployeeActivity
from apps.resources.models import Employee
from .models import DailyFact
from .forms import ObjectDateForm
from .entry_concurrency import sqlite_serialized

TTL=timedelta(minutes=5)


def work_key(work,item=None):
    return f'item:{work}:{item}' if item else f'work:{work}'


def owner_name(user):
    return getattr(user,'full_name','') or user.get_full_name() or user.username


def public_claim(row,user,session):
    return {'key':f'employee:{row.employee_id}:{row.shift}' if isinstance(row,EmployeeFactClaim) else work_key(row.work_id,row.work_item_id),'state':'editing','owner':owner_name(row.dispatcher),
        'own':row.dispatcher_id==user.pk,'mine':row.dispatcher_id==user.pk and row.session_key==session,
        'expires_at':row.expires_at.isoformat()}


def can_edit_person(user, activity):
    role=role_code(user)
    if role in {'ADMIN','ALL_FACT_DISPATCHER'}:return True
    from .employee_pool import activity_direction
    direction=activity_direction(activity)
    return role=={'works':'DISPATCHER','labor':'PERSONNEL_DISPATCHER'}[direction]


def status_rows(user,obj,day,session):
    now=timezone.now()
    values={work_key(row.work_id,row.work_item_id):public_claim(row,user,session) for row in
        WorkFactClaim.objects.filter(company=obj.company,construction_object=obj,date=day,expires_at__gt=now).select_related('dispatcher')}
    for fact in DailyFact.objects.filter(company=obj.company,project_work__section__construction_object=obj,date=day).select_related('reported_by'):
        key=work_key(fact.project_work_id,fact.work_item_id)
        if key not in values:
                        values[key]={'key':key,'state':'saved','owner':owner_name(fact.reported_by) if fact.reported_by_id else 'Общий ввод',
                'own':True,'mine':False}
    for row in EmployeeFactClaim.objects.filter(company=obj.company,date=day,expires_at__gt=now).select_related('dispatcher'):
        values[f'employee:{row.employee_id}:{row.shift}']=public_claim(row,user,session)
    for row in EmployeeActivity.objects.filter(company=obj.company,date=day).select_related('reported_by'):
        key=f'employee:{row.employee_id}:{row.shift}'
        if key not in values:
            own=row.construction_object_id==obj.pk and can_edit_person(user,row)
            values[key]={'key':key,'state':'saved','owner':owner_name(row.reported_by) if row.reported_by_id else 'Общий ввод','own':own,'mine':False,'other_object':row.construction_object_id!=obj.pk}
    return list(values.values())


def release_session(user,obj,day,session):
    try:session=UUID(str(session))
    except (ValueError,TypeError):return
    for model in (WorkFactClaim,EmployeeFactClaim):
        model.objects.filter(company=obj.company,construction_object=obj,date=day,dispatcher=user,session_key=session).delete()


def check_form_claims(user,obj,day,groups):
    now=timezone.now()
    active={(claim.work_id,claim.work_item_id):claim for claim in WorkFactClaim.objects.filter(
        company=obj.company,construction_object=obj,date=day,expires_at__gt=now).select_related('dispatcher')}
    for group in groups:
        if group['prefix']=='labor':
            for form in group['formset']:
                if not form.cleaned_data or not form.has_changed():continue
                employee=form.cleaned_data.get('employee');shift=form.cleaned_data.get('shift')
                if not employee:continue
                if form.instance.pk:
                    saved_person=EmployeeActivity.objects.select_related('employee__position__planning_mapping__brigade__group','reported_by__role').get(pk=form.instance.pk,company=obj.company)
                    if not can_edit_person(user,saved_person):raise ValidationError('Это запись другого направления. Она доступна только для просмотра.')
                # Lock employee before inspecting a reservation, including across objects.
                employee=Employee.objects.select_for_update().get(pk=employee.pk,company=obj.company)
                retained=form.instance.pk and EmployeeActivity.objects.filter(pk=form.instance.pk,employee=employee,date=day,construction_object=obj,shift=shift).exists()
                if not employee.is_active and not retained and not form.cleaned_data.get('DELETE'):raise ValidationError('Для нового ввода выберите активного сотрудника.')
                claim=EmployeeFactClaim.objects.filter(company=obj.company,date=day,employee=employee,shift=shift,expires_at__gt=now).select_related('dispatcher').first()
                supplied=form.cleaned_data.get('person_claim_token','')
                if claim and (claim.dispatcher_id!=user.pk or str(claim.session_key)!=supplied or claim.construction_object_id!=obj.pk):
                    raise ValidationError(f'Сотрудника сейчас заполняет {owner_name(claim.dispatcher)}. Его данные не изменены.')
                if form.instance.pk:
                    original_employee=int(form.initial.get('employee') or employee.pk);original_shift=int(form.initial.get('shift') or shift)
                    original=EmployeeFactClaim.objects.filter(company=obj.company,date=day,employee_id=original_employee,shift=original_shift,expires_at__gt=now).select_related('dispatcher').first()
                    if original and (original.dispatcher_id!=user.pk or str(original.session_key)!=supplied):raise ValidationError(f'Исходного сотрудника сейчас заполняет {owner_name(original.dispatcher)}.')
                if supplied and not claim:raise ValidationError('Резерв сотрудника истёк. Возьмите сотрудника снова; введённые значения остались в форме.')
            continue
        if group['prefix']!='works':continue
        for form in group['formset']:
            if not form.cleaned_data or not form.has_changed():continue
            work=form.cleaned_data.get('project_work');item=form.cleaned_data.get('work_item')
            if not work:continue
            claim=active.get((work.pk,item.pk if item else None))
            supplied=form.cleaned_data.get('work_claim_token','')
            if claim and (claim.dispatcher_id!=user.pk or str(claim.session_key)!=supplied):
                raise ValidationError(f'Работу сейчас заполняет {owner_name(claim.dispatcher)}. Ваши значения не сохранены поверх чужих данных.')
            if form.instance.pk:
                original=(int(form.initial.get('project_work') or work.pk),int(form.initial['work_item']) if form.initial.get('work_item') else None)
                original_claim=active.get(original)
                if original_claim and (original_claim.dispatcher_id!=user.pk or str(original_claim.session_key)!=supplied):raise ValidationError(f'Исходную работу сейчас заполняет {owner_name(original_claim.dispatcher)}.')
            if supplied and not claim:
                raise ValidationError('Резерв работы истёк. Возьмите работу снова; введённые значения остались в форме.')


@login_required
@require_http_methods(['GET','POST'])
@sqlite_serialized
def claims(request):
    require_roles(request.user,{'ADMIN','DISPATCHER','PERSONNEL_DISPATCHER','ALL_FACT_DISPATCHER'})
    try:
        data=json.loads(request.body) if request.method=='POST' and request.content_type=='application/json' else request.POST if request.method=='POST' else request.GET
        if not hasattr(data,'get'):raise ValueError
        selector=ObjectDateForm(data,user=request.user)
        if not selector.is_valid():return JsonResponse({'error':'Выберите доступный объект и корректную дату.'},status=400)
        obj=selector.cleaned_data['construction_object'];day=selector.cleaned_data['date']
        session=UUID(str(data.get('session','')))
        if request.method=='GET':
            from .production_entry import report_data
            return JsonResponse({'claims':status_rows(request.user,obj,day,session),'report':report_data(obj,day)})
        from .collaboration import expire_presence
        expire_presence(obj,day)
        action=data.get('action')
        if action not in {'release','release_session'}:
            from .day_reset import epoch
            if int(data.get('reset_epoch',0))!=epoch(obj,day):return JsonResponse({'error':'Производственная часть сброшена администратором. Обновите форму.','reset_required':True},status=409)
            from core.fact_edit_window import require_fact_day
            try:require_fact_day(request.user,day)
            except ValidationError as exc:return JsonResponse({'error':'; '.join(exc.messages)},status=403)
        if action=='release_session':
            release_session(request.user,obj,day,session)
            return JsonResponse({'released':True})
        parts=str(data.get('key','')).split(':')
        if parts[0]=='employee':return employee_claim(request.user,obj,day,session,action,parts)
        require_roles(request.user,{'ADMIN','DISPATCHER','ALL_FACT_DISPATCHER'})
        if parts[0]=='work' and len(parts)==2:work_id=int(parts[1]);item_id=None
        elif parts[0]=='item' and len(parts)==3:work_id=int(parts[1]);item_id=int(parts[2])
        else:raise ValueError
        work=get_object_or_404(ProjectWork,company=obj.company,section__construction_object=obj,pk=work_id,merged_source__isnull=True)
        if work.kind=='COMPOSITE':get_object_or_404(ProjectWorkItem,company=obj.company,project_work=work,pk=item_id)
        elif item_id:raise ValueError
        if action not in {'take','renew','release'}:raise ValueError
        with transaction.atomic():
            type(obj).objects.filter(pk=obj.pk,company=obj.company).update(name=F('name'))
            row=WorkFactClaim.objects.select_for_update().filter(company=obj.company,construction_object=obj,date=day,work=work,work_item_id=item_id).first()
            if action!='release':
                from core.fact_edit_window import require_fact_day
                try:require_fact_day(request.user,day)
                except ValidationError as exc:return JsonResponse({'error':'; '.join(exc.messages)},status=403)
            now=timezone.now()
            mine=bool(row and row.dispatcher_id==request.user.pk and row.session_key==session)
            if action=='release':
                if mine:row.delete()
                return JsonResponse({'released':mine})
            if row and row.expires_at>now and not mine:
                return JsonResponse({'error':f'Работу уже взял {owner_name(row.dispatcher)}.','claim':public_claim(row,request.user,session)},status=409)
            fact=DailyFact.objects.filter(company=obj.company,project_work=work,work_item_id=item_id,date=day).select_related('reported_by').first()
            if action=='renew' and (not mine or row.expires_at<=now):
                return JsonResponse({'error':'Резерв истёк. Нажмите «Взять в работу» снова.'},status=409)
            if row is None:row=WorkFactClaim(company=obj.company,construction_object=obj,date=day,work=work,work_item_id=item_id)
            row.dispatcher=request.user;row.session_key=session;row.expires_at=now+TTL;row.save()
            return JsonResponse({'claim':public_claim(row,request.user,session)})
    except (ValueError,TypeError,UnicodeDecodeError):
        return JsonResponse({'error':'Некорректный запрос резерва работы.'},status=400)


def employee_claim(user,obj,day,session,action,parts):
    if len(parts)!=3 or action not in {'take','renew','release'}:raise ValueError
    employee_id,shift=int(parts[1]),int(parts[2])
    if not 1<=shift<=24:raise ValueError
    with transaction.atomic():
        type(obj).objects.filter(pk=obj.pk,company=obj.company).update(name=F('name'))
        employee=get_object_or_404(Employee.objects.select_for_update(),company=obj.company,pk=employee_id)
        row=EmployeeFactClaim.objects.select_for_update().filter(company=obj.company,date=day,employee=employee,shift=shift).select_related('dispatcher').first()
        now=timezone.now();mine=bool(row and row.dispatcher_id==user.pk and row.session_key==session and row.construction_object_id==obj.pk)
        if action=='release':
            if mine:row.delete()
            return JsonResponse({'released':mine})
        if row and row.expires_at>now and not mine:
            return JsonResponse({'error':f'Сотрудника уже взял {owner_name(row.dispatcher)}.','claim':public_claim(row,user,session)},status=409)
        from core.fact_edit_window import require_fact_day
        try:require_fact_day(user,day)
        except ValidationError as exc:return JsonResponse({'error':'; '.join(exc.messages)},status=403)
        saved=EmployeeActivity.objects.filter(company=obj.company,date=day,employee=employee,shift=shift).first()
        if saved and (saved.construction_object_id!=obj.pk or not can_edit_person(user,saved)):
            return JsonResponse({'error':'Этот сотрудник за эту смену уже учтён другим диспетчером или на другом объекте.'},status=409)
        if not employee.is_active and not saved:return JsonResponse({'error':'Сотрудник неактивен.'},status=400)
        if action=='renew' and (not mine or row.expires_at<=now):return JsonResponse({'error':'Резерв сотрудника истёк. Возьмите его снова.'},status=409)
        if row is None:row=EmployeeFactClaim(company=obj.company,date=day,employee=employee,shift=shift)
        row.construction_object=obj;row.dispatcher=user;row.session_key=session;row.expires_at=now+TTL;row.save()
        return JsonResponse({'claim':public_claim(row,user,session)})
