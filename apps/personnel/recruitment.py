"""Company-scoped transitions. Company serialization also works with SQLite."""
from pathlib import Path
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import F
from django.utils import timezone
from core.permissions import require_roles
from .models import (RecruitmentRequest, RecruitmentCandidate, ArrivalPreparation,
                     CandidateDocument, RecruitmentEvent, CampStay,MovementReviewRequest)
from .models import Person

DISPATCH = {'ADMIN','HR_HEAD','PERSONNEL_DISPATCHER'}
RECRUIT = {'ADMIN','HR_HEAD','RECRUITER'}
READ = DISPATCH | RECRUIT


def serialize(user, roles):
    require_roles(user, roles)
    from apps.accounts.models import Company
    Company.objects.filter(pk=user.company_id).update(name=F('name'))


def current(user, pk, revision=None):
    row = RecruitmentRequest.objects.select_for_update().get(company=user.company, pk=pk)
    if revision is not None and row.revision != revision:raise ValidationError('Заявка уже изменена. Обновите страницу.')
    if row.cancelled_at:raise ValidationError('Заявка отменена.')
    if row.completed_at:raise ValidationError('Заявка выполнена. Требуемое количество сотрудников уже прибыло.')
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
    shortlisted=[c for c in candidates if c.status not in {'REJECTED','WITHDRAWN'}]
    ready=[];arrived=[]
    for c in shortlisted:
        prep=getattr(c,'arrival',None)
        if prep and prep.actually_arrived:arrived.append(c)
        if c.status in {'READY','TRANSFERRED'} and not (prep and prep.status=='CANCELLED'):ready.append(c)
    state=('Отменена' if row.cancelled_at else 'Выполнена' if row.completed_at or len(arrived)>=row.quantity else
           'Кандидаты готовы' if len(ready)>=row.quantity else 'В работе' if row.assigned_to_id or candidates else 'Новая')
    return {'required':row.quantity,'shortlisted':len(shortlisted),'ready':len(ready),'arrived':len(arrived),'label':state}


@transaction.atomic
def save_request(user, values, pk=None, revision=None):
    serialize(user,DISPATCH)
    row=current(user,pk,revision) if pk else RecruitmentRequest(company=user.company,created_by=user)
    for field in ('construction_object','position','quantity','required_on','requirements'):setattr(row,field,values[field])
    row.position=' '.join(row.position.split())
    if not row.position:raise ValidationError('Укажите должность.')
    # Changing the destination after handing candidates off would strand preparations.
    if pk and ArrivalPreparation.objects.filter(candidate__request=row).exists():
        before=RecruitmentRequest.objects.get(pk=pk)
        if before.construction_object_id!=row.construction_object_id:raise ValidationError('После передачи кандидатов объект менять нельзя. Создайте другую заявку.')
    row.revision+=1;row.save()
    audit(user,row,'Заявка изменена.' if pk else 'Создана заявка на подбор.')
    return row


@transaction.atomic
def take_request(user, pk, revision):
    serialize(user,RECRUIT);row=current(user,pk,revision)
    if row.assigned_to_id and row.assigned_to_id!=user.pk:raise ValidationError('Заявку уже взял другой специалист.')
    row.assigned_to=user;row.save(update_fields=['assigned_to','updated_at']);touch(row)
    audit(user,row,'Заявка взята в работу.')
    return row


def owned(user,row):
    from core.permissions import role_code
    if role_code(user)=='RECRUITER' and row.assigned_to_id!=user.pk:
        raise ValidationError('Сначала возьмите заявку в работу. Чужую заявку ведёт назначенный специалист.')


@transaction.atomic
def cancel_request(user, pk, revision, reason):
    serialize(user,DISPATCH);row=current(user,pk,revision)
    if not reason.strip():raise ValidationError('Укажите причину отмены.')
    row.cancelled_at=timezone.now();row.revision+=1;row.save()
    for prep in ArrivalPreparation.objects.select_for_update().filter(candidate__request=row,status='EXPECTED'):
        prep.status='CANCELLED';prep.revision+=1;prep.save()
    audit(user,row,'Заявка отменена: '+reason.strip())
    return row


def validate_documents(files):
    if len(files)>10:raise ValidationError('За один раз можно добавить до 10 файлов.')
    for file in files:
        if Path(file.name).suffix.lower() not in {'.pdf','.doc','.docx','.txt','.jpg','.jpeg','.png'}:
            raise ValidationError('Резюме и документы: PDF, DOC, DOCX, TXT, JPG или PNG.')
        if not file.size or file.size>10*1024*1024:raise ValidationError('Размер каждого файла — от 1 байта до 10 МБ.')


def normalized_candidate_identity(full_name,phone):
    name=' '.join((full_name or '').split()).casefold()
    digits=''.join(character for character in (phone or '') if character.isdigit())
    return name,digits


def save_candidate(user, request_id, values, pk=None, revision=None, files=()):
    # Cleanup private blobs even if the enclosing DB operation rolls back.
    written=[]
    try:
        with transaction.atomic():
            serialize(user,RECRUIT);row=current(user,request_id);owned(user,row)
            c=RecruitmentCandidate.objects.select_for_update().get(company=user.company,request=row,pk=pk) if pk else RecruitmentCandidate(company=user.company,request=row,created_by=user)
            if pk and c.revision!=revision:raise ValidationError('Кандидата уже изменили. Обновите страницу.')
            if pk and ArrivalPreparation.objects.filter(candidate=c).exists():raise ValidationError('Кандидат уже передан. Изменения заезда оформляет диспетчер.')
            if values['status'] not in {'NEW','SCREENING','READY','REJECTED','WITHDRAWN'}:raise ValidationError('Недопустимый статус кандидата.')
            validate_documents(files)
            c.pool_candidate=values.get('pool_candidate')
            for field in ('full_name','phone','status','ready_on','notes'):setattr(c,field,values[field])
            if c.pool_candidate_id:
                if not c.pool_candidate.is_active:raise ValidationError('Этот кандидат уже недоступен в общем пуле.')
                occupied=RecruitmentCandidate.objects.filter(company=user.company,pool_candidate=c.pool_candidate,
                    status__in=['NEW','SCREENING','READY','TRANSFERRED'],request__cancelled_at__isnull=True,
                    request__completed_at__isnull=True).exclude(pk=c.pk).exists()
                if occupied:raise ValidationError(f'Кандидат {c.pool_candidate.full_name} уже участвует в другой действующей заявке.')
            identity=normalized_candidate_identity(c.full_name,c.phone)
            for existing in RecruitmentCandidate.objects.filter(company=user.company,request=row).exclude(pk=c.pk):
                same_pool=c.pool_candidate_id and existing.pool_candidate_id==c.pool_candidate_id
                same_identity=identity[0] and normalized_candidate_identity(existing.full_name,existing.phone)==identity
                if same_pool or same_identity:
                    raise ValidationError(f'Кандидат {existing.full_name} уже добавлен в эту заявку.')
            c.revision+=1;c.save()
            for file in files:
                doc=CandidateDocument(company=user.company,candidate=c,uploaded_by=user,original_name=Path(file.name.replace('\\','/')).name[:255],size=file.size)
                doc.file.save(file.name,file,save=False);written.append(doc.file);doc.save()
            touch(row);audit(user,row,f'Кандидат: {c.get_status_display()}.',c)
            return c
    except Exception:
        for file in written:file.storage.delete(file.name)
        raise


@transaction.atomic
def handoff(user, candidate_id, revision):
    serialize(user,RECRUIT|DISPATCH)
    c=RecruitmentCandidate.objects.get(company=user.company,pk=candidate_id)
    row=current(user,c.request_id);owned(user,row)
    c=RecruitmentCandidate.objects.select_for_update().get(pk=c.pk)
    existing=ArrivalPreparation.objects.filter(candidate=c).first()
    if existing:return existing
    if c.revision!=revision:raise ValidationError('Кандидата уже изменили. Обновите страницу.')
    if c.status in {'REJECTED','WITHDRAWN','TRANSFERRED'} or not c.ready_on:
        raise ValidationError('Для оформления заезда укажите дату готовности кандидата. Отказанного кандидата передать нельзя.')
    active_preparations=ArrivalPreparation.objects.filter(candidate__request=row).exclude(status='CANCELLED').count()
    if active_preparations>=row.quantity:
        raise ValidationError(f'По заявке уже оформлено требуемое количество сотрудников: {row.quantity}. Остальных кандидатов оставьте в резерве.')
    prep=ArrivalPreparation.objects.create(company=user.company,candidate=c,expected_on=max(c.ready_on,row.required_on),handed_by=user)
    c.status='TRANSFERRED';c.revision+=1;c.save();touch(row)
    audit(user,row,'Передан диспетчеру для подготовки заезда.',c)
    return prep


@transaction.atomic
def prepare_arrival(user, pk, revision, values, *, confirm=False, workflow=False):
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
    if prep.camp_id and not allowed_camps(user.company,row.construction_object).filter(pk=prep.camp_id).exists():raise ValidationError('ВЖГ не связан с объектом заявки.')
    if prep.employee_id and (prep.employee.company_id!=user.company_id or not prep.employee.is_active):raise ValidationError('Выберите действующего сотрудника своей компании.')
    if prep.employee_id and ArrivalPreparation.objects.filter(company=user.company,employee=prep.employee,status='EXPECTED').exclude(pk=pk).exists():raise ValidationError('Этот сотрудник уже привязан к другому ожидаемому заезду.')
    # A cancelled actual stay can be corrected without keeping an obsolete binding.
    if prep.actual_stay_id and prep.actual_stay.cancelled_at:prep.actual_stay=None;prep.status='EXPECTED'
    if workflow:
        if not prep.camp_id:raise ValidationError('Укажите ВЖГ заезда.')
        if prep.movement_request_id:raise ValidationError('Заявка на заезд уже отправлена. Следите за её этапами в перевахте.')
        from . import housing
        person=housing.person_for(prep.employee) if prep.employee_id else Person.objects.create(company=user.company,
            full_name=prep.candidate.full_name,phone=prep.candidate.phone)
        request=MovementReviewRequest.objects.create(company=user.company,person=person,kind='ARRIVAL',movement_date=prep.expected_on,to_camp=prep.camp,
            requested_by=user,source='DISPATCHER',status='PENDING_APPROVAL',employment_kind='NEW',planning_position_name=row.position,
            position_name=prep.employee.display_position if prep.employee_id else row.position,construction_object=row.construction_object,site_object=row.construction_object,
            site=row.construction_object.name,phone=(prep.employee.phone if prep.employee_id else '') or prep.candidate.phone,travel_status='Ожидается',note=f'Кандидат из Магазина подбора. Заявка №{row.pk}. '+prep.notes)
        prep.movement_request=request
        from .departure_workflow import create_approval,log
        create_approval(request);log(request,user,'Кандидат из Магазина подбора отправлен на согласование руководителю')
    elif confirm:
        if not prep.employee_id or not prep.camp_id:raise ValidationError('Укажите оформленного сотрудника и ВЖГ. Кандидат сам по себе не является сотрудником.')
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
    prep.revision+=1;prep.save();touch(row)
    audit(user,row,'Заезд отправлен на согласование руководителю.' if workflow else 'Фактический заезд подтверждён.' if confirm else 'Подготовка заезда обновлена.',prep.candidate)
    return prep


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
    candidate.status='WITHDRAWN';candidate.revision+=1;candidate.save()
    audit(user,row,'Подготовка заезда отменена: '+reason.strip(),prep.candidate)
    return prep
