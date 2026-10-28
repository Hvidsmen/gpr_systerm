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
from .dispatch_models import (EmployeeActivity, EmployeeWorkLink, ProductionPresence,
    WorkFactClaim, EmployeeFactClaim, FactSectionDay, ProductionEntryDay)
from .models import DailyFact
from .production_entry import targets, token, report_data
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
    selected=list(row.work_links.order_by('pk').values_list('work_id','work_item_id'))
    row.work_id,row.work_item_id=selected[0] if selected else (None,None)
    row.general=False
    row.assignment_reviewed=bool(selected)
    row.links_revision+=1
    # Relationship edits must not invalidate an independent data-field baseline.
    row.save(update_fields=RELATION_FIELDS)


def detach_work(fact):
    """Deleting a work fact clears its edges; a zero quantity does not."""
    rows=EmployeeActivity.objects.filter(company=fact.company,construction_object=fact.project_work.section.construction_object,date=fact.date)
    affected=rows.filter(Q(work_id=fact.project_work_id,work_item_id=fact.work_item_id)|Q(work_links__work_id=fact.project_work_id,work_links__work_item_id=fact.work_item_id)).distinct()
    for row in affected.select_for_update():
        row.work_links.filter(work_id=fact.project_work_id,work_item_id=fact.work_item_id).delete()
        apply_relations(row)


def preserve_relations(row):
    if not row.pk:return
    fresh=EmployeeActivity.objects.select_for_update().get(pk=row.pk,company=row.company)
    for name in RELATION_FIELDS:setattr(row,name,getattr(fresh,name))


def automatic_readiness(obj,day,actor=None):
    from .dispatch import plans
    planned=plans(obj,day)[0]
    present=set(DailyFact.objects.filter(company=obj.company,date=day,project_work__section__construction_object=obj).values_list('project_work_id','work_item_id'))
    people=EmployeeActivity.objects.filter(company=obj.company,construction_object=obj,date=day,entry_direction='works')
    used=set(EmployeeWorkLink.objects.filter(company=obj.company,activity__in=people).values_list('work_id','work_item_id'))
    used.update(people.exclude(work=None).values_list('work_id','work_item_id'))
    ready=not(set(planned)-present or used-present or people.filter(assignment_reviewed=False).exists())
    stamp=timezone.now() if ready else None
    FactSectionDay.objects.filter(company=obj.company,construction_object=obj,date=day,section='works').update(confirmed_at=stamp,confirmed_by=None)
    ProductionEntryDay.objects.filter(company=obj.company,construction_object=obj,date=day).update(confirmed_at=stamp)
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
                resolved.append((edge_action,fact))
            for edge_action,fact in resolved:
                if edge_action=='add':
                    EmployeeWorkLink.objects.get_or_create(company=obj.company,activity=person,work_id=fact.project_work_id,work_item_id=fact.work_item_id)
                else:person.work_links.filter(work_id=fact.project_work_id,work_item_id=fact.work_item_id).delete()
            if action=='mode':
                if data.get('mode') not in {'pending','general'}:raise ValueError
                person.work_links.all().delete()
                person.general=data['mode']=='general';person.work=None;person.work_item=None
                person.assignment_reviewed=person.general;person.links_revision+=1
                person.save(update_fields=RELATION_FIELDS)
            else:apply_relations(person)
            automatic_readiness(obj,day,request.user)
            return JsonResponse({'report':report_data(obj,day)})
    except (ValueError,TypeError,KeyError):return JsonResponse({'error':'Некорректный запрос изменения связи.'},status=400)
    except ValidationError as exc:return JsonResponse({'error':'; '.join(exc.messages)},status=400)


def canonical_rows(obj,day,report):
    rows={'works':[],'labor':[]}
    for fact in DailyFact.objects.filter(company=obj.company,date=day,project_work__section__construction_object=obj):
        values={name:str(value) if value is not None else '' for name,value in {
            'id':fact.pk,'project_work':fact.project_work_id,'work_item':fact.work_item_id,'actual_quantity':fact.actual_quantity.normalize(),
            'deviation_reason':fact.deviation_reason_id,'comment':fact.comment}.items()}
        values['DELETE']=False
        rows['works'].append({'id':fact.pk,'key':token(fact.project_work_id,fact.work_item_id),'version':fact.updated_at.isoformat(),'values':values})
    people={p['id']:p for p in report['people']}
    for person in EmployeeActivity.objects.filter(company=obj.company,construction_object=obj,date=day,entry_direction='works'):
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
        with transaction.atomic():
            type(obj).objects.filter(pk=obj.pk,company=obj.company).update(name=F('name'))
            expire_presence(obj,day)
            now=timezone.now()
            existing=ProductionPresence.objects.filter(company=obj.company,construction_object=obj,date=day,session_key=session).first()
            if existing and existing.user_id!=request.user.pk:return JsonResponse({'error':'Сеанс принадлежит другому пользователю.'},status=403)
            if data.get('action')=='close':
                if existing:existing.closing_at=now;existing.save(update_fields=['closing_at'])
                return JsonResponse({'closed':True})
            step=int(data.get('step',1))
            if step not in {1,2,3,4}:raise ValueError
            if existing is None:existing=ProductionPresence(company=obj.company,construction_object=obj,date=day,user=request.user,session_key=session)
            existing.step=step;existing.last_seen=now;existing.closing_at=None;existing.save()
            for model in (WorkFactClaim,EmployeeFactClaim):
                model.objects.filter(company=obj.company,construction_object=obj,date=day,dispatcher=request.user,session_key=session,expires_at__gt=now).update(expires_at=now+TTL)
            claims=status_rows(request.user,obj,day,session)
            active={}
            for presence in ProductionPresence.objects.filter(company=obj.company,construction_object=obj,date=day,last_seen__gte=now-GRACE,closing_at=None).select_related('user').order_by('last_seen'):
                key=presence.user_id
                item=active.setdefault(key,{'id':key,'name':owner_name(presence.user),'self':key==request.user.pk,'steps':set(),'editing':False})
                item['steps'].add(presence.step)
                item['editing']=item['editing'] or WorkFactClaim.objects.filter(company=obj.company,construction_object=obj,date=day,dispatcher_id=key,expires_at__gt=now).exists() or EmployeeFactClaim.objects.filter(company=obj.company,construction_object=obj,date=day,dispatcher_id=key,expires_at__gt=now).exists()
            users=[{**item,'steps':sorted(item['steps'])} for item in active.values()]
            report=report_data(obj,day);rows=canonical_rows(obj,day,report)
            digest=hashlib.sha256(json.dumps([rows,report,users,[{k:v for k,v in c.items() if k!='expires_at'} for c in claims]],sort_keys=True,default=str).encode()).hexdigest()
            result={'users':users,'claims':claims,'digest':digest}
            if data.get('digest')!=digest:
                result.update(report=report,rows=rows)
                if data.get('production_snapshot'):result['production_snapshot']=rebase(request.user,obj,day,data['production_snapshot'],rows,set(data.get('keep',[])))
            return JsonResponse(result)
    except (ValueError,TypeError,KeyError,signing.BadSignature):return JsonResponse({'error':'Не удалось обновить совместную форму. Обновите страницу.'},status=400)
    except ValidationError as exc:return JsonResponse({'error':'; '.join(exc.messages)},status=400)
