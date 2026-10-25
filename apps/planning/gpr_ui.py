"""State-only presentation helpers for the single GPR workspace."""
from django.core.exceptions import ObjectDoesNotExist
def path_context(version, context, user=None):
    locked=version.status in {'SUBMITTED','APPROVED','COMPLETED'}
    approved=version.status in {'APPROVED','COMPLETED'}
    handed=bool(context.get('resource_preparation')) or locked
    resources=locked or context.get('resources_ready',False)
    prices=locked or context.get('estimator_approved',False)
    steps=[
        {'key':'works','label':'Доработка работ' if version.status=='REJECTED' else 'Подготовка работ','state':'current' if version.status=='REJECTED' else 'done' if handed else 'current'},
        {'key':'preparation','label':'Ресурсы и цены','state':'done' if locked else 'current' if handed else 'pending',
         'note':'Ожидает отправки на согласование' if resources and prices and not locked else '',
         'branches':[{'key':'resources','label':'Ресурсы','state':'done' if resources else 'current' if handed else 'pending'},
                     {'key':'prices','label':'Цены','state':'done' if prices else 'current' if handed else 'pending'}]},
        {'key':'approval','label':'Согласование','state':'done' if approved or version.status=='SUBMITTED' and context.get('approval_ready') else 'current' if version.status=='SUBMITTED' else 'returned' if version.status=='REJECTED' and version.approval_round else 'pending'},
        {'key':'director','label':'Утверждение','state':'done' if approved else 'current' if version.status=='SUBMITTED' and context.get('approval_ready') else 'pending'},
    ]
    names={'done':'Завершено','current':'Сейчас','pending':'Предстоит','returned':'Возврат на доработку'}
    for step in steps:
        step['state_label']=names[step['state']]
        for branch in step.get('branches',[]):branch['state_label']=names[branch['state']]
    from core.permissions import role_code
    role=role_code(user) if user else ''
    if version.status=='COMPLETED':instruction='План завершён. Данные и решения доступны для просмотра.'
    elif approved:instruction='План утверждён. Используйте его как основание для СМГ; история согласования сохранена.'
    elif locked:
        parts=[s['label'] for s in context.get('approval_sections',[]) if s.get('can_approve')]
        if context.get('can_final_approve'):instruction='Проверьте согласованный план и утвердите его либо верните на доработку с причиной.'
        elif parts:instruction='Проверьте свой раздел: '+', '.join(parts)+'. Согласуйте его или верните план на доработку с причиной.'
        else:instruction='План на согласовании. Работы и ресурсы зафиксированы; ожидайте решений участников. Текущий ход показан выше.'
    elif role=='PLANNER' and handed:instruction='Работы и предварительные ресурсы переданы руководителю. Можно уточнять данные до отправки на согласование; после изменений руководитель подтверждает ресурсы заново, а изменения работ требуют проверки сметчиком.'
    elif role=='PLANNER':instruction='Составьте работы и объёмы. При необходимости добавьте предварительные ресурсы. Сохраните изменения и передайте план руководителю и сметчику. Ресурсы утверждает руководитель.'
    elif role=='MANAGER' and context.get('can_confirm_resources') and resources and prices:instruction='Ресурсы подтверждены, цены проверены. Отправьте план на согласование.'
    elif role=='MANAGER' and context.get('can_confirm_resources') and resources:instruction='Ресурсы подтверждены. Дождитесь проверки цен сметчиком, затем отправьте план на согласование.'
    elif role=='MANAGER' and context.get('can_confirm_resources'):instruction='Проверьте работы и предварительные ресурсы планировщика, дополните ресурсы и подтвердите их. После проверки цен сметчиком отправьте план на согласование.'
    elif role=='ESTIMATOR':instruction='Проверьте названия, состав работ и цены в назначенном задании сметчика. Завершите проверку; ресурсы подтверждает руководитель.'
    elif role=='ADMIN':instruction='Составьте работы и предварительные ресурсы, передайте план руководителю и сметчику. Для запуска согласования нужны подтверждённые руководителем ресурсы и проверенные сметчиком цены.'
    else:instruction='Просмотрите работы и ресурсы. Дождитесь назначения задания или передачи плана на ваш этап согласования.'
    return {'gpr_instruction':instruction,'gpr_path':steps,'gpr_returned':version.status=='REJECTED' and bool(version.approval_round)}


def list_stage(version):
    if version is None:return 'Подготовка работ'
    if version.status=='SUBMITTED':return 'Согласование'
    if version.status in {'APPROVED','COMPLETED'}:return version.get_status_display()
    if version.status=='REJECTED':return 'Доработка'
    try:preparation=version.preparation
    except ObjectDoesNotExist:return 'Подготовка работ'
    return 'Подготовка ресурсов и цен' if preparation else 'Подготовка работ'
