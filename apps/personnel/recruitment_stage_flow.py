"""Sequential approval route and SLA calculation for recruitment candidates."""
from datetime import timedelta

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from .recruitment_models import (
    RECRUITMENT_APPROVAL_FLOW,
    TalentPoolCandidate,
    TalentPoolStageEvent,
)


STAGE_RULES={
    'MANAGER_APPROVAL':(3,'calendar'),
    'SECURITY_CHECK':(3,'calendar'),
    'MEDICAL_DOCUMENTS':(10,'working'),
    'PASS_PROCESSING':(10,'working'),
    'READY':(None,None),
}
NEXT_STAGE=dict(zip(RECRUITMENT_APPROVAL_FLOW,RECRUITMENT_APPROVAL_FLOW[1:]))


class WorkingDayCalendar:
    """Company production calendar with a weekday fallback for missing dates."""
    def __init__(self,company_id):
        self.company_id=company_id
        self._years={}

    def _load(self,year):
        if year in self._years:return
        from apps.planning.models import CalendarDay,ProductionCalendar
        calendar_id=ProductionCalendar.objects.filter(company_id=self.company_id,year=year,
            is_default=True).order_by('-pk').values_list('pk',flat=True).first()
        rows=CalendarDay.objects.filter(calendar_id=calendar_id).values_list('date','is_working') if calendar_id else ()
        self._years[year]={day:is_working for day,is_working in rows}

    def is_working(self,day):
        self._load(day.year)
        return self._years[day.year].get(day,day.weekday()<5)

    def add_working_days(self,start,count):
        day=start
        added=0
        while added<count:
            day+=timedelta(days=1)
            if self.is_working(day):added+=1
        return day

    def working_days_between(self,start,end):
        """Working days after start through end; zero for an empty range."""
        if end<=start:return 0
        return sum(self.is_working(start+timedelta(days=offset)) for offset in range(1,(end-start).days+1))


def stage_status(candidate,*,today=None,calendar=None):
    """Return display-ready SLA state for the candidate's current stage."""
    stage=candidate.stage if hasattr(candidate,'stage') else candidate['stage']
    if stage not in STAGE_RULES:return {'managed':False}
    started_at=candidate.stage_started_at if hasattr(candidate,'stage_started_at') else candidate.get('stage_started_at')
    started_on=timezone.localtime(started_at).date() if started_at else (today or timezone.localdate())
    today=today or timezone.localdate()
    limit,kind=STAGE_RULES[stage]
    step=RECRUITMENT_APPROVAL_FLOW.index(stage)+1
    labels=dict(TalentPoolCandidate._meta.get_field('stage').choices)
    result={'managed':True,'stage':stage,'step':step,'steps':len(RECRUITMENT_APPROVAL_FLOW),
        'started_on':started_on,'deadline':None,'overdue':False,'overdue_days':0,'remaining_days':None,
        'next_stage':NEXT_STAGE.get(stage),'next_label':labels.get(NEXT_STAGE.get(stage),''),
        'flow':[{'number':number,'code':code,'label':labels[code],
            'state':'done' if number<step else 'current' if number==step else 'upcoming'}
            for number,code in enumerate(RECRUITMENT_APPROVAL_FLOW,1)],
        'is_final':stage=='READY'}
    if limit is None:
        result.update(label='Маршрут согласования завершён',css='success')
        return result
    if kind=='working':
        calendar=calendar or WorkingDayCalendar(candidate.company_id if hasattr(candidate,'company_id') else candidate['company_id'])
        deadline=calendar.add_working_days(started_on,limit)
        unit='рабочих дней'
        overdue_days=calendar.working_days_between(deadline,today)
        remaining_days=calendar.working_days_between(today,deadline)
        short_unit='раб. дн.'
    else:
        deadline=started_on+timedelta(days=limit)
        unit='дней'
        overdue_days=max(0,(today-deadline).days)
        remaining_days=max(0,(deadline-today).days)
        short_unit='дн.'
    if overdue_days:
        label=f'Просрочено на {overdue_days} {short_unit}';css='danger'
    elif remaining_days==0:
        label='Срок истекает сегодня';css='warning'
    else:
        label=f'Срок до {deadline.strftime("%d.%m.%Y")} · осталось {remaining_days} {short_unit}';css='info'
    result.update(deadline=deadline,overdue=bool(overdue_days),overdue_days=overdue_days,
        remaining_days=remaining_days,label=label,css=css,limit=limit,unit=unit)
    return result


def allowed_manual_stage(current,target):
    """Managed steps cannot be skipped through a generic stage selector."""
    if target not in STAGE_RULES:return True
    if current not in STAGE_RULES:return target==RECRUITMENT_APPROVAL_FLOW[0]
    return target==current


@transaction.atomic
def advance(candidate_id,company,actor,note=''):
    candidate=TalentPoolCandidate.objects.select_for_update().get(pk=candidate_id,company=company)
    next_stage=NEXT_STAGE.get(candidate.stage)
    if not next_stage:
        if candidate.stage=='READY':raise ValidationError('Кандидат уже готов к заезду: маршрут согласования завершён.')
        raise ValidationError('Сначала переведите кандидата на согласование Заказчику.')
    previous=candidate.stage
    today=timezone.localdate()
    update_fields=['stage','is_active','updated_at']
    if previous=='MANAGER_APPROVAL':
        candidate.manager_approved=True;candidate.manager_approved_on=today
        update_fields+=['manager_approved','manager_approved_on']
    elif previous=='SECURITY_CHECK':
        candidate.security_approved=True;candidate.security_approved_on=today
        update_fields+=['security_approved','security_approved_on']
    elif previous=='MEDICAL_DOCUMENTS':
        candidate.medical_exam_passed=True;candidate.medical_exam_on=today
        update_fields+=['medical_exam_passed','medical_exam_on']
    elif previous=='PASS_PROCESSING':
        source=dict(candidate.source_data or {})
        source['pass_approved_on']=today.isoformat()
        candidate.source_data=source;candidate.ready_for_arrival=True;candidate.ready_for_arrival_on=today
        update_fields+=['source_data','ready_for_arrival','ready_for_arrival_on']
    candidate.stage=next_stage
    candidate.save(update_fields=update_fields)
    TalentPoolStageEvent.objects.create(company=company,candidate=candidate,from_stage=previous,
        to_stage=next_stage,actor=actor,note=note.strip() or 'Текущий этап выполнен.')
    return candidate
