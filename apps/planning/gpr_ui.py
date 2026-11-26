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
    elif role=='PLANNER' and handed and resources and prices:instruction='Проверьте состав плана и маршрут согласования. Если все согласующие назначены, запустите согласование; после запуска редакция будет зафиксирована.'
    elif role=='PLANNER' and handed:instruction='Продолжайте формировать работы, объёмы и сроки вместе с руководителем проекта и начальником участка. Для запуска согласования дождитесь подтверждения ресурсов и проверки цен.'
    elif role=='PLANNER':instruction='Спланируйте работы, объёмы и сроки. Передайте ресурсную часть руководителю проекта, начальнику участка и профильным службам; запуск согласования выполняет планировщик.'
    elif role in {'MANAGER','SITE_CHIEF'} and context.get('can_confirm_resources'):instruction='Дополните ресурсную часть вместе с планировщиком. Руководитель проекта и начальник участка взаимозаменяемы: достаточно подтверждения одного из назначенных участников.'
    elif role=='TECH_HEAD':instruction='Проверьте и скорректируйте технику, ГСМ и должности назначенных вам групп. Остальные разделы доступны для просмотра.'
    elif role=='SBO_HEAD':instruction='Проверьте и скорректируйте должности назначенных вам групп. Остальные разделы доступны для просмотра.'
    elif role=='ESTIMATOR':instruction='Проверьте цены работ в назначенном задании сметчика. Названия, состав и объёмы доступны для просмотра. Завершите проверку; ресурсы подтверждает руководитель.'
    elif role=='ADMIN':instruction='Сформируйте план совместно с участниками. Перед запуском проверьте подтверждение ресурсов, цены и назначение всех согласующих.'
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
