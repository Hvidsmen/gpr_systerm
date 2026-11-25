"""Company-scoped transitions. Company serialization also works with SQLite."""
from pathlib import Path
from uuid import uuid4
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import F
from django.utils import timezone
from core.permissions import require_roles
from .models import (RecruitmentRequest, RecruitmentCandidate, ArrivalPreparation,
                     CandidateDocument, RecruitmentEvent, CampStay,MovementReviewRequest,TalentPoolCandidate,
                     TalentPoolStageEvent)
from .models import Person
from .recruitment_models import POOL_UNAVAILABLE_STAGES,RECRUITMENT_SYSTEM_STAGES,RECRUITMENT_TERMINAL_STAGES

DISPATCH = {'ADMIN','HR_HEAD','PERSONNEL_DISPATCHER','PEREVAKHTA_SPECIALIST'}
RECRUIT = {'ADMIN','HR_HEAD','RECRUITMENT_HEAD','RECRUITER','PEREVAKHTA_SPECIALIST'}
ASSIGN = {'ADMIN','HR_HEAD','RECRUITMENT_HEAD','PEREVAKHTA_SPECIALIST'}
RECRUITER_USER_ROLES = {'RECRUITER','RECRUITMENT_HEAD'}
ARRIVAL_PROCESSORS = DISPATCH | {'PEREVAKHTA_SPECIALIST'}
READ = DISPATCH | RECRUIT | {'PEREVAKHTA_SPECIALIST'}
EDITABLE_CANDIDATE_STAGES={value for value,_ in RecruitmentCandidate.STATES}-RECRUITMENT_SYSTEM_STAGES
ACTIVE_CANDIDATE_STAGES={value for value,_ in RecruitmentCandidate.STATES}-RECRUITMENT_TERMINAL_STAGES-{'RESERVE','ARRIVED'}
TalentPoolFormConfirmationPairs=(('ready_for_arrival','ready_for_arrival_on'),('manager_approved','manager_approved_on'),
    ('security_approved','security_approved_on'),('medical_exam_passed','medical_exam_on'),
    ('pass_documents_sent','pass_documents_sent_on'),('pass_ready','pass_ready_on'))


def serialize(user, roles):
    require_roles(user, roles)
    from apps.accounts.models import Company
    Company.objects.filter(pk=user.company_id).update(name=F('name'))


def current(user, pk, revision=None):
    row = RecruitmentRequest.objects.select_for_update().get(company=user.company, pk=pk)
    if revision is not None and row.revision != revision:raise ValidationError('Заявка уже изменена. Обновите страницу.')
    if row.cancelled_at:raise ValidationError('Заявка отменена.')
    if row.completed_at:raise ValidationError('Заявка выполнена.')
    return row


def audit(user, row, description, candidate=None):
    RecruitmentEvent.objects.create(company=user.company,request=row,candidate=candidate,actor=user,description=description)


def touch(row):
    row.revision += 1
    row.save(update_fields=['revision','updated_at'])


def allowed_camps(company, obj):
    from apps.projects.models import ConstructionObject
    from .models import Camp
    # A macro-object request may arrive at any camp linked to one of its children.
    ids={obj.pk};frontier={obj.pk}
    while frontier:
        frontier=set(ConstructionObject.objects.filter(company=company,parent_id__in=frontier).values_list('pk',flat=True))-ids
        ids.update(frontier)
    return Camp.objects.filter(company=company,is_active=True,objects_served__in=ids).distinct()


def progress(row):
    candidates=list(row.candidates.all())
    shortlisted=[c for c in candidates if c.status not in RECRUITMENT_TERMINAL_STAGES|{'RESERVE'}]
    ready=[];arrived=[]
    for c in shortlisted:
        prep=getattr(c,'arrival',None)
        if prep and prep.actually_arrived:arrived.append(c)
        if c.status in {'READY','TRANSFERRED','ARRIVAL_APPROVED','ARRIVED'} and not (prep and prep.status=='CANCELLED'):ready.append(c)
    state=('Отменена' if row.cancelled_at else
           'Закрыта автоматически' if row.closed_at and row.close_kind=='AUTO' else
           'Закрыта принудительно' if row.closed_at else
           'Выполнена' if row.completed_at or len(arrived)>=row.quantity else
           'Кандидаты готовы' if len(ready)>=row.quantity else 'В работе' if row.assigned_to_id or candidates else 'Новая')
    return {'required':row.quantity,'shortlisted':len(shortlisted),'ready':len(ready),'arrived':len(arrived),'label':state}


def found_count(row):
    return RecruitmentCandidate.objects.filter(request=row).exclude(status__in=RECRUITMENT_TERMINAL_STAGES|{'RESERVE'}).count()


def sync_auto_closure(user,row):
    """Close a filled request, or reopen it when an automatically selected candidate drops out."""
    found=found_count(row)
    if not row.closed_at and found>=row.quantity:
        row.closed_at=timezone.now();row.closed_by=user;row.close_kind='AUTO'
        row.close_reason=f'Подобрано требуемое количество сотрудников: {found} из {row.quantity}.'
        row.revision+=1
        row.save(update_fields=['closed_at','closed_by','close_kind','close_reason','revision','updated_at'])
        audit(user,row,row.close_reason)
        return True
    if row.closed_at and row.close_kind=='AUTO' and found<row.quantity and not row.completed_at:
        old_reason=row.close_reason
        row.closed_at=None;row.closed_by=None;row.close_kind='';row.close_reason='';row.revision+=1
        row.save(update_fields=['closed_at','closed_by','close_kind','close_reason','revision','updated_at'])
        audit(user,row,f'Автоматическое закрытие снято: подобрано {found} из {row.quantity}. {old_reason}')
        return True
    return False


@transaction.atomic
def save_request(user, values, pk=None, revision=None):
    serialize(user,DISPATCH)
    row=current(user,pk,revision) if pk else RecruitmentRequest(company=user.company,created_by=user)
    if row.closed_at:raise ValidationError('Подбор по заявке уже закрыт.')
    position=values.get('position_ref')
    if position is None:
        raw=values.get('position')
        from apps.resources.models import Position
        if isinstance(raw,Position):position=raw
        elif raw:
            matches=list(Position.objects.filter(company=user.company,name__iexact=' '.join(str(raw).split()))[:2])
            if len(matches)==1:position=matches[0]
    if position is None or position.company_id!=user.company_id:raise ValidationError('Выберите должность из справочника.')
    for field in ('construction_object','quantity','required_on','requirements'):setattr(row,field,values[field])
    row.position_ref=position;row.position=position.name
    # Changing the destination after handing candidates off would strand preparations.
    if pk and ArrivalPreparation.objects.filter(candidate__request=row).exists():
        before=RecruitmentRequest.objects.get(pk=pk)
        if before.construction_object_id!=row.construction_object_id:raise ValidationError('После передачи кандидатов объект менять нельзя. Создайте другую заявку.')
    row.revision+=1;row.save()
    audit(user,row,'Заявка изменена.' if pk else 'Создана заявка на подбор.')
    return row


@transaction.atomic
def assign_request(user,pk,recruiter,revision):
    serialize(user,ASSIGN);row=current(user,pk,revision)
    if row.closed_at:raise ValidationError('Подбор по заявке уже закрыт.')
    from core.permissions import role_code
    if (not recruiter or recruiter.company_id!=user.company_id or not recruiter.is_active or recruiter.archived_at or
            role_code(recruiter) not in RECRUITER_USER_ROLES):
        raise ValidationError('Выберите действующего рекрутера или руководителя банка сотрудников.')
    previous=row.assigned_to
    row.assigned_to=recruiter;row.save(update_fields=['assigned_to','updated_at']);touch(row)
    description=f'Назначен ответственный рекрутер: {recruiter.get_full_name() or recruiter.username}.'
    if previous and previous.pk!=recruiter.pk:
        description=f'Ответственный рекрутер изменён: {previous.get_full_name() or previous.username} → {recruiter.get_full_name() or recruiter.username}.'
    audit(user,row,description)
    return row


def owned(user,row):
    from core.permissions import role_code
    if role_code(user)=='RECRUITER' and row.assigned_to_id!=user.pk:
        raise ValidationError('Заявку ведёт назначенный ответственный рекрутер.')


@transaction.atomic
def cancel_request(user, pk, revision, reason):
    serialize(user,DISPATCH);row=current(user,pk,revision)
    if not reason.strip():raise ValidationError('Укажите причину отмены.')
    row.cancelled_at=timezone.now();row.revision+=1;row.save()
    for prep in ArrivalPreparation.objects.select_for_update().filter(candidate__request=row,status='EXPECTED'):
        prep.status='CANCELLED';prep.revision+=1;prep.save()
    audit(user,row,'Заявка отменена: '+reason.strip())
    return row


@transaction.atomic
def close_request(user,pk,revision,reason):
    serialize(user,DISPATCH|RECRUIT);row=current(user,pk,revision)
    if row.closed_at:raise ValidationError('Подбор по заявке уже закрыт.')
    reason=' '.join((reason or '').split())
    if not reason:raise ValidationError('Укажите причину закрытия.')
    row.closed_at=timezone.now();row.closed_by=user;row.close_kind='FORCED';row.close_reason=reason;row.revision+=1
    row.save(update_fields=['closed_at','closed_by','close_kind','close_reason','revision','updated_at'])
    audit(user,row,'Заявка закрыта принудительно. Причина: '+reason)
    return row


def validate_documents(files):
    if len(files)>10:raise ValidationError('За один раз можно добавить до 10 файлов.')
    for file in files:
        if Path(file.name).suffix.lower() not in {'.pdf','.doc','.docx','.txt','.jpg','.jpeg','.png'}:
            raise ValidationError('Резюме и документы: PDF, DOC, DOCX, TXT, JPG или PNG.')
        if not file.size or file.size>10*1024*1024:raise ValidationError('Размер каждого файла — от 1 байта до 10 МБ.')


def save_pool_documents(user,candidate,files_by_kind):
    """Store private candidate files after the card itself has validated."""
    from .models import TalentPoolDocument
    allowed=dict(TalentPoolDocument.KINDS);written=[]
    try:
        for kind,files in files_by_kind.items():
            if kind not in allowed:raise ValidationError('Неизвестный раздел документов кандидата.')
            validate_documents(files)
            for file in files:
                doc=TalentPoolDocument(company=user.company,candidate=candidate,kind=kind,uploaded_by=user,
                    original_name=Path(file.name.replace('\\','/')).name[:255],size=file.size)
                doc.file.save(file.name,file,save=False);written.append(doc.file);doc.save()
    except Exception:
        for stored in written:
            try:stored.storage.delete(stored.name)
            except OSError:pass
        raise
    return len(written)


def normalized_candidate_identity(full_name,phone):
    name=' '.join((full_name or '').split()).casefold().replace('ё','е')
    digits=''.join(character for character in (phone or '') if character.isdigit())
    if len(digits)==11 and digits[0] in {'7','8'}:digits=digits[-10:]
    return name,digits


def duplicate_candidates(company,full_name,phone,exclude_pk=None):
    """Return exact duplicates by the required pair: normalized name and phone."""
    identity=normalized_candidate_identity(full_name,phone)
    if not all(identity):return []
    rows=TalentPoolCandidate.objects.filter(company=company)
    if exclude_pk:rows=rows.exclude(pk=exclude_pk)
    stage_labels=dict(TalentPoolCandidate._meta.get_field('stage').choices)
    matches=[]
    for row in rows.only('pk','full_name','phone','stage','archived_at'):
        if normalized_candidate_identity(row.full_name,row.phone)==identity:
            matches.append({'pk':row.pk,'full_name':row.full_name,'phone':row.phone,
                'stage':row.stage,'stage_label':stage_labels[row.stage],'archived':bool(row.archived_at)})
    return matches


def duplicate_candidate_map(company):
    """Map every duplicate candidate id to the other matching cards."""
    groups={};rows=list(TalentPoolCandidate.objects.filter(company=company).only(
        'pk','full_name','phone','stage','archived_at'))
    stage_labels=dict(TalentPoolCandidate._meta.get_field('stage').choices)
    for row in rows:
        identity=normalized_candidate_identity(row.full_name,row.phone)
        if all(identity):groups.setdefault(identity,[]).append(row)
    result={}
    for matches in groups.values():
        if len(matches)<2:continue
        for row in matches:
            result[row.pk]=[{'pk':other.pk,'full_name':other.full_name,'phone':other.phone,
                'stage':other.stage,'stage_label':stage_labels[other.stage],'archived':bool(other.archived_at)}
                for other in matches if other.pk!=row.pk]
    return result


def pool_candidate_delete_blockers(candidate):
    """Explain why a durable candidate card must be retained instead of deleted."""
    blockers=[]
    application_count=candidate.vacancy_candidates.count()
    movement_count=candidate.movement_requests.count()
    document_count=candidate.documents.count()
    if application_count:blockers.append(f'заявки на подбор: {application_count}')
    if movement_count:blockers.append(f'задания перевахты: {movement_count}')
    if document_count:blockers.append(f'документы: {document_count}')
    if candidate.employed_employee_id:blockers.append('привязка к сотруднику ЗУП')
    return blockers


@transaction.atomic
def restart_dismissed_candidate(user,candidate_id):
    """Start a fresh recruitment cycle while retaining the previous employment history."""
    candidate=TalentPoolCandidate.objects.select_for_update().select_related('employed_employee').get(
        company=user.company,pk=candidate_id)
    if candidate.stage!='DISMISSED':
        raise ValidationError('Повторный приём можно начать только для кандидата на этапе «Уволен».')
    active_movements=candidate.movement_requests.exclude(status__in={'COMPLETED','ACCEPTED','CANCELLED'}).count()
    if active_movements:
        raise ValidationError(f'Сначала завершите действующие задания перевахты: {active_movements}.')
    now=timezone.now();source=dict(candidate.source_data or {})
    cycles=list(source.get('recruitment_cycles') or [])
    cycle_number=int(source.get('current_recruitment_cycle') or len(cycles)+1)
    employee=candidate.employed_employee
    confirmations={flag:{'confirmed':getattr(candidate,flag),'date':(
        getattr(candidate,date_field).isoformat() if getattr(candidate,date_field) else '')}
        for flag,date_field in TalentPoolFormConfirmationPairs}
    cycles.append({'number':cycle_number,
        'started_at':source.get('current_cycle_started_at') or candidate.created_at.isoformat(),
        'ended_at':now.isoformat(),'final_stage':candidate.stage,'final_stage_label':candidate.get_stage_display(),
        'employee':({'id':employee.pk,'name':employee.display_name,'employee_number':employee.employee_number,
            'employed_at':candidate.employed_at.isoformat() if candidate.employed_at else ''} if employee else {}),
        'application_ids':list(candidate.vacancy_candidates.values_list('pk',flat=True)),
        'confirmations':confirmations})
    source['recruitment_cycles']=cycles
    source['current_recruitment_cycle']=cycle_number+1
    source['current_cycle_started_at']=now.isoformat();source['funnel_stage']='NEW'
    source.pop('employment_review',None);source.pop('employment_match',None)
    for key in ('readiness','manager_approval','security_approval','medical_exam','pass_documents','pass_submitted','pass_received'):
        source[key]=''
    candidate.source_data=source;candidate.stage='NEW';candidate.stage_started_at=now;candidate.is_active=True
    candidate.archived_at=None;candidate.archived_by=None;candidate.employed_employee=None;candidate.employed_at=None
    update_fields=['source_data','stage','stage_started_at','is_active','archived_at','archived_by',
        'employed_employee','employed_at','updated_at']
    for flag,date_field in TalentPoolFormConfirmationPairs:
        setattr(candidate,flag,False);setattr(candidate,date_field,None);update_fields.extend((flag,date_field))
    candidate.save(update_fields=update_fields)
    TalentPoolStageEvent.objects.create(company=user.company,candidate=candidate,from_stage='DISMISSED',to_stage='NEW',actor=user,
        note=f'Повторный приём: начат цикл №{cycle_number+1}. История цикла №{cycle_number} сохранена.')
    return candidate


@transaction.atomic
def merge_pool_candidates(user,source_id,target_id):
    """Merge a confirmed duplicate into the selected durable card without losing links."""
    if source_id==target_id:raise ValidationError('Выберите другую основную карточку.')
    rows={row.pk:row for row in TalentPoolCandidate.objects.select_for_update().filter(
        company=user.company,pk__in={source_id,target_id})}
    if set(rows)!={source_id,target_id}:raise ValidationError('Одна из карточек не найдена.')
    source,target=rows[source_id],rows[target_id]
    source_identity=normalized_candidate_identity(source.full_name,source.phone)
    target_identity=normalized_candidate_identity(target.full_name,target.phone)
    if not all(source_identity) or source_identity!=target_identity:
        raise ValidationError('Объединять можно только карточки с одинаковыми ФИО и телефоном.')
    if source.employed_employee_id and target.employed_employee_id and source.employed_employee_id!=target.employed_employee_id:
        raise ValidationError('Карточки связаны с разными сотрудниками ЗУП. Сначала проверьте привязку.')

    if source.employed_employee_id and not target.employed_employee_id:
        employee_id=source.employed_employee_id
        TalentPoolCandidate.objects.filter(pk=source.pk).update(employed_employee=None)
        target.employed_employee_id=employee_id
        target.employed_at=source.employed_at
    for field in ('position_id','construction_object_id','camp_id','base_city_id','responsible_hr_id'):
        if not getattr(target,field) and getattr(source,field):setattr(target,field,getattr(source,field))
    for flag,date_field in TalentPoolFormConfirmationPairs:
        if getattr(source,flag) and not getattr(target,flag):
            setattr(target,flag,True);setattr(target,date_field,getattr(source,date_field))
    if source.notes and source.notes not in target.notes:
        target.notes='\n\n'.join(value for value in (target.notes,source.notes) if value)
    target_source=dict(target.source_data or {});source_data=dict(source.source_data or {})
    for key,value in source_data.items():
        if key!='funnel_sources' and value and not target_source.get(key):target_source[key]=value
    funnel_sources=[]
    for value in [*(target_source.get('funnel_sources') or []),*(source_data.get('funnel_sources') or [])]:
        if value not in funnel_sources:funnel_sources.append(value)
    if funnel_sources:target_source['funnel_sources']=funnel_sources
    merged=list(target_source.get('merged_candidates') or [])
    merged.extend(source_data.get('merged_candidates') or [])
    merged.append({'id':source.pk,'full_name':source.full_name,'phone':source.phone,
        'merged_at':timezone.now().isoformat(),'merged_by':user.pk})
    target_source['merged_candidates']=merged;target.source_data=target_source
    target.save()

    RecruitmentCandidate.objects.filter(company=user.company,pool_candidate=source).update(pool_candidate=target)
    MovementReviewRequest.objects.filter(company=user.company,pool_candidate=source).update(pool_candidate=target)
    source.documents.update(candidate=target);source.stage_events.update(candidate=target)
    TalentPoolStageEvent.objects.create(company=user.company,candidate=target,from_stage=target.stage,
        to_stage=target.stage,actor=user,note=f'Объединена карточка №{source.pk}: {source.full_name}, {source.phone}.')
    source.delete()
    return target


def ensure_pool_candidate(user,row,candidate,chosen=None):
    """Every vacancy application points to one durable candidate card."""
    if chosen:return chosen
    identity=normalized_candidate_identity(candidate.full_name,candidate.phone)
    matches=[]
    for pool in TalentPoolCandidate.objects.select_for_update().filter(company=user.company):
        pool_identity=normalized_candidate_identity(pool.full_name,pool.phone)
        if pool_identity==identity or identity[0] and not identity[1] and pool_identity[0]==identity[0]:matches.append(pool)
    if len(matches)>1:raise ValidationError('Найдено несколько карточек с таким ФИО. Выберите кандидата из пула вручную.')
    if matches:
        if matches[0].archived_at:raise ValidationError('Карточка кандидата находится в архиве. Сначала восстановите её.')
        return matches[0]
    return TalentPoolCandidate.objects.create(company=user.company,full_name=candidate.full_name,phone=candidate.phone,
        notes='',stage=candidate.status,created_by=user,position=row.position_ref,
        construction_object=row.construction_object,ready_for_arrival=bool(candidate.ready_on),ready_for_arrival_on=candidate.ready_on,source_data={
            'position':row.position,'construction_site':row.construction_object.name,'origin':'recruitment',
        })


def sync_pool_candidate(candidate,stage=None):
    if not candidate.pool_candidate_id:return
    pool=candidate.pool_candidate;stage=stage or candidate.status
    if pool.employed_employee_id:
        from .recruitment_employment import employment_confirmed
        if employment_confirmed(pool.employed_employee):stage='EMPLOYED' if pool.employed_employee.is_active else 'DISMISSED'
    pool.stage=stage
    pool.full_name=candidate.full_name
    if candidate.phone:pool.phone=candidate.phone
    if not pool.notes and candidate.notes:pool.notes=candidate.notes
    pool.is_active=stage not in POOL_UNAVAILABLE_STAGES
    source=dict(pool.source_data or {})
    source.setdefault('position',candidate.request.position)
    source.setdefault('construction_site',candidate.request.construction_object.name)
    source['funnel_stage']=stage
    pool.source_data=source
    if not pool.construction_object_id:pool.construction_object=candidate.request.construction_object
    if not pool.position_id:
        pool.position=candidate.request.position_ref
    if candidate.ready_on:
        pool.ready_for_arrival=True;pool.ready_for_arrival_on=candidate.ready_on
    arrival=getattr(candidate,'arrival',None)
    if arrival and arrival.camp_id:pool.camp=arrival.camp
    pool.save(update_fields=['stage','full_name','phone','notes','is_active','source_data','construction_object','position',
        'ready_for_arrival','ready_for_arrival_on','camp','updated_at'])


def save_candidate(user, request_id, values, pk=None, revision=None, files=()):
    # Cleanup private blobs even if the enclosing DB operation rolls back.
    written=[]
    try:
        with transaction.atomic():
            serialize(user,RECRUIT);row=current(user,request_id);owned(user,row)
            if row.closed_at and (not pk or row.close_kind=='FORCED'):
                raise ValidationError('Подбор по заявке закрыт. Новых кандидатов добавлять нельзя.')
            c=RecruitmentCandidate.objects.select_for_update().get(company=user.company,request=row,pk=pk) if pk else RecruitmentCandidate(company=user.company,request=row,created_by=user)
            if pk and c.revision!=revision:raise ValidationError('Кандидата уже изменили. Обновите страницу.')
            if pk and ArrivalPreparation.objects.filter(candidate=c).exists():raise ValidationError('Кандидат уже передан. Изменения заезда оформляет диспетчер.')
            if values['status'] not in EDITABLE_CANDIDATE_STAGES:raise ValidationError('Недопустимый статус кандидата.')
            validate_documents(files)
            for field in ('full_name','phone','status','ready_on','notes'):setattr(c,field,values[field])
            c.pool_candidate=ensure_pool_candidate(user,row,c,values.get('pool_candidate'))
            if c.pool_candidate_id:
                if not c.pool_candidate.is_active and not pk:raise ValidationError('Этот кандидат уже недоступен в общем пуле.')
                occupied=RecruitmentCandidate.objects.filter(company=user.company,pool_candidate=c.pool_candidate,
                    status__in=ACTIVE_CANDIDATE_STAGES,request__cancelled_at__isnull=True,
                    request__completed_at__isnull=True).exclude(pk=c.pk).exclude(request=row).exists()
                if occupied:raise ValidationError(f'Кандидат {c.pool_candidate.full_name} уже участвует в другой действующей заявке.')
            identity=normalized_candidate_identity(c.full_name,c.phone)
            for existing in RecruitmentCandidate.objects.filter(company=user.company,request=row).exclude(pk=c.pk):
                same_pool=c.pool_candidate_id and existing.pool_candidate_id==c.pool_candidate_id
                same_identity=identity[0] and normalized_candidate_identity(existing.full_name,existing.phone)==identity
                if same_pool or same_identity:
                    raise ValidationError(f'Кандидат {existing.full_name} уже добавлен в эту заявку.')
            # Manual entry must not bypass the pool lock or recreate a person
            # who is already travelling/arrived through another request.
            if identity[0] and identity[1]:
                possible=RecruitmentCandidate.objects.filter(company=user.company,normalized_name=identity[0],normalized_phone=identity[1]).exclude(pk=c.pk).select_related(
                    'request','arrival__actual_stay','arrival__movement_request')
                for existing in possible:
                    active_request=not existing.request.cancelled_at and not existing.request.completed_at and existing.status in ACTIVE_CANDIDATE_STAGES
                    prep=getattr(existing,'arrival',None)
                    active_arrival=bool(prep and (prep.actually_arrived or prep.movement_request_id and prep.movement_request.status!='CANCELLED'))
                    if active_request or active_arrival:
                        raise ValidationError(f'{existing.full_name} уже участвует в действующей заявке или оформлен на заезд.')
                from apps.resources.models import Employee
                for employee in Employee.objects.filter(company=user.company,is_active=True):
                    if normalized_candidate_identity(employee.display_name,employee.phone)==identity:
                        raise ValidationError(f'{employee.display_name} уже является действующим сотрудником компании.')
            c.revision+=1;c.save();sync_pool_candidate(c)
            for file in files:
                doc=CandidateDocument(company=user.company,candidate=c,uploaded_by=user,original_name=Path(file.name.replace('\\','/')).name[:255],size=file.size)
                doc.file.save(file.name,file,save=False);written.append(doc.file);doc.save()
            audit(user,row,f'Кандидат: {c.get_status_display()}.',c)
            if not sync_auto_closure(user,row):touch(row)
            return c
    except Exception:
        for file in written:file.storage.delete(file.name)
        raise


@transaction.atomic
def handoff(user, candidate_id, revision):
    serialize(user,RECRUIT|DISPATCH)
    c=RecruitmentCandidate.objects.get(company=user.company,pk=candidate_id)
    row=current(user,c.request_id);owned(user,row)
    if row.closed_at and row.close_kind=='FORCED':raise ValidationError('Подбор по заявке закрыт принудительно.')
    c=RecruitmentCandidate.objects.select_for_update().get(pk=c.pk)
    if c.pool_candidate_id and c.pool_candidate.employed_employee_id:
        from .recruitment_employment import employment_confirmed
        if employment_confirmed(c.pool_candidate.employed_employee):
            raise ValidationError('Кандидат уже найден среди трудоустроенных сотрудников ЗУП.')
    existing=ArrivalPreparation.objects.filter(candidate=c).first()
    if existing:return existing
    if c.revision!=revision:raise ValidationError('Кандидата уже изменили. Обновите страницу.')
    if c.status in RECRUITMENT_TERMINAL_STAGES|{'TRANSFERRED','ARRIVAL_APPROVED','ARRIVED'} or not c.ready_on:
        raise ValidationError('Для оформления заезда укажите дату готовности кандидата. Отказанного кандидата передать нельзя.')
    active_preparations=ArrivalPreparation.objects.filter(candidate__request=row).exclude(status='CANCELLED').count()
    if active_preparations>=row.quantity:
        raise ValidationError(f'По заявке уже оформлено требуемое количество сотрудников: {row.quantity}. Остальных кандидатов оставьте в резерве.')
    prep=ArrivalPreparation.objects.create(company=user.company,candidate=c,expected_on=max(c.ready_on,row.required_on),handed_by=user)
    c.status='TRANSFERRED';c.revision+=1;c.save();sync_pool_candidate(c);touch(row)
    audit(user,row,'Передан диспетчеру для подготовки заезда.',c)
    return prep


@transaction.atomic
def prepare_arrival(user, pk, revision, values, *, confirm=False, workflow=False, approval_batch=None):
    serialize(user,DISPATCH)
    prep=ArrivalPreparation.objects.get(company=user.company,pk=pk)
    row=current(user,prep.candidate.request_id)
    prep=ArrivalPreparation.objects.select_for_update().select_related('candidate','actual_stay').get(pk=pk)
    if prep.actually_arrived:
        if confirm:return prep  # Repeated POST must not create another actual stay.
        raise ValidationError('Заезд уже подтверждён. Размещение изменяется через историю сотрудника.')
    if prep.status=='CANCELLED':raise ValidationError('Подготовка заезда отменена.')
    if prep.revision!=revision:raise ValidationError('Подготовку заезда уже изменили. Обновите страницу.')
    for field in ('expected_on','camp','employee','notes'):setattr(prep,field,values[field])
    if prep.camp_id and not allowed_camps(user.company,row.construction_object).filter(pk=prep.camp_id).exists():raise ValidationError('Местонахождение не связано с объектом заявки.')
    if prep.employee_id and (prep.employee.company_id!=user.company_id or not prep.employee.is_active):raise ValidationError('Выберите действующего сотрудника своей компании.')
    if prep.employee_id and ArrivalPreparation.objects.filter(company=user.company,employee=prep.employee,status='EXPECTED').exclude(pk=pk).exists():raise ValidationError('Этот сотрудник уже привязан к другому ожидаемому заезду.')
    # A cancelled actual stay can be corrected without keeping an obsolete binding.
    if prep.actual_stay_id and prep.actual_stay.cancelled_at:prep.actual_stay=None;prep.status='EXPECTED'
    if workflow:
        if not prep.camp_id:raise ValidationError('Укажите местонахождение заезда.')
        if prep.movement_request_id:raise ValidationError('Заявка на заезд уже отправлена. Следите за её этапами в перевахте.')
        from . import housing
        person=housing.person_for(prep.employee) if prep.employee_id else Person.objects.create(company=user.company,
            full_name=prep.candidate.full_name,phone=prep.candidate.phone)
        request=MovementReviewRequest.objects.create(company=user.company,person=person,kind='ARRIVAL',movement_date=prep.expected_on,to_camp=prep.camp,
            requested_by=user,source='DISPATCHER',status='PENDING_APPROVAL',employment_kind='NEW',planning_position_name=row.position,
            position_name=prep.employee.display_position if prep.employee_id else row.position,construction_object=row.construction_object,site_object=row.construction_object,
            site=row.construction_object.name,base_city=(prep.candidate.pool_candidate.base_city.name if prep.candidate.pool_candidate_id and prep.candidate.pool_candidate.base_city_id else ''),
            phone=(prep.employee.phone if prep.employee_id else '') or prep.candidate.phone,travel_status='Ожидается',
            approval_batch=approval_batch,note=f'Кандидат из Банка сотрудников. Заявка №{row.pk}. '+prep.notes)
        prep.movement_request=request
        from .departure_workflow import create_approval,log
        create_approval(request);log(request,user,'Кандидат из Банка сотрудников отправлен на согласование руководителю')
    elif confirm:
        if not prep.employee_id or not prep.camp_id:raise ValidationError('Укажите оформленного сотрудника и местонахождение. Кандидат сам по себе не является сотрудником.')
        day=values.get('actual_on')
        if not day:raise ValidationError('Укажите фактическую дату заезда.')
        if day<prep.candidate.ready_on:raise ValidationError('Фактический заезд раньше готовности кандидата.')
        from . import housing
        prep.full_clean()
        housing.require_fact_day(user,day)
        existing=CampStay.objects.filter(company=user.company,person__employee=prep.employee,camp=prep.camp,arrived_on=day,cancelled_at__isnull=True).first()
        if existing:
            if ArrivalPreparation.objects.filter(actual_stay=existing).exclude(pk=pk).exists():raise ValidationError('Этот заезд уже подтверждён по другому кандидату.')
        else:
            housing.move(user,'ARRIVAL',prep.camp,day,[],employees=[prep.employee],comment=f'Заявка на подбор №{row.pk}. '+prep.notes)
            existing=CampStay.objects.get(company=user.company,person__employee=prep.employee,camp=prep.camp,arrived_on=day,cancelled_at__isnull=True)
        prep.actual_stay=existing;prep.status='ARRIVED'
        candidate=RecruitmentCandidate.objects.select_for_update().select_related('pool_candidate','request__construction_object').get(pk=prep.candidate_id)
        candidate.status='ARRIVED';candidate.revision+=1;candidate.save(update_fields=['status','revision','updated_at'])
        sync_pool_candidate(candidate)
    prep.revision+=1;prep.save();touch(row)
    audit(user,row,'Заезд отправлен на согласование руководителю.' if workflow else 'Фактический заезд подтверждён.' if confirm else 'Подготовка заезда обновлена.',prep.candidate)
    return prep


@transaction.atomic
def bulk_prepare_arrivals(user, candidate_ids):
    """Create a persistent draft; approval starts only after the dispatcher fills it."""
    serialize(user,DISPATCH)
    try:ids=list(dict.fromkeys(int(value) for value in candidate_ids))
    except (TypeError,ValueError):raise ValidationError('Некорректный список кандидатов.')
    if not ids:raise ValidationError('Выберите хотя бы одного кандидата.')
    candidates=list(RecruitmentCandidate.objects.select_for_update().select_related(
        'request__construction_object','arrival__camp','arrival__employee','pool_candidate').filter(company=user.company,pk__in=ids))
    if len(candidates)!=len(ids):raise ValidationError('Один из выбранных кандидатов больше недоступен.')
    by_id={candidate.pk:candidate for candidate in candidates};batch=uuid4();prepared=[]
    for candidate_id in ids:
        candidate=by_id[candidate_id]
        if candidate.status in RECRUITMENT_TERMINAL_STAGES or not candidate.ready_on:
            raise ValidationError(f'{candidate.full_name}: кандидат не готов к оформлению заезда.')
        prep=handoff(user,candidate.pk,candidate.revision)
        if prep.movement_request_id:raise ValidationError(f'{candidate.full_name}: заявка на заезд уже создана.')
        row=candidate.request
        from . import housing
        person=housing.person_for(prep.employee) if prep.employee_id else Person.objects.create(company=user.company,
            full_name=candidate.full_name,phone=candidate.phone)
        source=candidate.pool_candidate.source_data if candidate.pool_candidate_id else {}
        base_city=candidate.pool_candidate.base_city.name if candidate.pool_candidate_id and candidate.pool_candidate.base_city_id else source.get('base_city','')
        request=MovementReviewRequest.objects.create(company=user.company,person=person,kind='ARRIVAL',movement_date=prep.expected_on,
            to_camp=prep.camp,requested_by=user,source='DISPATCHER',status='OPEN',employment_kind='NEW',approval_batch=batch,
            pool_candidate=candidate.pool_candidate,
            planning_position_name=row.position,position_name=row.position,construction_object=row.construction_object,
            site_object=row.construction_object,site=row.construction_object.name,base_city=base_city,
            phone=(prep.employee.phone if prep.employee_id else '') or candidate.phone,travel_status='Черновик',
            note=f'Кандидат из Банка сотрудников. Заявка №{row.pk}. '+prep.notes)
        prep.movement_request=request;prep.save(update_fields=['movement_request','updated_at']);prepared.append(prep)
        audit(user,row,'Создан черновик пакетной заявки на заезд.',candidate)
    return batch,prepared


@transaction.atomic
def bulk_prepare_pool_arrivals(user,pool_candidate_ids):
    """Create one editable arrival batch directly from transferred pool cards."""
    serialize(user,ARRIVAL_PROCESSORS)
    try:ids=list(dict.fromkeys(int(value) for value in pool_candidate_ids))
    except (TypeError,ValueError):raise ValidationError('Некорректный список сотрудников.')
    if not ids:raise ValidationError('Выберите хотя бы одного сотрудника.')
    pools=list(TalentPoolCandidate.objects.select_for_update().select_related(
        'position','construction_object','camp','base_city').filter(company=user.company,pk__in=ids))
    if len(pools)!=len(ids):raise ValidationError('Один из выбранных сотрудников больше недоступен.')
    by_id={row.pk:row for row in pools}
    active_pool_ids=set(MovementReviewRequest.objects.filter(company=user.company,kind='ARRIVAL').exclude(
        status='CANCELLED').filter(pool_candidate_id__in=ids).values_list('pool_candidate_id',flat=True))
    active_pool_ids.update(MovementReviewRequest.objects.filter(company=user.company,kind='ARRIVAL').exclude(
        status='CANCELLED').filter(recruitment_arrival__candidate__pool_candidate_id__in=ids).values_list(
            'recruitment_arrival__candidate__pool_candidate_id',flat=True))
    batch=uuid4();requests=[]
    for pool_id in ids:
        pool=by_id[pool_id]
        if pool.stage!='TRANSFERRED':raise ValidationError(f'{pool.full_name}: сотрудник ещё не поставлен на заезд.')
        if pool.pk in active_pool_ids:raise ValidationError(f'{pool.full_name}: заявка на заезд уже создана.')
        application=RecruitmentCandidate.objects.select_for_update().select_related(
            'request__construction_object','arrival__movement_request').filter(company=user.company,pool_candidate=pool).order_by(
                '-created_at','-pk').first()
        prep=getattr(application,'arrival',None) if application else None
        if prep and (prep.status=='CANCELLED' or prep.movement_request_id):prep=None
        construction_object=pool.construction_object or (application.request.construction_object if application else None)
        position=(pool.position.name if pool.position_id else
            application.request.position if application else (pool.source_data or {}).get('position',''))
        camp=pool.camp or (prep.camp if prep else None)
        movement_date=pool.ready_for_arrival_on or (application.ready_on if application else None) or timezone.localdate()
        person=Person.objects.create(company=user.company,full_name=pool.full_name,phone=pool.phone)
        request=MovementReviewRequest.objects.create(company=user.company,person=person,pool_candidate=pool,kind='ARRIVAL',
            movement_date=movement_date,to_camp=camp,requested_by=user,source='DISPATCHER',status='OPEN',employment_kind='NEW',
            approval_batch=batch,planning_position_name=position,position_name=position,construction_object=construction_object,
            site_object=construction_object,site=construction_object.name if construction_object else '',
            base_city=pool.base_city.name if pool.base_city_id else (pool.source_data or {}).get('base_city',''),phone=pool.phone,
            travel_status='Черновик',note='Сотрудник из Банка сотрудников. '+pool.notes)
        if prep:
            prep.movement_request=request;prep.save(update_fields=['movement_request','updated_at'])
            audit(user,application.request,'Создан черновик пакетной заявки на заезд.',application)
        requests.append(request)
    return batch,requests


@transaction.atomic
def save_bulk_arrival_draft(user,batch,construction_object,camp,row_values,submit=False):
    serialize(user,ARRIVAL_PROCESSORS)
    requests=list(MovementReviewRequest.objects.select_for_update().filter(company=user.company,requested_by=user,
        approval_batch=batch,kind='ARRIVAL').select_related('person').order_by('pk'))
    if not requests:raise ValidationError('Черновик пакетной заявки не найден.')
    if any(row.status!='OPEN' for row in requests):raise ValidationError('Заявка уже отправлена на согласование.')
    if construction_object and camp and not allowed_camps(user.company,construction_object).filter(pk=camp.pk).exists():
        raise ValidationError('Выбранное местонахождение не относится к указанному проекту.')
    for row in requests:
        values=row_values[row.pk]
        row.construction_object=construction_object;row.site_object=construction_object
        row.site=construction_object.name if construction_object else ''
        row.to_camp=camp;row.planning_position_name=values.get('planning_position_name','')
        row.position_name=values.get('position_name','');row.base_city=values.get('base_city','')
        row.phone=values.get('phone','');row.movement_date=values.get('movement_date') or row.movement_date
        row.planned_hub_departure_date=values.get('planned_hub_departure_date');row.note=values.get('note','')
        row.travel_status='Ожидается' if submit else 'Черновик';row.status='PENDING_APPROVAL' if submit else 'OPEN'
        row.save()
        ArrivalPreparation.objects.filter(movement_request=row).update(camp=camp,expected_on=row.movement_date)
    if submit:
        from .departure_workflow import create_approval,log
        for row in requests:
            create_approval(row);log(row,user,'Пакетная заявка на заезд отправлена на согласование')
    return requests


@transaction.atomic
def cancel_bulk_arrival_draft(user,batch,reason):
    serialize(user,ARRIVAL_PROCESSORS);reason=reason.strip()
    if not reason:raise ValidationError('Укажите причину отмены черновика.')
    requests=list(MovementReviewRequest.objects.select_for_update().filter(company=user.company,requested_by=user,
        approval_batch=batch,kind='ARRIVAL').order_by('pk'))
    if not requests:raise ValidationError('Черновик пакетной заявки не найден.')
    if any(row.status!='OPEN' for row in requests):raise ValidationError('Отменить можно только неотправленный черновик.')
    from .departure_workflow import log,restore_recommendation
    for row in requests:
        row.status='CANCELLED';row.cancellation_reason=reason;row.cancelled_by=user;row.cancelled_at=timezone.now();row.travel_status='Черновик отменён'
        row.save(update_fields=['status','cancellation_reason','cancelled_by','cancelled_at','travel_status','updated_at'])
        restore_recommendation(row);log(row,user,'Пакетный черновик отменён',reason)
    return requests


@transaction.atomic
def cancel_preparation(user, pk, revision, reason):
    serialize(user,DISPATCH)
    prep=ArrivalPreparation.objects.get(company=user.company,pk=pk);row=current(user,prep.candidate.request_id)
    prep=ArrivalPreparation.objects.select_for_update().select_related('actual_stay').get(pk=pk)
    if prep.revision!=revision:raise ValidationError('Подготовку заезда уже изменили.')
    if prep.actually_arrived:raise ValidationError('Заезд подтверждён. Ошибочное размещение отменяется через историю сотрудника.')
    if not reason.strip():raise ValidationError('Укажите причину отмены заезда.')
    prep.status='CANCELLED';prep.revision+=1;prep.save();touch(row)
    candidate=RecruitmentCandidate.objects.select_for_update().get(pk=prep.candidate_id)
    candidate.status='WITHDRAWN';candidate.revision+=1;candidate.save();sync_pool_candidate(candidate)
    audit(user,row,'Подготовка заезда отменена: '+reason.strip(),prep.candidate)
    return prep
