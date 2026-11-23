"""Approval email outbox and signed, login-free decision links."""
from urllib.parse import urljoin

from django.conf import settings
from django.core import signing
from django.core.mail import EmailMultiAlternatives
from django.db import transaction
from django.db.models import Q
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone

from . import invitation_mail
from .invitations import safe_mail_error
from .models import ApprovalNotice, User

TOKEN_SALT='portal.approval.email.v1'
TOKEN_MAX_AGE=7*24*60*60


def delivery_users(notice):
    # Temporary rollout rule: approval mail is delivered only to company
    # administrators in every environment. The intended approver remains in
    # the workflow and is shown in the message.
    return list(User.objects.filter(company=notice.company,is_active=True,archived_at__isnull=True).filter(
        Q(role__code='ADMIN')|Q(is_superuser=True)).exclude(email='').order_by('pk'))


def observer_users(notice,action_recipients):
    action_ids={user.pk for user in action_recipients}
    return list(User.objects.filter(approval_observer_memberships__company_id=notice.company_id,
        company=notice.company,is_active=True,
        archived_at__isnull=True).exclude(email='').exclude(pk__in=action_ids).select_related('role').distinct().order_by('pk'))


def queue_task(task,process,stage_code,intended_user):
    if not intended_user or task.status!='OPEN':return None
    notice,created=ApprovalNotice.objects.get_or_create(company=task.company,process=process,stage_code=stage_code,
        task_id=task.pk,intended_user=intended_user,defaults={'intended_email':intended_user.email})
    # Before live approval SMTP was enabled, acceptance's dummy backend
    # reported success although nothing left the process.  Requeue each such
    # legacy notice once; successful SMTP delivery records its backend and is
    # never duplicated on later page opens.
    if not created and getattr(settings,'APPROVAL_EMAIL_FORCE_SMTP',False) and notice.status=='SENT' and notice.delivery_backend!='SMTP':
        created=bool(ApprovalNotice.objects.filter(pk=notice.pk,status='SENT').exclude(delivery_backend='SMTP').update(
            status='PENDING',delivered_to=[],sent_at=None,error=''))
    if created:transaction.on_commit(lambda pk=notice.pk:send_notice(pk))
    return notice


def retry_failed(company):
    ids=list(ApprovalNotice.objects.filter(company=company,status='FAILED').values_list('pk',flat=True)[:500])
    if not ids:return 0
    ApprovalNotice.objects.filter(pk__in=ids,status='FAILED').update(status='PENDING',error='')
    transaction.on_commit(lambda:[send_notice(pk) for pk in ids])
    return len(ids)


def token(notice,recipient):
    return signing.dumps({'notice':notice.pk,'actor':recipient.pk,'task':notice.task_id,
        'process':notice.process,'stage':notice.stage_code,'intended':notice.intended_user_id},salt=TOKEN_SALT,compress=True)


def movement_rows(task):
    """Return the same employee summary shown on a movement approval task."""
    prefix='personnel-movement-approval:'
    batch_prefix='personnel-movement-batch:'
    if not task.key.startswith((prefix,batch_prefix)):return []
    from apps.personnel.models import MovementReviewRequest
    rows=MovementReviewRequest.objects.filter(company=task.company)
    if task.key.startswith(batch_prefix):
        rows=rows.filter(approval_batch=task.key.removeprefix(batch_prefix))
    else:
        pk=task.key.removeprefix(prefix)
        if not pk.isdecimal():return []
        rows=rows.filter(pk=int(pk))
    rows=rows.select_related('person__employee__position','construction_object','from_camp','to_camp').order_by('pk')
    result=[]
    for row in rows:
        employee=row.person.employee if row.person.employee_id else None
        position=(row.planning_position_name or row.position_name or
            (employee.display_position if employee else '') or 'Должность не указана')
        if row.kind=='DEPARTURE':
            reason='Увольнение' if row.departure_destination=='DISMISSAL' else 'Межвахтовый отдых'
        elif row.kind=='ARRIVAL':
            reason=row.get_employment_kind_display() if row.employment_kind else 'Заезд'
        else:reason='Перевод'
        result.append({'employee':row.person.display_name,'position':position,
            'kind':{'ARRIVAL':'Заезд','DEPARTURE':'Выезд','TRANSFER':'Перевод'}.get(row.kind,row.kind),
            'reason':reason,'route':row.route_label(),'date':row.movement_date,
            'decision':row.workflow_status_label()})
    return result


def context(notice,task,recipient,cfg):
    signed=token(notice,recipient)
    action=urljoin(cfg['portal_url'],reverse('accounts:approval_email_action',args=[signed]))
    portal=urljoin(cfg['portal_url'],reverse('planning:task_detail',args=[task.pk]))
    process=dict(ApprovalNotice._meta.get_field('process').choices)[notice.process]
    from .approval_assignments import PROCESS_STAGES
    stage_label=PROCESS_STAGES.get(notice.process,('',{}))[1].get(notice.stage_code,(notice.stage_code,''))[0]
    details=[]
    if task.version_id:
        version=task.version
        snapshot=version.snapshot or {}
        resources=snapshot.get('resources',{})
        details=[('Документ',version.title or f'ГПР · версия {version.version_number}'),
            ('Период',f'{version.start_date:%d.%m.%Y} — {version.end_date:%d.%m.%Y}'),
            ('Раунд согласования',str(version.approval_round)),
            ('Работ',str(len(snapshot.get('works',[])))),
            ('Строк должностей',str(len(resources.get('labor',[])))),
            ('Строк техники',str(len(resources.get('equipment',[])))),
            ('Строк ГСМ',str(len(resources.get('fuel',[]))))]
        items=[row.get('name') or row.get('label') for row in snapshot.get('works',[])[:30]]
    elif task.schedule_id:
        schedule=task.schedule
        grouped={kind:schedule.entries.filter(kind=kind).count() for kind in ('work','labor','equipment','fuel')}
        details=[('Документ',schedule.name or 'СМГ'),('Период',schedule.month.strftime('%m.%Y')),
            ('Вариант',str(schedule.revision)),('Раунд согласования',str(schedule.approval_round)),
            ('Работ',str(grouped['work'])),('Должностей',str(grouped['labor'])),
            ('Техники',str(grouped['equipment'])),('ГСМ',str(grouped['fuel']))]
        items=list(schedule.entries.filter(kind='work').values_list('label',flat=True)[:30])
    else:items=[]
    return {'notice':notice,'task':task,'recipient':recipient,'process_label':process,'stage_label':stage_label,
        'details':details,'items':items,'movement_rows':movement_rows(task),
        'action_url':action,
        'approve_url':action+'?decision=approve','reject_url':action+'?decision=reject',
        'portal_url':portal,'test_mode':getattr(settings,'STAGING_MODE',False),'admin_delivery':True}


def send_notice(pk):
    from apps.planning.models import WorkTask
    try:
        notice=ApprovalNotice.objects.select_related('company','intended_user__role').get(pk=pk)
        if notice.status!='PENDING':return
        if not notice.intended_user_id:
            ApprovalNotice.objects.filter(pk=pk,status='PENDING').update(status='FAILED',error='Назначенный согласующий удалён.');return
        task=WorkTask.objects.select_related('construction_object','created_by').filter(pk=notice.task_id,company=notice.company).first()
        if not task or task.status!='OPEN':
            ApprovalNotice.objects.filter(pk=pk,status='PENDING').update(status='FAILED',error='Согласование уже не активно.');return
        recipients=delivery_users(notice)
        if not recipients:
            ApprovalNotice.objects.filter(pk=pk,status='PENDING').update(
                status='FAILED',error='У активных администраторов компании не указан email.');return
        cfg,error=invitation_mail.ready(notice.company)
        if error:
            ApprovalNotice.objects.filter(pk=pk,status='PENDING').update(status='FAILED',error=error);return
        force_smtp=getattr(settings,'APPROVAL_EMAIL_FORCE_SMTP',False)
        conn=invitation_mail.connection(cfg,force_smtp=force_smtp);conn.open()
        delivery_backend='SMTP' if isinstance(conn,invitation_mail.SMTPBackend) else type(conn).__module__.rsplit('.',1)[-1][:12].upper()
        delivered=[]
        try:
            for recipient in recipients:
                ctx=context(notice,task,recipient,cfg)
                prefix='[ТЕСТ] ' if getattr(settings,'STAGING_MODE',False) else ''
                subject=(prefix+'Согласование: '+task.title)[:255]
                message=EmailMultiAlternatives(subject,render_to_string('accounts/email/approval.txt',ctx),
                    invitation_mail.sender(cfg),[recipient.email],connection=conn)
                message.attach_alternative(render_to_string('accounts/email/approval.html',ctx),'text/html')
                if message.send()!=1:raise RuntimeError('Почтовый сервер не принял письмо.')
                delivered.append(recipient.email)
            observer_errors=[]
            for observer in observer_users(notice,recipients):
                try:
                    ctx=context(notice,task,observer,cfg)
                    prefix='[ТЕСТ] ' if getattr(settings,'STAGING_MODE',False) else ''
                    subject=(prefix+'Наблюдение за согласованием: '+task.title)[:255]
                    message=EmailMultiAlternatives(subject,render_to_string('accounts/email/approval_observer.txt',ctx),
                        invitation_mail.sender(cfg),[observer.email],connection=conn)
                    message.attach_alternative(render_to_string('accounts/email/approval_observer.html',ctx),'text/html')
                    if message.send()!=1:raise RuntimeError('Почтовый сервер не принял информационное письмо.')
                    delivered.append(observer.email)
                except Exception as exc:observer_errors.append(f'{observer.email}: {safe_mail_error(exc)}')
        finally:
            try:conn.close()
            except Exception:pass
        ApprovalNotice.objects.filter(pk=pk,status='PENDING').update(status='SENT',delivered_to=delivered,
            delivery_backend=delivery_backend,sent_at=timezone.now(),error='; '.join(observer_errors))
    except Exception as exc:
        ApprovalNotice.objects.filter(pk=pk).update(status='FAILED',
            delivery_backend='SMTP' if getattr(settings,'APPROVAL_EMAIL_FORCE_SMTP',False) else '',error=safe_mail_error(exc))


def load_token(value):
    return signing.loads(value,salt=TOKEN_SALT,max_age=TOKEN_MAX_AGE)
