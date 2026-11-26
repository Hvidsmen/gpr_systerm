"""SMG presentation using the same workflow vocabulary as the GPR editor."""
from types import SimpleNamespace
from core.permissions import role_code,PLAN_ROLES
from . import preparation
from .gpr_ui import path_context
from .models import DailySchedule
from .preparation_views import HandoffForm


def editor_context(user,schedule,approval):
    record=preparation.record(schedule);latest=schedule.estimate_reviews.order_by('-pk').first()
    role=role_code(user);draft=schedule.status=='DRAFT'
    resources=bool(record and record.resources_ready_at and record.resources_fingerprint==preparation.resource_signature(schedule))
    manager=preparation.resource_editor_allowed(user,schedule,record)
    status={'APPLIED':'APPROVED','ARCHIVED':'COMPLETED','CANCELLED':'COMPLETED'}.get(schedule.status,schedule.status)
    returned=draft and any(decision.action=='REJECT' for decision in approval['smg_decisions'])
    if returned:status='REJECTED'
    path=path_context(SimpleNamespace(status=status,approval_round=schedule.approval_round),{'resource_preparation':record,'resources_ready':resources,'estimator_approved':approval['smg_checked'],'approval_ready':all(item['done'] for item in approval['smg_actions'])},user)
    state='done' if schedule.status in {'APPLIED','ARCHIVED'} else 'current' if schedule.status=='APPROVED' else 'pending'
    path['gpr_path'].append({'key':'operational','label':'Оперативный план','state':state,'state_label':{'done':'Применён','current':'Нужно применить','pending':'Предстоит'}[state]})
    if schedule.status=='APPLIED' and role=='ADMIN':instruction='СМГ применён. Для исправлений верните эту редакцию в доработку с указанием причины либо создайте новую редакцию.'
    elif schedule.status in {'APPLIED','ARCHIVED'}:instruction='Эта редакция зафиксирована. Для уточнений создайте новую редакцию; сохранённые закрытые дни останутся в истории.'
    elif schedule.status=='APPROVED':instruction='СМГ утверждён. Руководитель проекта применяет его к оперативному плану.'
    elif schedule.status=='SUBMITTED':
        sections=[item['label'] for item in approval['smg_actions'] if item['allowed']]
        if approval['smg_final_allowed']:instruction='Проверьте согласованный СМГ и утвердите его либо верните на доработку с причиной.'
        elif sections:instruction='Проверьте свой раздел: '+', '.join(sections)+'. Согласуйте его или верните СМГ на доработку с причиной.'
        else:instruction='СМГ на согласовании. Дневные значения зафиксированы; ожидайте решений участников.'
    elif role in PLAN_ROLES:instruction='Заполните работы, объёмы и дни, организуйте совместное формирование ресурсов. После подтверждения ресурсов и проверки сметчика проверьте маршрут и запустите согласование.'
    elif manager:instruction='Проверьте и дополните ресурсы, настройте связи с работами и подтвердите ресурсную часть. Согласование запускает планировщик.'
    elif role=='TECH_HEAD':instruction='Скорректируйте технику, ГСМ и должности назначенных групп. Остальные строки доступны для просмотра.'
    elif role=='SBO_HEAD':instruction='Скорректируйте должности назначенных групп. Остальные строки доступны для просмотра.'
    elif role=='ESTIMATOR':instruction='Проверьте состав работ и цены в задании сметчика. Ресурсная часть формируется параллельно.'
    else:instruction='Просмотрите дневной план, связи и текущий этап согласования.'
    path['gpr_instruction']=('СМГ возвращён. Исправьте замечания в истории согласования. ' if returned else '')+instruction
    months={}
    for row in DailySchedule.objects.filter(company=user.company,construction_object_id=schedule.construction_object_id).order_by('month','-revision'):
        months.setdefault(row.month,row)
    rejections=[d for d in approval['smg_decisions'] if d.action=='REJECT']
    from apps.resources.catalogue_review import context as catalogue_context
    return {**catalogue_context(user,schedule),**path,**preparation.task_context(user,schedule),'smg_can_return_applied':role=='ADMIN' and schedule.status=='APPLIED',
        'smg_return_comment':max(rejections,key=lambda d:d.pk).comment if returned and rejections else '',
        'smg_resource_ready':resources,'smg_can_confirm':draft and bool(manager and record),'smg_can_handoff':draft and role in PLAN_ROLES,
        'smg_can_launch_approval':draft and role in PLAN_ROLES and bool(record) and resources,
        'smg_handoff_form':HandoffForm(company=user.company,initial={'manager':record.manager_id if record else None,'resource_editors':list(record.resource_editors.values_list('pk',flat=True)) if record else [],'estimator':latest.assigned_to_id if latest else None}) if draft and role in PLAN_ROLES else None,
        'smg_latest_review':latest if role in {'ADMIN','PLANNER','ESTIMATOR'} else None,'smg_month_choices':list(months.values())}
