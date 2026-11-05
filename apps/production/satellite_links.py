"""Small, idempotent batches for the low bandwidth production links outbox."""
import hashlib
import json
from uuid import UUID
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import F
from django.http import JsonResponse
from core.fact_edit_window import require_fact_day
from core.fact_history import history_actor
from .dispatch_models import EmployeeActivity, EmployeeWorkLink, ProductionLinkReceipt
from .models import DailyFact


def person_state(row):
    return {'id':row.pk,'employee':row.employee_id,'shift':row.shift,'general':row.general,'reviewed':row.assignment_reviewed,
        'links_revision':row.links_revision,'targets':[
            f'operational:{link.fact_id}' if link.fact_id else (f'item:{link.work_id}:{link.work_item_id}' if link.work_item_id else f'work:{link.work_id}')
            for link in row.work_links.order_by('pk')]}


def execute(user,obj,day,command):
    from .collaboration import apply_relations, RELATION_FIELDS
    row=EmployeeActivity.objects.select_for_update().filter(company=obj.company,construction_object=obj,date=day,pk=command['activity'],entry_direction='works').first()
    if row is None:raise ValidationError('Сотрудник удалён из производственного факта. Обновите список.')
    action=command['action']
    if action=='mode':
        if command.get('mode') not in {'general','pending'}:raise ValidationError('Неизвестный вид занятости.')
        if command.get('revision')!=row.links_revision:raise ValidationError('Занятость уже изменена коллегой. Проверьте обновлённые связи и повторите действие.')
        row.work_links.all().delete();row.general=command['mode']=='general';row.assignment_reviewed=row.general
        row.work=None;row.work_item=None;row.links_revision+=1;row.save(update_fields=RELATION_FIELDS)
        return
    # Keep a valid legacy primary assignment when adding an independent second edge.
    if row.work_id and not row.work_links.exists() and DailyFact.objects.filter(company=obj.company,date=day,project_work_id=row.work_id,work_item_id=row.work_item_id,actual_quantity__gt=0,project_work__section__construction_object=obj).exists():
        EmployeeWorkLink.objects.get_or_create(company=obj.company,activity=row,work_id=row.work_id,work_item_id=row.work_item_id)
    edges=command.get('edges') if action=='delta' else [{'action':action,'fact':command.get('fact')}]
    if action not in {'add','remove','delta'} or not isinstance(edges,list) or len(edges)>2000:raise ValidationError('Некорректный список связей.')
    resolved=[]
    for edge in edges:
        if not isinstance(edge,dict) or edge.get('action') not in {'add','remove'}:raise ValidationError('Некорректное действие со связью.')
        fact=DailyFact.objects.select_for_update().filter(company=obj.company,date=day,project_work__section__construction_object=obj,pk=edge.get('fact')).first()
        if fact is None:raise ValidationError('Работа удалена из факта. Проверьте обновлённый список работ.')
        if edge['action']=='add' and fact.actual_quantity<=0:raise ValidationError('У работы нулевой факт. Связь не добавлена; сначала внесите выполненный объём.')
        resolved.append((edge['action'],fact))
    for action,fact in resolved:
        if action=='add':EmployeeWorkLink.objects.get_or_create(company=obj.company,activity=row,work_id=fact.project_work_id,work_item_id=fact.work_item_id,fact=fact if fact.operational_key else None)
        else:(row.work_links.filter(fact=fact) if fact.operational_key else row.work_links.filter(work_id=fact.project_work_id,work_item_id=fact.work_item_id,fact=None)).delete()
    apply_relations(row)


def batch(request,data,obj,day):
    from .day_reset import epoch
    if data.get('actor_id')!=request.user.pk or data.get('company_id')!=request.user.company_id:
        return JsonResponse({'error':'Учётная запись изменилась. Откройте форму под нужным пользователем; очередь осталась в его браузере.','auth_changed':True},status=403)
    commands=data.get('commands')
    if not isinstance(commands,list) or not 1<=len(commands)<=40:return JsonResponse({'error':'За один раз допустимо от 1 до 40 изменений связей.'},status=400)
    # Validate the complete envelope before any write. A retry uses exactly the same command UUID and body.
    prepared=[];seen=set()
    try:
        for source in commands:
            if not isinstance(source,dict):raise ValueError
            key=UUID(str(source.get('id')))
            if key in seen:raise ValueError
            seen.add(key);command={name:source[name] for name in ('activity','action','fact','edges','mode','revision') if name in source}
            if type(command.get('activity')) is not int or command['activity']<=0:raise ValueError
            if command.get('action') not in {'add','remove','delta','mode'}:raise ValueError
            digest=hashlib.sha256(json.dumps(command,sort_keys=True,separators=(',',':')).encode()).hexdigest()
            prepared.append((key,command,digest))
    except (TypeError,ValueError):return JsonResponse({'error':'Некорректный пакет изменений связей.'},status=400)
    with transaction.atomic(),history_actor(request.user,'Пакетное сохранение связей производства'):
        type(obj).objects.filter(pk=obj.pk,company=obj.company).update(name=F('name'))
        current_epoch=epoch(obj,day)
        if int(data.get('reset_epoch',0))!=current_epoch:return JsonResponse({'error':'Производственный день сброшен. Проверьте данные перед повторением изменений.','reset_required':True},status=409)
        receipts={r.pk:r for r in ProductionLinkReceipt.objects.filter(pk__in=[key for key,_,_ in prepared])}
        for key,command,digest in prepared:
            receipt=receipts.get(key)
            if receipt and (receipt.company_id,receipt.user_id,receipt.construction_object_id,receipt.date,receipt.digest)!=(obj.company_id,request.user.pk,obj.pk,day,digest):
                return JsonResponse({'error':'Идентификатор изменения уже использован для других данных.'},status=409)
        client_digest=data.get('data_digest','')
        from .satellite_sync import state_stamp
        acknowledge_digest=bool(isinstance(client_digest,str) and len(client_digest)==64 and state_stamp(obj,day)==client_digest)
        results=[];changed=False
        for key,command,digest in prepared:
            receipt=receipts.get(key)
            if receipt:
                if (receipt.company_id,receipt.user_id,receipt.construction_object_id,receipt.date,receipt.digest)!=(obj.company_id,request.user.pk,obj.pk,day,digest):
                    return JsonResponse({'error':'Идентификатор изменения уже использован для других данных.'},status=409)
                results.append(receipt.result);continue
            result={'id':str(key),'activity':command['activity'],'ok':True}
            try:
                with transaction.atomic():
                    require_fact_day(request.user,day)
                    execute(request.user,obj,day,command)
                changed=True
            except (ValidationError,ValueError,TypeError) as exc:
                result.update(ok=False,error='; '.join(exc.messages) if isinstance(exc,ValidationError) else 'Некорректные данные связи.')
            ProductionLinkReceipt.objects.create(id=key,company=obj.company,construction_object=obj,date=day,user=request.user,digest=digest,result=result)
            results.append(result)
        if changed:
            from .collaboration import automatic_readiness
            automatic_readiness(obj,day,request.user)
        ids={c['activity'] for _,c,_ in prepared}
        people=[person_state(row) for row in EmployeeActivity.objects.filter(company=obj.company,construction_object=obj,date=day,pk__in=ids,entry_direction='works')]
        response={'results':results,'people':people,'removed':sorted(ids-{p['id'] for p in people}),'reset_epoch':current_epoch,'compact':True}
        if acknowledge_digest:response['digest']=state_stamp(obj,day)
        return JsonResponse(response)
