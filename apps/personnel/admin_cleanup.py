"""Explicit, company-scoped cleanup tools for demo environments."""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.shortcuts import redirect,render
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from core.permissions import role_code
from apps.audit.models import AuditLog
from .models import DISPATCHER_BULK_CLEANUP_REASON,MovementRecommendationOverride,MovementReviewEvent,MovementReviewRequest,Person,ScheduleChangeRequest,StaffingSlot
from .recruitment_models import ArrivalPreparation,CandidateDocument,RecruitmentCandidate,RecruitmentEvent,RecruitmentRequest,TalentPoolCandidate


CONFIRMATION='ОЧИСТИТЬ'


def require_admin(user):
    if not (user.is_superuser or role_code(user)=='ADMIN'):
        raise PermissionDenied('Администрирование персонала доступно только администратору.')


def counts(company):
    active_people=Person.objects.filter(company=company,dispatcher_removed_at=None)
    assigned_removed_people=StaffingSlot.objects.filter(company=company,person__isnull=False,
        person__dispatcher_removed_at__isnull=False)
    vacancies=StaffingSlot.objects.filter(company=company,person__isnull=True)
    return {
        'pool':TalentPoolCandidate.objects.filter(company=company).count(),
        'requests':(
            RecruitmentRequest.objects.filter(company=company).count()+
            MovementReviewRequest.objects.filter(company=company).count()+
            ScheduleChangeRequest.objects.filter(company=company).count()
        ),
        # Count both roster cards and empty staffing rows. This keeps the
        # action available after an earlier cleanup left only vacancies.
        'assignments':active_people.count()+assigned_removed_people.count()+vacancies.count(),
    }


def audit(user,action,deleted):
    AuditLog.objects.create(user=user,action='DELETE',entity_type='personnel.DemoCleanup',entity_id=action,
        old_value={'deleted':deleted},comment='Административная очистка демоданных персонала')


@login_required
@require_http_methods(['GET','POST'])
def cleanup(request):
    require_admin(request.user)
    if request.method=='POST':
        action=request.POST.get('cleanup_action','')
        if request.POST.get('confirmation','').strip()!=CONFIRMATION:
            messages.error(request,f'Для подтверждения введите слово «{CONFIRMATION}».')
            return redirect('personnel:admin_cleanup')
        handlers={'pool':clear_pool,'requests':clear_requests,'assignments':clear_assignments}
        if action not in handlers:raise PermissionDenied('Неизвестная операция очистки.')
        with transaction.atomic():
            deleted=(handlers[action](request.user.company,request.user) if action=='assignments'
                else handlers[action](request.user.company))
            audit(request.user,action,deleted)
        messages.success(request,f'Очистка завершена. Обработано записей: {deleted}.')
        return redirect('personnel:admin_cleanup')
    return render(request,'personnel/admin_cleanup.html',{'personnel_section':'administration','cleanup_counts':counts(request.user.company),'confirmation_word':CONFIRMATION})


def clear_pool(company):
    pool=TalentPoolCandidate.objects.filter(company=company)
    total=pool.count()
    # Vacancy history remains readable, but no longer blocks removal of the
    # reusable common pool.
    RecruitmentCandidate.objects.filter(company=company,pool_candidate__isnull=False).update(pool_candidate=None)
    pool.delete()
    return total


def clear_requests(company):
    from apps.planning.models import WorkTask
    schedules=ScheduleChangeRequest.objects.filter(company=company)
    task_keys=list(schedules.exclude(task_key='').values_list('task_key',flat=True))
    movement_tasks=WorkTask.objects.filter(company=company,key__startswith='personnel-movement-')
    schedule_tasks=WorkTask.objects.filter(company=company,key__in=task_keys)
    total=(RecruitmentRequest.objects.filter(company=company).count()+MovementReviewRequest.objects.filter(company=company).count()+schedules.count())
    movement_tasks.delete();schedule_tasks.delete()
    ArrivalPreparation.objects.filter(company=company).delete()
    CandidateDocument.objects.filter(company=company).delete()
    RecruitmentEvent.objects.filter(company=company).delete()
    RecruitmentCandidate.objects.filter(company=company).delete()
    RecruitmentRequest.objects.filter(company=company).delete()
    MovementReviewEvent.objects.filter(company=company).delete()
    MovementReviewRequest.objects.filter(company=company).delete()
    MovementRecommendationOverride.objects.filter(company=company).delete()
    schedules.delete()
    return total


def clear_assignments(company,user):
    assigned=StaffingSlot.objects.filter(company=company,person__isnull=False)
    active_people=Person.objects.filter(company=company,dispatcher_removed_at=None)
    vacancies=StaffingSlot.objects.filter(company=company,person__isnull=True)
    # A removed card can still have an old staffing link after an interrupted
    # cleanup. Count it separately so the cleanup action remains available.
    total=(active_people.count()+assigned.filter(person__dispatcher_removed_at__isnull=False).count()+
        vacancies.count())
    Person.objects.filter(company=company).update(schedule_slot=None)
    StaffingSlot.objects.filter(company=company).update(previous_assignment=None)
    # Remove both assignment history and imported empty staffing rows. GPR/SMG
    # plans and position groups are separate records and remain untouched.
    StaffingSlot.objects.filter(company=company).delete()
    # Keep employee master data and WJG history for audit, but remove every
    # active personnel card from the operational dispatcher roster.
    active_people.update(dispatcher_removed_at=timezone.now(),dispatcher_removed_by=user,
        dispatcher_removal_reason=DISPATCHER_BULK_CLEANUP_REASON)
    return total
