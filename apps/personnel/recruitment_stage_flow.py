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
STEP_CONFIRMATIONS={
    'MANAGER_APPROVAL':(('manager_approved','manager_approved_on','согласование Заказчика'),),
    'SECURITY_CHECK':(('security_approved','security_approved_on','согласование СЭБ'),),
    'MEDICAL_DOCUMENTS':(('medical_exam_passed','medical_exam_on','медкомиссия'),),
    'PASS_PROCESSING':(
        ('pass_documents_sent','pass_documents_sent_on','направление документов на пропуск'),
        ('pass_ready','pass_ready_on','готовый пропуск'),
    ),
}
MANUAL_EXIT_STAGES={'RESERVE','REJECTED','WITHDRAWN','NOT_REQUIRED','NO_SHOW'}


def stage_from_confirmation_values(manager,security,medical,pass_documents,pass_ready):
    """Return the first approval step that has not been confirmed yet."""
    if not manager:return 'MANAGER_APPROVAL'
    if not security:return 'SECURITY_CHECK'
    if not medical:return 'MEDICAL_DOCUMENTS'
    if not pass_documents or not pass_ready:return 'PASS_PROCESSING'
    return 'READY'


def stage_from_confirmations(candidate):
    return stage_from_confirmation_values(candidate.manager_approved,candidate.security_approved,
        candidate.medical_exam_passed,candidate.pass_documents_sent,candidate.pass_ready)


def stage_consistency_warning(candidate):
    """Describe legacy approval data without changing it."""
    stage=candidate.stage if hasattr(candidate,'stage') else candidate.get('stage')
    if stage not in RECRUITMENT_APPROVAL_FLOW:return ''
    if not hasattr(candidate,'manager_approved'):return ''
    current_index=RECRUITMENT_APPROVAL_FLOW.index(stage)
    missing=[]
    for step in RECRUITMENT_APPROVAL_FLOW[:current_index]:
        for flag,_,label in STEP_CONFIRMATIONS.get(step,()):
            if not getattr(candidate,flag):missing.append(label)
    if missing:
        return ('Текущий этап не соответствует отметкам: не подтверждено '+', '.join(missing)+
            '. Этап не изменён автоматически. Исправьте отметки или верните карточку на предыдущий этап с указанием причины.')
    completed_ahead=[]
    current_confirmations=STEP_CONFIRMATIONS.get(stage,())
    if current_confirmations and all(getattr(candidate,flag) for flag,_,_ in current_confirmations):
        completed_ahead.extend(label for _,_,label in current_confirmations)
    for step in RECRUITMENT_APPROVAL_FLOW[current_index+1:]:
        for flag,_,label in STEP_CONFIRMATIONS.get(step,()):
            if getattr(candidate,flag):completed_ahead.append(label)
    if completed_ahead:
        return ('Отметки заполнены дальше текущего этапа: '+', '.join(completed_ahead)+
            '. Этап не изменён автоматически; для перехода используйте кнопку «Этап выполнен».')
    return ''


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
        'is_final':stage=='READY','can_advance':stage!='READY','auto_complete':False,
        'consistency_warning':stage_consistency_warning(candidate),
        'return_options':[{'code':code,'label':labels[code]}
            for code in RECRUITMENT_APPROVAL_FLOW[:step-1]]}
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
    if current in STAGE_RULES:
        if target not in STAGE_RULES:return target in MANUAL_EXIT_STAGES
        return target==current
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
    previous_steps=RECRUITMENT_APPROVAL_FLOW[:RECRUITMENT_APPROVAL_FLOW.index(previous)]
    missing_prior=[]
    for step in previous_steps:
        for flag,_,label in STEP_CONFIRMATIONS.get(step,()):
            if not getattr(candidate,flag):missing_prior.append(label)
    if missing_prior:
        raise ValidationError('Нельзя завершить этап: не подтверждено '+', '.join(missing_prior)+'.')
    if previous=='MANAGER_APPROVAL':
        candidate.manager_approved=True;candidate.manager_approved_on=candidate.manager_approved_on or today
        update_fields+=['manager_approved','manager_approved_on']
    elif previous=='SECURITY_CHECK':
        candidate.security_approved=True;candidate.security_approved_on=candidate.security_approved_on or today
        update_fields+=['security_approved','security_approved_on']
    elif previous=='MEDICAL_DOCUMENTS':
        candidate.medical_exam_passed=True;candidate.medical_exam_on=candidate.medical_exam_on or today
        update_fields+=['medical_exam_passed','medical_exam_on']
    elif previous=='PASS_PROCESSING':
        required=(('согласование Заказчика',candidate.manager_approved),('согласование СЭБ',candidate.security_approved),
            ('медкомиссия',candidate.medical_exam_passed),('направление документов на пропуск',candidate.pass_documents_sent),
            ('готовый пропуск',candidate.pass_ready))
        missing=[label for label,complete in required if not complete]
        if missing:raise ValidationError('Не заполнено: '+', '.join(missing)+'.')
        candidate.ready_for_arrival=True;candidate.ready_for_arrival_on=candidate.pass_ready_on
        update_fields+=['ready_for_arrival','ready_for_arrival_on']
    candidate.stage=next_stage
    candidate.save(update_fields=update_fields)
    TalentPoolStageEvent.objects.create(company=company,candidate=candidate,from_stage=previous,
        to_stage=next_stage,actor=actor,note=note.strip() or 'Текущий этап выполнен.')
    return candidate


@transaction.atomic
def return_to_stage(candidate_id,company,actor,target_stage,reason):
    """Return a managed route to an earlier step and invalidate that step and everything after it."""
    reason=reason.strip()
    if not reason:raise ValidationError('Укажите причину возврата на предыдущий этап.')
    candidate=TalentPoolCandidate.objects.select_for_update().get(pk=candidate_id,company=company)
    if candidate.stage not in RECRUITMENT_APPROVAL_FLOW:
        raise ValidationError('Возврат доступен только для этапов маршрута согласования.')
    current_index=RECRUITMENT_APPROVAL_FLOW.index(candidate.stage)
    if target_stage not in RECRUITMENT_APPROVAL_FLOW[:current_index]:
        raise ValidationError('Выберите один из предыдущих этапов.')
    target_index=RECRUITMENT_APPROVAL_FLOW.index(target_stage)
    previous=candidate.stage
    update_fields=['stage','is_active','source_data','updated_at']
    source=dict(candidate.source_data or {})
    source_keys={
        'manager_approved':('manager_approval',),
        'security_approved':('security_approval',),
        'medical_exam_passed':('medical_exam',),
        'pass_documents_sent':('pass_documents','pass_submitted'),
        'pass_ready':('pass_received',),
    }
    for step in RECRUITMENT_APPROVAL_FLOW[target_index:]:
        for flag,date_field,_ in STEP_CONFIRMATIONS.get(step,()):
            setattr(candidate,flag,False);setattr(candidate,date_field,None)
            update_fields.extend([flag,date_field])
            for key in source_keys.get(flag,()):source[key]=''
    candidate.ready_for_arrival=False;candidate.ready_for_arrival_on=None
    source['readiness']='';source['funnel_stage']=target_stage
    update_fields+=['ready_for_arrival','ready_for_arrival_on']
    candidate.stage=target_stage;candidate.is_active=True;candidate.source_data=source
    candidate.save(update_fields=set(update_fields))
    TalentPoolStageEvent.objects.create(company=company,candidate=candidate,from_stage=previous,
        to_stage=target_stage,actor=actor,note=reason)
    return candidate
