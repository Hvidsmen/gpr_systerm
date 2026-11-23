import logging

from django.core import signing
from django.core.exceptions import PermissionDenied,ValidationError
from django.db import transaction
from django.shortcuts import get_object_or_404,render
from django.views.decorators.http import require_http_methods
from django.views.decorators.csrf import csrf_exempt

from .approval_assignments import authorized
from .approval_mail import load_token
from .models import ApprovalNotice,User

logger=logging.getLogger(__name__)


def movement_rows_for_notice(notice,task,selected=None,lock=False):
    from apps.personnel.departure_workflow import active_route_roles,movement_scope
    rows=approval_rows_for_task(task)
    if lock:rows=rows.select_for_update()
    rows=list(rows.select_related('person').order_by('pk'))
    rows=[row for row in rows if notice.stage_code in active_route_roles(row) and
        authorized(notice.intended_user,'MOVEMENT',notice.stage_code,notice.stage_code,**movement_scope(row))]
    if selected is not None:
        selected_ids={int(value) for value in selected if str(value).isdigit()}
        if not selected_ids:raise ValidationError('Выберите хотя бы одного сотрудника.')
        rows=[row for row in rows if row.pk in selected_ids]
    return rows


@transaction.atomic
def execute(notice,task,actor,decision,comment,selected=None,comments=None):
    intended=notice.intended_user
    if task.status!='OPEN':raise ValidationError('Это согласование уже завершено или заменено.')
    if notice.process=='MOVEMENT' and notice.stage_code=='PEREVAKHTA_SPECIALIST':
        raise ValidationError('Специалист перевахты должен проверить заявки и принять решение в портале. Согласование из письма недоступно.')
    approved=decision=='approve'
    if decision not in {'approve','reject'}:raise ValidationError('Выберите решение.')
    if notice.process=='MOVEMENT':
        from apps.personnel.departure_workflow import decide,sync_approval_task
        rows=movement_rows_for_notice(notice,task,selected,lock=True)
        if not rows:raise ValidationError('Этот этап уже завершён или ещё не начался.')
        row_comments={}
        if not approved:
            if comments is None:
                if not comment.strip():raise ValidationError('При несогласовании комментарий обязателен.')
                row_comments={row.pk:comment.strip() for row in rows}
            else:
                row_comments={row.pk:str(comments.get(str(row.pk),'')).strip() for row in rows}
                missing=[row.person.display_name for row in rows if not row_comments[row.pk]]
                if missing:raise ValidationError('Укажите отдельную причину несогласования для: '+', '.join(missing)+'.')
        for row in rows:decide(actor,row,approved,row_comments.get(row.pk,''),sync_task=False,step=notice.stage_code)
        sync_approval_task(rows[0],actor);return
    if not approved and not comment.strip():raise ValidationError('При несогласовании комментарий обязателен.')
    if not authorized(intended,notice.process,notice.stage_code,task.target_role):
        raise ValidationError('Согласующий этапа был заменён. Используйте новое письмо.')
    from core.plan_approval import SECTION_ACTIONS
    command=SECTION_ACTIONS.get(notice.stage_code,'approve') if approved else 'reject'
    if notice.process=='GPR':
        if not task.version_id or not task.review_id or task.version.approval_round!=task.review.number:
            raise ValidationError('Раунд согласования ГПР уже изменился.')
        from apps.planning.approval_workflow import transition
        transition(task.version,actor,command,comment);return
    if not task.schedule_id or not task.schedule_review_id or task.schedule.approval_round!=task.schedule_review.number:
        raise ValidationError('Раунд согласования СМГ уже изменился.')
    from apps.planning.daily_schedule_approval import transition
    transition(actor,task.schedule,command,comment)


def approval_rows_for_task(task):
    from apps.personnel.models import MovementReviewRequest
    query=MovementReviewRequest.objects.filter(company=task.company,status__in=['PENDING_APPROVAL','RETURNED'])
    if task.key.startswith('personnel-movement-batch:'):return query.filter(approval_batch=task.key.split(':',1)[1])
    if task.key.startswith('personnel-movement-approval:'):return query.filter(pk=task.key.rsplit(':',1)[1])
    return query.none()


@csrf_exempt
@require_http_methods(['GET','POST'])
def action(request,token):
    from apps.planning.models import WorkTask
    invalid='Ссылка недействительна или срок её действия истёк.'
    try:data=load_token(token)
    except signing.BadSignature:return render(request,'accounts/approval_email_action.html',{'error':invalid},status=400)
    notice=get_object_or_404(ApprovalNotice.objects.select_related('company','intended_user__role'),pk=data.get('notice'))
    if not data.get('actor') or any(data.get(key)!=value for key,value in [('task',notice.task_id),('process',notice.process),('stage',notice.stage_code),('intended',notice.intended_user_id)]):
        return render(request,'accounts/approval_email_action.html',{'error':invalid},status=400)
    if not notice.intended_user_id:return render(request,'accounts/approval_email_action.html',{'error':invalid},status=400)
    actor=User.objects.filter(pk=data.get('actor'),company=notice.company,is_active=True,archived_at__isnull=True).first()
    task=WorkTask.objects.select_related('version','review','schedule','schedule_review','construction_object','created_by').filter(pk=notice.task_id,company=notice.company).first()
    if not actor or not task:return render(request,'accounts/approval_email_action.html',{'error':invalid},status=400)
    portal_review_only=notice.process=='MOVEMENT' and notice.stage_code=='PEREVAKHTA_SPECIALIST'
    if portal_review_only:
        # Legacy messages may still contain approve/reject forms.  Never enter
        # the decision handler for this stage: the specialist must inspect and
        # complete the movement data in the authenticated portal first.
        return render(request,'accounts/approval_email_action.html',{'notice':notice,'task':task,
            'portal_review_only':True})
    source=request.POST if request.method=='POST' else request.GET
    decision=source.get('decision','');selected=source.getlist('selected')
    error=''
    if request.method=='POST':
        try:
            row_comments={key.removeprefix('comment_'):value[:4000] for key,value in request.POST.items()
                if key.startswith('comment_')}
            execute(notice,task,actor,decision,request.POST.get('comment','')[:4000],selected or None,
                row_comments if notice.process=='MOVEMENT' and decision=='reject' else None)
            return render(request,'accounts/approval_email_action.html',{'success':'Решение сохранено.','task':task,'decision':decision})
        except (ValidationError,PermissionDenied) as exc:error='; '.join(exc.messages) if isinstance(exc,ValidationError) else str(exc)
        except Exception:
            logger.exception('Approval email action failed',extra={'notice_id':notice.pk,'task_id':task.pk})
            error='Не удалось сохранить решение по почтовой ссылке. Откройте согласование в портале.'
    decision_rows=[]
    if notice.process=='MOVEMENT':
        try:decision_rows=movement_rows_for_notice(notice,task,selected or None)
        except ValidationError:pass
    selected_names=[row.person.display_name for row in decision_rows] if selected else []
    return render(request,'accounts/approval_email_action.html',{'notice':notice,'task':task,'decision':decision,
        'error':error,'selected':selected,'selected_names':selected_names,'decision_rows':decision_rows,
        'portal_review_only':False})
