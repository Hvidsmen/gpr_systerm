"""Independent relationship edits and presence for the production day form."""
from datetime import timedelta
import hashlib
import json
from uuid import UUID
from django.contrib.auth.decorators import login_required
from django.core import signing
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import F, Q
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.http import require_POST
from core.fact_history import history_actor
from core.permissions import require_roles
from .forms import ObjectDateForm
from .entry_concurrency import sqlite_serialized
from .dispatch_models import (EmployeeActivity, EmployeeWorkLink, ProductionDailyCrew, ProductionPresence,
    WorkFactClaim, EmployeeFactClaim, FactSectionDay, ProductionEntryDay)
from .models import DailyFact
from .production_entry import targets, token, report_data
from .edit_lock import acquire as acquire_edit_lock, release as release_edit_lock, require_available, session_value
from .work_claims import owner_name, status_rows, release_session, TTL

ROLES={'ADMIN','DISPATCHER','ALL_FACT_DISPATCHER'}
GRACE=timedelta(seconds=120)
CLOSE_GRACE=timedelta(seconds=5)
RELATION_FIELDS=('general','work_id','work_item_id','assignment_reviewed','links_revision')


def expire_presence(obj, day):
    now=timezone.now()
    stale=ProductionPresence.objects.filter(company=obj.company,construction_object=obj,date=day).filter(
        Q(last_seen__lt=now-GRACE)|Q(closing_at__lt=now-CLOSE_GRACE))
    for presence in stale.select_related('user'):
        release_session(presence.user,obj,day,presence.session_key)
    stale.delete()


def scope(request):
    require_roles(request.user,ROLES)
    data=json.loads(request.body) if request.content_type=='application/json' else request.POST
    if not isinstance(data,dict) and not hasattr(data,'get'):raise ValueError
    selector=ObjectDateForm(data,user=request.user)
    if not selector.is_valid():raise ValidationError('Выберите доступный объект и корректную дату.')
    return data,selector.cleaned_data['construction_object'],selector.cleaned_data['date']


def apply_relations(row):
    links=EmployeeWorkLink.objects.filter(activity=row)
    selected=list(links.filter(fact=None).order_by('pk').values_list('work_id','work_item_id'))
    has_links=links.exists()
    row.work_id,row.work_item_id=selected[0] if selected else (None,None)
    row.general=False
    row.assignment_reviewed=has_links
    row.links_revision+=1
    # Relationship edits must not invalidate an independent data-field baseline.
    row.save(update_fields=RELATION_FIELDS)


def detach_work(fact):
    """Clearing or deleting a work fact removes only its edges for this day."""
    rows=EmployeeActivity.objects.filter(company=fact.company,construction_object=fact.project_work.section.construction_object,date=fact.date)
    affected=(rows.filter(work_links__fact=fact) if fact.operational_key else rows.filter(Q(work_id=fact.project_work_id,work_item_id=fact.work_item_id)|Q(work_links__work_id=fact.project_work_id,work_links__work_item_id=fact.work_item_id,work_links__fact=None))).distinct()
    for row in affected.select_for_update():
        (row.work_links.filter(fact=fact) if fact.operational_key else row.work_links.filter(work_id=fact.project_work_id,work_item_id=fact.work_item_id,fact=None)).delete()
        apply_relations(row)


def preserve_relations(row):
    if not row.pk:return
    fresh=EmployeeActivity.objects.select_for_update().get(pk=row.pk,company=row.company)
    for name in RELATION_FIELDS:setattr(row,name,getattr(fresh,name))


def automatic_readiness(obj,day,actor=None):
    entries=ProductionEntryDay.objects.filter(company=obj.company,construction_object=obj,date=day)
    # A production task is complete after the first successful server save.
    # Report completeness remains an independent quality check.
    ready=entries.filter(revision__gt=0).exists()
    stamp=timezone.now() if ready else None
    FactSectionDay.objects.filter(company=obj.company,construction_object=obj,date=day,section='works').update(confirmed_at=stamp,confirmed_by=None)
    entries.filter(revision__gt=0).update(confirmed_at=stamp)
    entries.filter(revision=0).update(confirmed_at=None)
    from apps.planning.models import WorkTask
    from apps.planning.task_services import state
    from .dispatch import fact_sections_confirmed
    for task in WorkTask.objects.filter(company=obj.company,construction_object=obj,fact_date=day,kind='FACT',status__in=['OPEN','DONE']).select_related('assigned_to__role'):
        if task.assigned_to:
            complete=fact_sections_confirmed(task.assigned_to,obj.company_id,obj.pk,day)
            state(task,'DONE' if complete else 'OPEN',actor,'Автоматическая проверка сохранённого факта.' if complete else 'Требуется проверить данные факта.')
    return ready


@login_required
@require_POST
@sqlite_serialized
def links(request):
    try:
        data,obj,day=scope(request)
        if data.get('protocol')=='queued-v1':
            from .satellite_links import batch
            return batch(request,data,obj,day)
        require_available(request.user,obj,day,data)
        from core.fact_edit_window import require_fact_day
        require_fact_day(request.user,day)
        action=data.get('action')
        if action not in {'add','remove','mode','delta'}:raise ValueError
        with transaction.atomic(),history_actor(request.user,'Изменение связей производства'):
            type(obj).objects.filter(pk=obj.pk,company=obj.company).update(name=F('name'))
            person=EmployeeActivity.objects.select_for_update().filter(company=obj.company,construction_object=obj,date=day,pk=data.get('activity'),entry_direction='works').first()
            if not person:return JsonResponse({'error':'Сотрудник уже удалён из факта. Обновлены карточки и связи.'},status=409)
            # Full replacements require their own revision; individual edges merge.
            if action in {'mode','delta'} and int(data.get('revision',-1))!=person.links_revision:
                return JsonResponse({'error':'Связи уже изменил другой пользователь. Проверьте обновлённые связи и повторите действие.','report':report_data(obj,day)},status=409)
            edges=data.get('edges',[]) if action=='delta' else [data] if action in {'add','remove'} else []
            if not isinstance(edges,list) or len(edges)>2000 or any(not isinstance(edge,dict) for edge in edges):raise ValueError
            resolved=[]
            for edge in edges:
                edge_action=edge.get('action',action)
                if edge_action not in {'add','remove'}:raise ValueError
                # IDs are tombstones: an old card cannot bind a newly re-added fact.
                fact=DailyFact.objects.select_for_update().filter(company=obj.company,date=day,project_work__section__construction_object=obj,pk=edge.get('fact')).first()
                if not fact:return JsonResponse({'error':'Работа уже удалена из факта. Сначала проверьте обновлённый список работ.'},status=409)
                if edge_action=='add' and fact.actual_quantity<=0:
                    return JsonResponse({'error':'У работы нулевой факт. Сначала введите выполненный объём больше нуля, затем добавьте связь.','report':report_data(obj,day)},status=409)
                resolved.append((edge_action,fact))
            crew=None
            if data.get('crew') is not None:
                crew=ProductionDailyCrew.objects.filter(pk=data['crew'],company=obj.company,construction_object=obj,date=day,is_active=True).first()
                if crew is None:raise ValidationError('Бригада уже удалена или изменена.')
            from .crew_links import change_crew,change_individual
            for edge_action,fact in resolved:
                (change_crew(person,crew,fact,edge_action) if crew else change_individual(person,fact,edge_action))
            if action=='mode':
                if data.get('mode') not in {'pending','general'}:raise ValueError
                from .crew_links import clear_individual
                clear_individual(person);has_crew_links=person.work_links.exists()
                person.general=data['mode']=='general' and not has_crew_links;person.work=None;person.work_item=None
                person.assignment_reviewed=has_crew_links or person.general;person.links_revision+=1
                person.save(update_fields=RELATION_FIELDS)
            else:apply_relations(person)
            automatic_readiness(obj,day,request.user)
            return JsonResponse({'report':report_data(obj,day)})
    except (ValueError,TypeError,KeyError):return JsonResponse({'error':'Некорректный запрос изменения связи.'},status=400)
    except ValidationError as exc:return JsonResponse({'error':'; '.join(exc.messages)},status=400)


def canonical_rows(obj,day,report):
    rows={'works':[],'labor':[]}
    work_ids={work['id'] for work in report['works']}
    work_rows=DailyFact.objects.filter(company=obj.company,date=day,project_work__section__construction_object=obj)
    if report.get('_scope')=='visible':work_rows=work_rows.filter(pk__in=work_ids)
    for fact in work_rows:
        values={name:str(value) if value is not None else '' for name,value in {
            'id':fact.pk,'project_work':fact.project_work_id,'work_item':fact.work_item_id,'operational_name':fact.operational_name,'operational_unit':fact.operational_unit,'actual_quantity':format(fact.actual_quantity.normalize(),'f'),
            'actuation_type':fact.actuation_type,'actuation_reason':fact.actuation_reason,'comment':fact.comment}.items()}
        values['actuation_reasons']=sorted(str(pk) for pk in fact.actuation_reason_ids)
        values['actuation_reasons_present']='1'
        values['DELETE']=False
        rows['works'].append({'id':fact.pk,'key':f'operational:{fact.pk}' if fact.operational_key else token(fact.project_work_id,fact.work_item_id),'version':fact.updated_at.isoformat(),'values':values})
    people={p['id']:p for p in report['people']}
    people_rows=EmployeeActivity.objects.filter(company=obj.company,construction_object=obj,date=day,entry_direction='works')
    if report.get('_scope')=='visible':people_rows=people_rows.filter(pk__in=people)
    for person in people_rows:
        values={name:str(value) if value is not None else '' for name,value in {
            'id':person.pk,'employee':person.employee_id,'shift':person.shift,'work':person.work_id,'work_item':person.work_item_id,
            'equipment':person.equipment_id,'activity':person.activity,'planning_position':person.planning_position_id}.items()}
        values.update(general=person.general,DELETE=False,work_mode='general' if person.general else 'works' if person.assignment_reviewed else 'pending',work_targets=sorted(people[person.pk]['targets']))
        rows['labor'].append({'id':person.pk,'key':f'employee:{person.employee_id}:{person.shift}','version':person.updated_at.isoformat(),'values':values})
    return rows


def rebase(user,obj,day,signed,rows,keep):
    from .entry_concurrency import SALT
    baseline=signing.loads(signed,salt=SALT,max_age=12*3600)
    if any(baseline.get(k)!=v for k,v in {'user':user.pk,'company':obj.company_id,'object':obj.pk,'day':day.isoformat()}.items()):raise ValidationError('Форма другого пользователя, объекта или дня.')
    for prefix,records in rows.items():
        old=baseline['rows'][prefix]
        fresh={f"pk:{row['id']}":{'version':row['version'],'values':row['values']} for row in records if row['key'] not in keep}
        def protected_key(values):
            if prefix=='labor':return f"employee:{values.get('employee')}:{values.get('shift')}"
            return token(values.get('project_work'),values.get('work_item') or None)
        protected={key:value for key,value in old.items() if key not in fresh and (not key.startswith('pk:') or protected_key(value['values']) in keep)}
        baseline['rows'][prefix]={**protected,**fresh}
    # Actuation remains protected by its original baseline; no silent rebasing.
    return signing.dumps(baseline,salt=SALT,compress=True)


@login_required
@require_POST
@sqlite_serialized
def live(request):
    try:
        data,obj,day=scope(request);session=UUID(str(data.get('session','')))
        if data.get('protocol')=='compact-v1' and (data.get('actor_id')!=request.user.pk or data.get('company_id')!=request.user.company_id):
            return JsonResponse({'error':'Учётная запись изменилась. Откройте форму заново.','auth_changed':True},status=403)
        with transaction.atomic():
            type(obj).objects.filter(pk=obj.pk,company=obj.company).update(name=F('name'))
            expire_presence(obj,day)
            from .day_reset import epoch
            if int(data.get('reset_epoch',0))!=epoch(obj,day):return JsonResponse({'error':'Производственная часть сброшена администратором. Обновите форму.','reset_required':True},status=409)
            now=timezone.now()
            existing=ProductionPresence.objects.filter(company=obj.company,construction_object=obj,date=day,session_key=session).first()
            if existing and existing.user_id!=request.user.pk:return JsonResponse({'error':'Сеанс принадлежит другому пользователю.'},status=403)
            if data.get('action')=='close':
                release_edit_lock(request.user,obj,day,session)
                if existing:existing.closing_at=now;existing.save(update_fields=['closing_at'])
                return JsonResponse({'closed':True})
            step=int(data.get('step',1))
            if step not in {1,2,3,4,5,6}:raise ValueError
            if existing is None:existing=ProductionPresence(company=obj.company,construction_object=obj,date=day,user=request.user,session_key=session)
            existing.step=step;existing.last_seen=now;existing.closing_at=None;existing.save()
            edit_lock=acquire_edit_lock(request.user,obj,day,session)
            for model in (WorkFactClaim,EmployeeFactClaim):
                model.objects.filter(company=obj.company,construction_object=obj,date=day,dispatcher=request.user,session_key=session,expires_at__gt=now).update(expires_at=now+TTL)
            claims=status_rows(request.user,obj,day,session)
            if data.get('protocol')=='compact-v1':
                visible_claims={str(key) for key in data.get('visible_claims',[]) if isinstance(key,str)}
                if visible_claims:claims=[claim for claim in claims if claim['key'] in visible_claims or claim.get('mine')]
            editing_ids=set(WorkFactClaim.objects.filter(company=obj.company,construction_object=obj,date=day,expires_at__gt=now).values_list('dispatcher_id',flat=True))
            editing_ids.update(EmployeeFactClaim.objects.filter(company=obj.company,construction_object=obj,date=day,expires_at__gt=now).values_list('dispatcher_id',flat=True))
            active={}
            for presence in ProductionPresence.objects.filter(company=obj.company,construction_object=obj,date=day,last_seen__gte=now-GRACE,closing_at=None).select_related('user').order_by('last_seen'):
                key=presence.user_id
                item=active.setdefault(key,{'id':key,'name':owner_name(presence.user),'self':key==request.user.pk,'steps':set(),'editing':False})
                item['steps'].add(presence.step)
                item['editing']=item['editing'] or key in editing_ids
            users=[{**item,'steps':sorted(item['steps'])} for item in active.values()]
            if data.get('protocol')=='compact-v1':
                from .satellite_sync import response_data
                result=response_data(request.user,obj,day,session,data,users,claims);result['edit_lock']=edit_lock
                return JsonResponse(result)
            report=report_data(obj,day);rows=canonical_rows(obj,day,report)
            from .actuation_models import ActuationReason
            reason_options=[{'id':r.pk,'label':r.label,'active':r.status in {'PENDING','APPROVED'}} for r in ActuationReason.objects.filter(company=obj.company)]
            digest=hashlib.sha256(json.dumps([rows,report,reason_options,users,[{k:v for k,v in c.items() if k!='expires_at'} for c in claims]],sort_keys=True,default=str).encode()).hexdigest()
            result={'users':users,'claims':claims,'digest':digest,'edit_lock':edit_lock}
            if data.get('digest')!=digest:
                result.update(report=report,rows=rows,reason_options=reason_options)
                if data.get('production_snapshot'):result['production_snapshot']=rebase(request.user,obj,day,data['production_snapshot'],rows,set(data.get('keep',[])))
            return JsonResponse(result)
    except (ValueError,TypeError,KeyError,signing.BadSignature):return JsonResponse({'error':'Не удалось обновить совместную форму. Обновите страницу.'},status=400)
    except ValidationError as exc:return JsonResponse({'error':'; '.join(exc.messages)},status=400)
