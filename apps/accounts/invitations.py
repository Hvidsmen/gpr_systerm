"""Reviewable Windows-login invitations and a persistent per-recipient outbox."""
import hashlib,json,threading,time,smtplib,socket
from datetime import timedelta
from urllib.parse import urljoin
from collections import Counter
from django.conf import settings
from django.core.exceptions import ValidationError,PermissionDenied
from django.core.validators import validate_email
from django.core.mail import EmailMultiAlternatives
from django.db import transaction,close_old_connections
from django.db.models import F,Q
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone
from core.permissions import require_roles
from .models import User
from .invitation_models import InvitationBatch,UserInvitation
from . import invitation_mail

MAX_RECIPIENTS=5000


def fingerprint(user):
    return hashlib.sha256(json.dumps([user.pk,user.company_id,user.ldap_guid.hex if user.ldap_guid else '',user.ldap_login,user.email,user.role_id,user.role.name if user.role_id else '',user.role.code if user.role_id else '',user.full_name,user.auth_source,user.is_active,str(user.archived_at),user.is_superuser,user.is_staff],ensure_ascii=False).encode()).hexdigest()


def blocked_reason(user):
    if user.archived_at or not user.is_active:return 'Учётная запись отключена или находится в архиве.'
    if user.is_superuser or user.is_staff:return 'Служебная учётная запись не предназначена для приглашения сотрудника.'
    if user.auth_source!='LDAP':return 'Локальная учётная запись: пароль Windows для неё не подходит. Добавьте сотрудника из AD.'
    if not settings.LDAP_ENABLED:return 'Корпоративный вход AD отключён в настройках сервера.'
    if not user.role_id:return 'Сначала назначьте роль или группу доступа.'
    from .backends import normalize_login
    if not user.ldap_login or user.ldap_login.startswith('ad:') or normalize_login(user.ldap_login)!=user.ldap_login:return 'Не получен логин Windows из AD.'
    try:validate_email(user.email)
    except ValidationError:return 'В карточке отсутствует корректный адрес электронной почты.'
    return ''


def message_context(user,portal_url):
    from core.instructions_content import GUIDES
    guide=next((g for g in GUIDES if g['kind']=='role' and g.get('role')==user.role.code),None)
    instructions=reverse('instructions:article',args=[guide['slug']]) if guide else reverse('instructions:index')
    return {'full_name':user.full_name,'windows_login':user.ldap_login,'role_name':user.role.name,'portal_url':portal_url,'instructions_url':urljoin(portal_url,instructions.lstrip('/'))}


def resolve_logins(users):
    pending=[u for u in users if u.auth_source=='LDAP' and u.ldap_guid and (not u.ldap_login or not u.email)]
    if not pending:return ''
    from .directory_admin import invitation_identities
    from .backends import normalize_login
    try:identities=invitation_identities([u.ldap_guid for u in pending])
    except ValidationError as exc:return ' '.join(exc.messages)
    for user in pending:
        identity=identities.get(str(user.ldap_guid),{});login=normalize_login(identity.get('login'))
        changes={}
        if login and not login.startswith('ad:'):changes['ldap_login']=login
        if not user.email and identity.get('email'):
            try:validate_email(identity['email']);changes['email']=identity['email']
            except ValidationError:pass
        if changes:
            User.objects.filter(pk=user.pk,company_id=user.company_id,ldap_guid=user.ldap_guid,auth_source='LDAP').update(**changes)
            for name,value in changes.items():setattr(user,name,value)
    return ''


def prepare(actor,ids):
    require_roles(actor,{'ADMIN'})
    if not actor.company_id:raise PermissionDenied('Администратору не назначена компания.')
    ids=set(ids)
    if not 1<=len(ids)<=MAX_RECIPIENTS:raise ValidationError('Выберите от 1 до 5000 пользователей.')
    users=list(User.objects.filter(company=actor.company,pk__in=ids).select_related('role').order_by('last_name','first_name','pk'))
    if len(users)!=len(ids):raise PermissionDenied('Выбор содержит пользователей другой компании или удалённые записи.')
    lookup_error=resolve_logins(users)
    try:cfg=invitation_mail.configuration(actor.company);portal_url=cfg['portal_url']
    except ValidationError:portal_url=invitation_mail.portal_address(settings.PORTAL_PUBLIC_URL)
    with transaction.atomic():
        batch=InvitationBatch.objects.create(company=actor.company,created_by=actor,portal_url=portal_url)
        entries=[]
        for user in users:
            reason=blocked_reason(user)
            if reason.startswith('Не получен') and lookup_error:reason+=' '+lookup_error
            context=message_context(user,portal_url) if not reason else None
            entries.append(UserInvitation(batch=batch,recipient=user,full_name=user.full_name[:350],recipient_email=user.email,windows_login=user.ldap_login,role_name=user.role.name if user.role_id else '',fingerprint=fingerprint(user),status='BLOCKED' if reason else 'DRAFT',error=reason,subject='Приглашение в портал «ЯДС ОС»' if context else '',body=render_to_string('accounts/email/invitation.txt',context) if context else '',html_body=render_to_string('accounts/email/invitation.html',context) if context else ''))
        UserInvitation.objects.bulk_create(entries)
    return batch


def confirm(actor,batch):
    require_roles(actor,{'ADMIN'})
    from apps.audit.models import AuditLog
    with transaction.atomic():
        batch=InvitationBatch.objects.select_for_update().filter(pk=batch.pk,company=actor.company,created_by=actor).first()
        if batch is None:raise PermissionDenied('Недоступное подтверждение приглашений.')
        if batch.confirmed_at:return batch
        cfg,error=invitation_mail.ready(actor.company)
        if error:raise ValidationError(error)
        if cfg['portal_url']!=batch.portal_url:raise ValidationError('Адрес портала изменился после предпросмотра. Подготовьте приглашения заново.')
        items=list(batch.items.filter(status='DRAFT').select_related('recipient__role'))
        if not items:raise ValidationError('Нет получателей, готовых к отправке. Исправьте указанные замечания.')
        for item in items:
            if not item.recipient or item.recipient.company_id!=actor.company_id or fingerprint(item.recipient)!=item.fingerprint or blocked_reason(item.recipient):raise ValidationError('Данные получателей изменились после предпросмотра. Подготовьте приглашения заново.')
        batch.items.filter(status='DRAFT').update(status='QUEUED')
        batch.confirmed_at=timezone.now();batch.last_error='';batch.save(update_fields=['confirmed_at','last_error','updated_at'])
        AuditLog.objects.create(user=actor,action='CREATE',entity_type='accounts.InvitationBatch',entity_id=str(batch.pk),new_value={'company_id':actor.company_id,'recipients':len(items),'portal_url':batch.portal_url},comment='Подтверждена отправка приглашений на портал')
        transaction.on_commit(lambda:kick())
    return batch


def counts(batch):
    result=Counter(batch.items.values_list('status',flat=True))
    return {'total':sum(result.values()),'ready':result['DRAFT'],'blocked':result['BLOCKED'],'pending':result['QUEUED']+result['SENDING'],'sent':result['SENT'],'failed':result['FAILED']+result['UNKNOWN']+result['CANCELLED']}


def safe_mail_error(error):
    if isinstance(error,smtplib.SMTPAuthenticationError):return 'Почтовый сервер отклонил авторизацию. Проверьте логин и пароль в настройках исходящей почты.'
    if isinstance(error,smtplib.SMTPRecipientsRefused):return 'Почтовый сервер отклонил адрес получателя.'
    if isinstance(error,smtplib.SMTPResponseException):return 'Почтовый сервер отклонил запрос (код '+str(error.smtp_code)+').'
    return 'Не удалось завершить обмен с SMTP-сервером. Проверьте настройки исходящей почты и доступность сервера.'


def drain(limit=50,seconds=30):
    """Claim each message once. Interrupted/inconclusive sends are never retried silently."""
    start=time.monotonic();sent=0
    UserInvitation.objects.filter(status='SENDING',started_at__lt=timezone.now()-timedelta(minutes=10)).update(status='UNKNOWN',error='Отправка прервана. Проверьте почтовый ящик перед повторным приглашением.')
    candidates=list(UserInvitation.objects.filter(status='QUEUED',batch__confirmed_at__isnull=False,batch__paused=False).filter(Q(batch__retry_after__isnull=True)|Q(batch__retry_after__lte=timezone.now())).select_related('batch__company').order_by('created_at','id')[:limit])
    connections={}
    try:
        for candidate in candidates:
            if time.monotonic()-start>seconds:break
            company=candidate.batch.company
            if company.pk not in connections:
                cfg,error=invitation_mail.ready(company)
                if error:
                    InvitationBatch.objects.filter(company=company,items__status='QUEUED').update(last_error=error,paused=True);connections[company.pk]=None;continue
                try:
                    conn=invitation_mail.connection(cfg);conn.open();connections[company.pk]=(conn,cfg)
                    InvitationBatch.objects.filter(company=company,items__status='QUEUED').update(last_error='',retry_after=None)
                except Exception as exc:
                    InvitationBatch.objects.filter(company=company,items__status='QUEUED').update(last_error=safe_mail_error(exc),paused=isinstance(exc,smtplib.SMTPAuthenticationError),retry_after=timezone.now()+timedelta(minutes=2));connections[company.pk]=None;continue
            bundle=connections[company.pk]
            if bundle is None:continue
            claimed=UserInvitation.objects.filter(pk=candidate.pk,status='QUEUED').update(status='SENDING',started_at=timezone.now(),attempts=F('attempts')+1)
            if not claimed:continue
            item=UserInvitation.objects.select_related('recipient__role','batch').get(pk=candidate.pk)
            recipient=item.recipient
            if not recipient or recipient.company_id!=company.pk or fingerprint(recipient)!=item.fingerprint or blocked_reason(recipient):
                UserInvitation.objects.filter(pk=item.pk,status='SENDING').update(status='CANCELLED',error='Данные пользователя изменились. Подготовьте приглашение заново.');continue
            conn,cfg=bundle
            if cfg['portal_url']!=item.batch.portal_url:
                UserInvitation.objects.filter(pk=item.pk,status='SENDING').update(status='CANCELLED',error='Адрес портала изменился. Подготовьте приглашение заново.');continue
            try:
                message=EmailMultiAlternatives(item.subject,item.body,invitation_mail.sender(cfg),[item.recipient_email],connection=conn,headers={'Message-ID':'<portal-invitation-'+str(item.pk)+'@'+cfg['from_email'].rsplit('@',1)[-1]+'>'})
                message.attach_alternative(item.html_body,'text/html')
                if message.send()!=1:raise smtplib.SMTPException('Backend did not accept message')
                UserInvitation.objects.filter(pk=item.pk,status='SENDING').update(status='SENT',sent_at=timezone.now(),error='');sent+=1
            except Exception as exc:
                uncertain=isinstance(exc,(socket.timeout,ConnectionError,smtplib.SMTPServerDisconnected)) or (isinstance(exc,OSError) and not isinstance(exc,smtplib.SMTPException))
                text=safe_mail_error(exc)
                if uncertain:text+=' Результат требует проверки: перед повторной отправкой проверьте почту получателя.'
                UserInvitation.objects.filter(pk=item.pk,status='SENDING').update(status='UNKNOWN' if uncertain else 'FAILED',error=text)
    finally:
        for bundle in connections.values():
            if bundle:
                try:bundle[0].close()
                except Exception:pass
    return sent


_worker_lock=threading.Lock()
def kick():
    if not settings.INVITATION_ASYNC_ENABLED:return
    def run():
        if not _worker_lock.acquire(blocking=False):return
        try:
            close_old_connections()
            for _ in range(20):
                if not UserInvitation.objects.filter(status='QUEUED').exists():break
                if drain(limit=25,seconds=20)==0:break
        finally:close_old_connections();_worker_lock.release()
    threading.Thread(target=run,name='portal-invitations',daemon=True).start()
