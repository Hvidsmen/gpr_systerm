"""Small, idempotent writes for the production day editor.

The legacy submit remains the confirmation path.  Autosave uses this endpoint so
its cost depends on the changed rows, not on the size of the whole day.
"""
import hashlib
import json
from uuid import UUID

from django.contrib.auth.decorators import login_required
from django import forms
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import F
from django.http import JsonResponse, QueryDict
from django.views.decorators.http import require_POST

from core.fact_edit_window import require_fact_day
from core.permissions import FACT_ROLES, require_roles, role_code
from .dispatch_models import EmployeeActivity, FactSectionDay, ProductionBatchReceipt, ProductionEntryDay
from .entry_concurrency import sqlite_serialized
from .forms import ObjectDateForm
from .models import DailyFact


MAX_ROWS=15


def _normalized_operational(value):
    return ' '.join(str(value or '').casefold().split())


def _error(message,status=400,errors=None):
    return JsonResponse({'error':message,'errors':errors or []},status=status)


def _query_data(values):
    data=QueryDict('',mutable=True)
    data['collaborative']='1'
    for name,value in values.items():
        key='row-'+name
        # Older long-lived browser tabs encoded an unchecked checkbox group as
        # an empty string.  Treat it as the empty selection so a harmless
        # quantity edit is not rejected by ModelMultipleChoiceField.
        if name=='actuation_reasons' and value in ('',None):data.setlist(key,[])
        elif isinstance(value,list):data.setlist(key,[str(item) for item in value])
        elif value is not None:data[key]=str(value)
    return data


def _form_error(client_prefix,form):
    return {'prefix':client_prefix,'fields':{name:list(messages) for name,messages in form.errors.items()}}


@login_required
@require_POST
@sqlite_serialized
def save_batch(request):
    require_roles(request.user,FACT_ROLES)
    try:data=json.loads(request.body)
    except (TypeError,ValueError):return _error('Некорректный пакет сохранения.')
    if not isinstance(data,dict):return _error('Некорректный пакет сохранения.')
    if data.get('actor_id') is not None and (data.get('actor_id')!=request.user.pk or data.get('company_id')!=request.user.company_id):
        return JsonResponse({'error':'Учётная запись изменилась. Откройте форму заново.','auth_changed':True},status=403)
    selector=ObjectDateForm(data,user=request.user)
    if not selector.is_valid():return _error('Выберите доступный объект и корректную дату.')
    obj=selector.cleaned_data['construction_object'];day=selector.cleaned_data['date']
    rows=data.get('rows')
    if not isinstance(rows,list) or len(rows)>MAX_ROWS:return _error(f'За один запрос можно сохранить не более {MAX_ROWS} строк.')
    # Ctrl+S may race with an autosave that has just completed. A zero-row
    # request is a harmless synchronization no-op, not a validation error.
    if not rows:return JsonResponse({'rows':[],'report_dirty':False,'noop':True})
    try:batch_id=UUID(str(data.get('batch_id')))
    except (TypeError,ValueError,AttributeError):return _error('Не указан идентификатор безопасного повтора.')
    canonical=json.dumps({'object':obj.pk,'date':day.isoformat(),'rows':rows},ensure_ascii=False,sort_keys=True,separators=(',',':'))
    digest=hashlib.sha256(canonical.encode()).hexdigest()
    prior=ProductionBatchReceipt.objects.filter(pk=batch_id,company=obj.company,user=request.user).first()
    if prior:
        if prior.digest!=digest:return _error('Идентификатор повтора уже использован для другого пакета.',409)
        return JsonResponse({**prior.result,'replayed':True})
    try:
        require_fact_day(request.user,day)
        from .edit_lock import require_available
        require_available(request.user,obj,day,data)
    except ValidationError as exc:return _error('; '.join(exc.messages),403)

    from .dispatch import WorkForm, plans
    from .production_entry import ProductionPersonForm, candidate_keys, work_catalogue, inherit_parent_works, sync_links
    from .employee_pool import prepare as prepare_employee_pool
    from .production_positions import plan_positions
    inherit_parent_works(obj,request.user)
    work_plan,_=plans(obj,day);catalogue=work_catalogue(obj)
    activity_keys=candidate_keys(obj,day,work_plan,catalogue,user=request.user if role_code(request.user)=='DISPATCHER' else None)
    employee_ids={int(item['values'].get('employee')) for item in rows if item.get('kind')=='labor' and str(item.get('values',{}).get('employee','')).isdigit()}
    pool=prepare_employee_pool(obj,day,employee_ids);planned_positions=plan_positions(obj,day)
    common={'user':request.user,'obj':obj,'day':day,'work_plan':work_plan,'activity_work_keys':activity_keys,
        'activity_choices':catalogue,'employee_pool':pool,'planned_positions':planned_positions}
    prepared=[];errors=[]
    try:
        with transaction.atomic():
            # Serialize a day write and make the receipt/result atomic with it.
            type(obj).objects.filter(pk=obj.pk,company=obj.company).update(name=F('name'))
            type(obj).objects.select_for_update().get(pk=obj.pk)
            if ProductionBatchReceipt.objects.select_for_update().filter(pk=batch_id).exists():
                prior=ProductionBatchReceipt.objects.get(pk=batch_id)
                if prior.company_id!=obj.company_id or prior.user_id!=request.user.pk or prior.digest!=digest:
                    raise ValidationError('Идентификатор повтора уже использован для другого пакета.')
                return JsonResponse({**prior.result,'replayed':True})
            for item in rows:
                if not isinstance(item,dict) or item.get('kind') not in {'works','labor'} or not isinstance(item.get('values'),dict):
                    raise ValidationError('В пакете есть некорректная строка.')
                kind=item['kind'];model=DailyFact if kind=='works' else EmployeeActivity
                instance=None
                if item.get('id'):
                    query=model.objects.select_for_update().filter(pk=item['id'],company=obj.company,date=day)
                    query=query.filter(project_work__section__construction_object=obj) if kind=='works' else query.filter(construction_object=obj)
                    instance=query.first()
                    if not instance:raise ValidationError('Сохранённая строка уже удалена или относится к другому объекту.')
                    expected=item.get('version')
                    if expected and instance.updated_at.isoformat()!=expected:
                        message='Строка изменилась после открытия формы. Проверьте восстановленный черновик и нажмите «Повторить сохранение», чтобы применить его к актуальной записи.'
                        return JsonResponse({'error':message,'errors':[{'prefix':item.get('prefix',''),'message':message,'version':instance.updated_at.isoformat()}]},status=409)
                elif kind=='works':
                    # Adding an already existing work means editing its daily
                    # fact, including when that fact is on another UI page.
                    # Never attempt a second INSERT and leak a database
                    # constraint name to the dispatcher.
                    values=item['values'];work_id=str(values.get('project_work') or '')
                    item_id=str(values.get('work_item') or '');operational=(values.get('operational_name') or '').strip()
                    if work_id.isdigit() and not operational:
                        existing=DailyFact.objects.select_for_update().filter(company=obj.company,date=day,project_work_id=int(work_id),project_work__section__construction_object=obj)
                        if item_id.isdigit():existing=existing.filter(work_item_id=int(item_id))
                        else:existing=existing.filter(work_item=None,operational_key=None)
                        instance=existing.first()
                        if instance:
                            message='Эта работа уже добавлена за выбранный день. Откройте существующую строку и измените объём в ней.'
                            return JsonResponse({'error':message,'errors':[{'prefix':item.get('prefix',''),'message':message,'code':'existing_work','existing_id':instance.pk}]},status=409)
                    elif work_id.isdigit() and operational and not item.get('allow_duplicate_operational'):
                        normalized_name=_normalized_operational(operational);normalized_unit=_normalized_operational(values.get('operational_unit'))
                        candidates=DailyFact.objects.select_for_update().filter(company=obj.company,date=day,project_work__section__construction_object=obj,operational_key__isnull=False)
                        duplicate=next((fact for fact in candidates if _normalized_operational(fact.operational_name)==normalized_name and _normalized_operational(fact.operational_unit)==normalized_unit),None)
                        if duplicate:
                            message='Прочая работа с таким названием и единицей уже добавлена. Откройте её или подтвердите создание отдельной записи.'
                            return JsonResponse({'error':message,'errors':[{'prefix':item.get('prefix',''),'message':message,'code':'operational_duplicate','existing_id':duplicate.pk}]},status=409)
                elif kind=='labor':
                    values=item['values'];employee_id=str(values.get('employee') or '');shift=str(values.get('shift') or '')
                    if employee_id.isdigit() and shift.isdigit():
                        duplicate=EmployeeActivity.objects.select_for_update().filter(company=obj.company,date=day,employee_id=int(employee_id),shift=int(shift)).select_related('construction_object').first()
                        if duplicate:
                            if duplicate.construction_object_id==obj.pk:
                                message='Этот сотрудник уже добавлен в выбранную смену. Откройте существующую строку.'
                                issue={'prefix':item.get('prefix',''),'message':message,'code':'existing_person','existing_id':duplicate.pk}
                            else:
                                message=f'Этот сотрудник уже указан в выбранную смену на объекте «{duplicate.construction_object}». Сначала исправьте запись на том объекте.'
                                issue={'prefix':item.get('prefix',''),'message':message,'code':'person_other_object','existing_id':duplicate.pk,
                                    'construction_object':duplicate.construction_object_id,'construction_object_name':str(duplicate.construction_object),'date':day.isoformat(),'shift':duplicate.shift}
                            return JsonResponse({'error':message,'errors':[issue]},status=409)
                form_class=WorkForm if kind=='works' else ProductionPersonForm
                form=form_class(_query_data(item['values']),instance=instance,prefix='row',**common)
                form.fields['DELETE']=forms.BooleanField(required=False)
                form.full_clean()
                if not form.is_valid():errors.append(_form_error(item.get('prefix',''),form))
                prepared.append((item,form))
            if errors:
                transaction.set_rollback(True)
                return _error('Проверьте заполнение выделенных строк.',400,errors)
            from .work_claims import check_form_claims
            check_form_claims(request.user,obj,day,[{'prefix':kind,'formset':[form for item,form in prepared if item['kind']==kind]} for kind in ('works','labor')])
            result_rows=[];people_changed=False;works_changed=False
            for item,form in prepared:
                kind=item['kind'];instance=form.instance;deleted=bool(form.cleaned_data.get('DELETE'))
                if deleted:
                    if not instance.pk:continue
                    if kind=='works' and (instance.project_work_id,instance.work_item_id) in work_plan:
                        raise ValidationError('Работу из плана СМГ удалить нельзя. Введите фактический объём, включая ноль.')
                    instance.delete();result_rows.append({'prefix':item.get('prefix',''),'id':None,'deleted':True,'kind':kind})
                else:
                    row=form.save(commit=False)
                    if not row.pk and not row.reported_by_id:row.reported_by=request.user
                    row.full_clean();row.save();sync_links(form)
                    result_rows.append({'prefix':item.get('prefix',''),'id':row.pk,'deleted':False,'kind':kind,'version':row.updated_at.isoformat()})
                works_changed|=kind=='works';people_changed|=kind=='labor'
            work_state,_=FactSectionDay.objects.get_or_create(company=obj.company,construction_object=obj,date=day,section='works')
            labor_state,_=FactSectionDay.objects.get_or_create(company=obj.company,construction_object=obj,date=day,section='labor')
            if works_changed:FactSectionDay.objects.filter(pk=work_state.pk).update(revision=F('revision')+1,confirmed_at=None,confirmed_by=None)
            if people_changed:FactSectionDay.objects.filter(pk=labor_state.pk).update(revision=F('revision')+1,confirmed_at=None,confirmed_by=None)
            entry,_=ProductionEntryDay.objects.get_or_create(company=obj.company,construction_object=obj,date=day,dispatcher=request.user)
            ProductionEntryDay.objects.filter(pk=entry.pk).update(roster_started=F('roster_started') if not people_changed else True,revision=F('revision')+1,confirmed_at=None)
            work_state.refresh_from_db();labor_state.refresh_from_db()
            result={'rows':result_rows,'revision':work_state.revision,'labor_revision':labor_state.revision,
                'checkpoint':batch_id.hex,'report_dirty':True}
            ProductionBatchReceipt.objects.create(id=batch_id,company=obj.company,construction_object=obj,date=day,user=request.user,digest=digest,result=result)
            return JsonResponse(result)
    except IntegrityError:
        return _error('Такая запись уже существует. Обновите данные и измените существующую строку.',409)
    except ValidationError as exc:
        message='; '.join(exc.messages)
        if 'unique_item_fact' in message or 'unique_simple_fact' in message:message='Такая работа уже добавлена за этот день. Измените существующую строку.'
        return _error(message,409)
