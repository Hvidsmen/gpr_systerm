"""Reconcile recruitment candidates with authoritative active ZUP employees."""
from collections import defaultdict
from django.db import transaction
from django.utils import timezone
from apps.resources.models import Employee
from .models import ArrivalPreparation,TalentPoolCandidate,TalentPoolStageEvent


def normalized_name(value):return ' '.join(str(value or '').split()).casefold()


def normalized_phone(value):
    digits=''.join(character for character in str(value or '') if character.isdigit())
    return digits[-10:] if len(digits)>=10 else digits


def normalized_date(value):
    if not value:return ''
    return value.isoformat() if hasattr(value,'isoformat') else str(value)[:10]


@transaction.atomic
def reconcile_company(company,actor):
    """Move unambiguous ZUP matches to the system-managed EMPLOYED stage."""
    employees=list(Employee.objects.filter(company=company,is_active=True).exclude(zup_ref='').order_by('pk'))
    employee_by_id={row.pk:row for row in employees}
    candidates=list(TalentPoolCandidate.objects.select_for_update().filter(company=company).order_by('pk'))
    already={row.employed_employee_id for row in candidates if row.employed_employee_id}
    proposals={}

    direct=defaultdict(set)
    for pool_id,employee_id in ArrivalPreparation.objects.filter(company=company,candidate__pool_candidate__isnull=False,
            employee_id__in=employee_by_id).values_list('candidate__pool_candidate_id','employee_id'):
        direct[pool_id].add(employee_id)
    for candidate in candidates:
        matches=direct.get(candidate.pk,set())
        if len(matches)==1:proposals[candidate.pk]=(employee_by_id[next(iter(matches))],'оформление заезда')

    employees_by_name=defaultdict(list)
    for employee in employees:employees_by_name[normalized_name(employee.display_name)].append(employee)
    for candidate in candidates:
        if candidate.employed_employee_id or candidate.pk in proposals:continue
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

    by_employee=defaultdict(list)
    for candidate_id,(employee,method) in proposals.items():
        if employee.pk not in already:by_employee[employee.pk].append((candidate_id,method))
    accepted={rows[0][0]:(employee_by_id[employee_id],rows[0][1]) for employee_id,rows in by_employee.items() if len(rows)==1}
    linked=0;now=timezone.now()
    for candidate in candidates:
        match=accepted.get(candidate.pk)
        if not match:continue
        employee,method=match;previous=candidate.stage;source=dict(candidate.source_data or {})
        source['employment_match']={'employee_id':employee.pk,'employee_number':employee.employee_number,'zup_ref':employee.zup_ref,
            'matched_by':method,'matched_at':now.isoformat()}
        candidate.stage='EMPLOYED';candidate.is_active=False;candidate.employed_employee=employee
        candidate.employed_at=employee.zup_synced_at or now;candidate.source_data=source
        candidate.archived_at=None;candidate.archived_by=None
        candidate.save(update_fields=['stage','is_active','employed_employee','employed_at','source_data','archived_at','archived_by','updated_at'])
        if previous!='EMPLOYED':
            TalentPoolStageEvent.objects.create(company=company,candidate=candidate,from_stage=previous,to_stage='EMPLOYED',actor=actor,
                note=f'Автоматически найден в ЗУП: {employee.display_name}, табельный № {employee.employee_number or "не указан"}.')
        already.add(employee.pk);linked+=1
    return {'linked':linked,'ambiguous':sum(len(rows)>1 for rows in by_employee.values())}
