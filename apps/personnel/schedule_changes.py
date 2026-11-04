"""Request ZUP schedule changes and apply them only after a verified employee sync."""
from datetime import timedelta
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from apps.planning.models import WorkTask
from apps.planning.task_services import ensure,state
from .models import ScheduleChangeRequest,StaffingSlot


def normalized(value):return ' '.join(str(value or '').split()).casefold()


@transaction.atomic
def request_change(user,person,schedule):
    if person.company_id!=user.company_id or schedule.company_id!=user.company_id or not schedule.is_active:raise ValidationError('Выберите действующий график ЗУП своей компании.')
    current=((person.employee.zup_card or {}).get('schedule') or '').strip() if person.employee_id else ''
    if normalized(current)==normalized(schedule.name):raise ValidationError('У сотрудника уже установлен этот график в ЗУП.')
    previous=ScheduleChangeRequest.objects.select_for_update().filter(person=person,status='PENDING').first()
    if previous:
        previous.status='CANCELLED';previous.save(update_fields=['status','updated_at'])
        if previous.task_key:
            task=WorkTask.objects.filter(company=user.company,key=previous.task_key).first()
            if task:state(task,'SUPERSEDED',user,'Создан новый запрос изменения графика.')
    row=ScheduleChangeRequest.objects.create(company=user.company,person=person,requested_schedule=schedule,previous_schedule=current,requested_by=user)
    row.task_key=f'zup-schedule-change:{row.pk}'
    row.save(update_fields=['task_key','updated_at'])
    ensure(user.company,row.task_key,kind='MANUAL',title=f'Изменить график в ЗУП · {person.display_name}',
        description=f'Установить сотруднику график «{schedule.name}» в ЗУП. Текущий график: {current or "не указан"}. Задание закроется автоматически после загрузки сотрудников из ЗУП.',
        target_role='PERSONNEL_CATALOG_MANAGER',created_by=user,due_date=timezone.localdate()+timedelta(days=1))
    return row


@transaction.atomic
def reconcile_company(company):
    applied=0
    rows=ScheduleChangeRequest.objects.select_for_update().filter(company=company,status='PENDING').select_related('person__employee','requested_schedule')
    for row in rows:
        actual=((row.person.employee.zup_card or {}).get('schedule') or '').strip() if row.person.employee_id else ''
        if normalized(actual)!=normalized(row.requested_schedule.name):continue
        StaffingSlot.objects.filter(company=company,person=row.person,cancelled_at=None).update(pattern=None)
        row.status='APPLIED';row.applied_at=timezone.now();row.save(update_fields=['status','applied_at','updated_at'])
        task=WorkTask.objects.filter(company=company,key=row.task_key).first()
        if task:state(task,'DONE',text='Новый график получен при синхронизации сотрудников из ЗУП.')
        applied+=1
    return applied
