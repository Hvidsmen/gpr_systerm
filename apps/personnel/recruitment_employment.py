"""Reconcile recruitment candidates with authoritative active ZUP employees."""
from collections import defaultdict
from datetime import date
from django.db import transaction
from django.utils import timezone
from apps.resources.models import Employee
from .models import ArrivalPreparation,TalentPoolCandidate,TalentPoolStageEvent
from .recruitment_models import POOL_UNAVAILABLE_STAGES

REVIEW_KEY='employment_review'


def normalized_name(value):return ' '.join(str(value or '').split()).casefold()


def normalized_phone(value):
    digits=''.join(character for character in str(value or '') if character.isdigit())
    return digits[-10:] if len(digits)>=10 else digits


def normalized_date(value):
    if not value:return ''
    return value.isoformat() if hasattr(value,'isoformat') else str(value)[:10]


def confirmed_hire_date(employee,on_date=None):
    """A ZUP directory card is not employment until its hire date has started."""
    raw=(employee.zup_card or {}).get('hire_date')
    if isinstance(raw,date):hire_date=raw
    else:
        try:hire_date=date.fromisoformat(str(raw or '')[:10])
        except (TypeError,ValueError):return None
    return hire_date if hire_date<=(on_date or timezone.localdate()) else None


def employment_confirmed(employee,on_date=None):return bool(employee and confirmed_hire_date(employee,on_date))


def review_employee_ids(candidate):
    review=(candidate.source_data or {}).get(REVIEW_KEY) or {}
    return [value for value in review.get('employee_ids',[]) if isinstance(value,int)]


def review_employees(candidate):
    """Return still available ZUP choices saved during the latest reconciliation."""
    ids=review_employee_ids(candidate)
    if not ids:return []
    rows=Employee.objects.filter(company_id=candidate.company_id,is_active=True,pk__in=ids).exclude(zup_ref='').select_related('position')
    linked=set(TalentPoolCandidate.objects.filter(company_id=candidate.company_id,employed_employee_id__in=ids).values_list('employed_employee_id',flat=True))
    by_id={row.pk:row for row in rows if row.pk not in linked}
    return [by_id[pk] for pk in ids if pk in by_id]


def _set_review(candidate,employees,reason,now):
    source=dict(candidate.source_data or {});employee_ids=sorted({row.pk for row in employees})
    current=source.get(REVIEW_KEY) or {}
    review={'employee_ids':employee_ids,'reason':reason,
        'detected_at':current.get('detected_at') if current.get('employee_ids')==employee_ids else now.isoformat()}
    if current==review:return False
    source[REVIEW_KEY]=review;candidate.source_data=source
    candidate.save(update_fields=['source_data','updated_at']);return True


def _clear_review(candidate):
    source=dict(candidate.source_data or {})
    if not source.pop(REVIEW_KEY,None):return False
    candidate.source_data=source;candidate.save(update_fields=['source_data','updated_at']);return True


def link_candidate(candidate,employee,actor,method):
    """Persist an authoritative candidate-to-ZUP link and its audit event."""
    if candidate.company_id!=employee.company_id or not employee.is_active or not employee.zup_ref:
        raise ValueError('Выбранный сотрудник ЗУП недоступен.')
    if TalentPoolCandidate.objects.filter(company_id=candidate.company_id,employed_employee=employee).exclude(pk=candidate.pk).exists():
        raise ValueError('Этот сотрудник ЗУП уже связан с другой карточкой кандидата.')
    now=timezone.now();previous=candidate.stage;source=dict(candidate.source_data or {})
    hire_date=confirmed_hire_date(employee);confirmed=bool(hire_date)
    source.pop(REVIEW_KEY,None)
    source['employment_match']={'employee_id':employee.pk,'employee_number':employee.employee_number,'zup_ref':employee.zup_ref,
        'matched_by':method,'matched_at':now.isoformat(),'hire_date':hire_date.isoformat() if hire_date else '',
        'employment_confirmed':confirmed}
    candidate.employed_employee=employee;candidate.source_data=source
    if confirmed:
        candidate.stage='EMPLOYED';candidate.is_active=False;candidate.employed_at=employee.zup_synced_at or now
    else:candidate.employed_at=None
    candidate.archived_at=None;candidate.archived_by=None
    candidate.save(update_fields=['stage','is_active','employed_employee','employed_at','source_data','archived_at','archived_by','updated_at'])
    if confirmed and previous!='EMPLOYED':
        prefix='Автоматически найден' if method!='ручная проверка соответствия' else 'Связан после ручной проверки'
        TalentPoolStageEvent.objects.create(company=candidate.company,candidate=candidate,from_stage=previous,to_stage='EMPLOYED',actor=actor,
            note=f'{prefix} в ЗУП: {employee.display_name}, табельный № {employee.employee_number or "не указан"}.')
    elif not confirmed:
        TalentPoolStageEvent.objects.create(company=candidate.company,candidate=candidate,from_stage=previous,to_stage=previous,actor=actor,
            note=f'Найдена предварительная карточка ЗУП: {employee.display_name}, табельный № {employee.employee_number or "не указан"}. Факт приёма не подтверждён — этап воронки сохранён.')
    return candidate


def sync_linked_stage(candidate,actor):
    """Mirror the authoritative active/dismissed state of a linked ZUP card."""
    employee=candidate.employed_employee
    if not employee:return False
    hire_date=confirmed_hire_date(employee);source=dict(candidate.source_data or {})
    match=dict(source.get('employment_match') or {})
    match.update({'employee_id':employee.pk,'employee_number':employee.employee_number,'zup_ref':employee.zup_ref,
        'hire_date':hire_date.isoformat() if hire_date else '','employment_confirmed':bool(hire_date)})
    source['employment_match']=match
    if not hire_date:
        previous=candidate.stage
        target=previous
        if previous in {'EMPLOYED','DISMISSED'}:
            event=candidate.stage_events.filter(to_stage='EMPLOYED').exclude(from_stage__in={'EMPLOYED','DISMISSED'}).order_by('-created_at','-pk').first()
            target=event.from_stage if event else 'SELECTED'
        changed=previous!=target or candidate.employed_at is not None or candidate.source_data!=source
        if not changed:return False
        candidate.stage=target;candidate.is_active=target not in POOL_UNAVAILABLE_STAGES
        candidate.employed_at=None;candidate.source_data=source
        candidate.save(update_fields=['stage','is_active','employed_at','source_data','updated_at'])
        if previous!=target:
            TalentPoolStageEvent.objects.create(company=candidate.company,candidate=candidate,from_stage=previous,to_stage=target,actor=actor,
                note='Скорректировано по ЗУП: есть карточка и табельный номер, но факт приёма не подтверждён. Восстановлен предыдущий этап воронки.')
        return True
    target='EMPLOYED' if employee.is_active else 'DISMISSED'
    if candidate.stage==target and not candidate.is_active and candidate.source_data==source:return False
    previous=candidate.stage;candidate.stage=target;candidate.is_active=False
    candidate.employed_at=candidate.employed_at or employee.zup_synced_at or timezone.now();candidate.source_data=source
    candidate.save(update_fields=['stage','is_active','employed_at','source_data','updated_at'])
    if previous!=target:
        dismissal=(employee.zup_card or {}).get('dismissal_date')
        note=('Автоматически по данным ЗУП: сотрудник снова действует.' if target=='EMPLOYED' else
            'Автоматически по данным ЗУП: сотрудник уволен'+(f' {dismissal}.' if dismissal else '.'))
        TalentPoolStageEvent.objects.create(company=candidate.company,candidate=candidate,from_stage=previous,
            to_stage=target,actor=actor,note=note)
    return True


def remove_employee_from_other_reviews(company,employee_id,except_candidate_id):
    """After a manual choice, keep only still possible choices on duplicate cards."""
    possible=TalentPoolCandidate.objects.filter(company=company,employed_employee__isnull=True).exclude(pk=except_candidate_id).values_list('pk','source_data')
    ids=[pk for pk,source in possible if employee_id in ((source or {}).get(REVIEW_KEY) or {}).get('employee_ids',[])]
    rows=TalentPoolCandidate.objects.select_for_update().filter(pk__in=ids)
    for row in rows:
        ids=review_employee_ids(row)
        if employee_id not in ids:continue
        remaining=[value for value in ids if value!=employee_id];source=dict(row.source_data or {})
        if remaining:source[REVIEW_KEY]={**source[REVIEW_KEY],'employee_ids':remaining}
        else:source.pop(REVIEW_KEY,None)
        row.source_data=source;row.save(update_fields=['source_data','updated_at'])


@transaction.atomic
def reconcile_company(company,actor):
    """Move unambiguous ZUP matches and retain ambiguous choices for manual review."""
    employees=list(Employee.objects.filter(company=company,is_active=True).exclude(zup_ref='').order_by('pk'))
    employee_by_id={row.pk:row for row in employees}
    candidates=list(TalentPoolCandidate.objects.select_for_update().filter(company=company).select_related(
        'company','employed_employee').order_by('pk'))
    for candidate in candidates:
        if candidate.employed_employee_id:sync_linked_stage(candidate,actor)
    already={row.employed_employee_id for row in candidates if row.employed_employee_id}
    proposals={};reviews={};ambiguous_groups=0

    direct=defaultdict(set)
    for pool_id,employee_id in ArrivalPreparation.objects.filter(company=company,candidate__pool_candidate__isnull=False,
            employee_id__in=employee_by_id).values_list('candidate__pool_candidate_id','employee_id'):
        direct[pool_id].add(employee_id)
    for candidate in candidates:
        matches=direct.get(candidate.pk,set())
        if len(matches)==1:proposals[candidate.pk]=(employee_by_id[next(iter(matches))],'оформление заезда')
        elif len(matches)>1:
            reviews[candidate.pk]=([employee_by_id[pk] for pk in matches],'Несколько сотрудников ЗУП связаны с оформлением заезда.')
            ambiguous_groups+=1

    employees_by_name=defaultdict(list)
    for employee in employees:employees_by_name[normalized_name(employee.display_name)].append(employee)
    for candidate in candidates:
        if candidate.employed_employee_id or candidate.pk in proposals or candidate.pk in reviews:continue
        matches=employees_by_name.get(normalized_name(candidate.full_name),[])
        method='точное ФИО'
        if len(matches)>1:
            phone=normalized_phone(candidate.phone)
            phone_matches=[row for row in matches if phone and normalized_phone(row.phone)==phone]
            if phone_matches:matches=phone_matches;method='ФИО и телефон'
        if len(matches)>1:
            birth=normalized_date((candidate.source_data or {}).get('birth_date'))
            birth_matches=[row for row in matches if birth and normalized_date((row.zup_card or {}).get('birth_date'))==birth]
            if birth_matches:matches=birth_matches;method='ФИО и дата рождения'
        if len(matches)==1:proposals[candidate.pk]=(matches[0],method)
        elif len(matches)>1:
            reviews[candidate.pk]=(matches,'В ЗУП найдено несколько сотрудников с одинаковыми данными.')
            ambiguous_groups+=1

    by_employee=defaultdict(list)
    for candidate_id,(employee,method) in proposals.items():
        if employee.pk not in already:by_employee[employee.pk].append((candidate_id,method))
    accepted={rows[0][0]:(employee_by_id[employee_id],rows[0][1]) for employee_id,rows in by_employee.items() if len(rows)==1}
    for employee_id,rows in by_employee.items():
        if len(rows)<2:continue
        ambiguous_groups+=1
        employee=employee_by_id[employee_id]
        for candidate_id,_method in rows:reviews[candidate_id]=([employee],'Несколько карточек кандидатов имеют одинаковые данные.')
    linked=0;now=timezone.now()
    for candidate in candidates:
        match=accepted.get(candidate.pk)
        if match:
            employee,method=match;link_candidate(candidate,employee,actor,method);already.add(employee.pk);linked+=1
        elif candidate.employed_employee_id:continue
        elif candidate.pk in reviews:_set_review(candidate,*reviews[candidate.pk],now)
        else:_clear_review(candidate)
    return {'linked':linked,'ambiguous':ambiguous_groups}
