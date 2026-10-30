from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied,ValidationError
from django.http import JsonResponse
from django.shortcuts import get_object_or_404,render,redirect
from django.views.decorators.debug import sensitive_post_parameters
from django.views.decorators.http import require_http_methods,require_POST
from django.core.paginator import Paginator
from core.permissions import require_roles
from .models import User
from .invitation_models import InvitationBatch,CompanyMailSettings
from . import invitations,invitation_mail


def admin(request):
    require_roles(request.user,{'ADMIN'})
    if not request.user.company_id:raise PermissionDenied('Администратору не назначена компания.')


@login_required
@require_POST
def prepare(request,pk=None):
    admin(request)
    if pk is not None:
        get_object_or_404(User,pk=pk,company=request.user.company);ids=[pk]
    elif request.POST.get('all_filtered')=='yes':
        from .views import UserListView
        view=UserListView();view.setup(request);ids=list(view.get_queryset().values_list('pk',flat=True)[:5001])
    else:
        values=request.POST.getlist('selected')
        if len(values)>5000 or any(not v.isdecimal() for v in values):raise PermissionDenied('Некорректный список получателей.')
        ids=[int(v) for v in values]
    try:batch=invitations.prepare(request.user,ids)
    except ValidationError as exc:
        messages.error(request,' '.join(exc.messages));return redirect('accounts:user_list')
    return redirect('accounts:invitation_detail',pk=batch.pk)


@login_required
@require_http_methods(['GET','POST'])
def detail(request,pk):
    admin(request);batch=get_object_or_404(InvitationBatch,pk=pk,company=request.user.company)
    if request.method=='POST':
        try:
            if request.POST.get('action')=='send':
                if batch.created_by_id!=request.user.pk:raise PermissionDenied('Подтвердить приглашения должен администратор, подготовивший список.')
                invitations.confirm(request.user,batch);messages.success(request,'Приглашения поставлены в очередь. Отправка продолжится в фоне.')
            elif request.POST.get('action')=='resume':
                if not batch.confirmed_at:raise ValidationError('Сначала подтвердите список получателей.')
                cfg,error=invitation_mail.ready(request.user.company)
                if error:raise ValidationError(error)
                InvitationBatch.objects.filter(pk=batch.pk).update(paused=False,retry_after=None,last_error='');invitations.kick();messages.success(request,'Отправка продолжена. Уже отправленные письма не повторяются.')
            else:raise ValidationError('Неизвестное действие.')
            return redirect('accounts:invitation_detail',pk=batch.pk)
        except ValidationError as exc:messages.error(request,' '.join(exc.messages))
    batch.refresh_from_db();stats=invitations.counts(batch);page=Paginator(batch.items.all(),100).get_page(request.GET.get('page'))
    example=batch.items.exclude(status='BLOCKED').first()
    cfg,error=invitation_mail.ready(request.user.company)
    return render(request,'accounts/invitation_detail.html',{'batch':batch,'stats':stats,'page':page,'example':example,'mail_error':error,'sender_email':cfg['from_email'] if cfg else '', 'can_confirm':batch.created_by_id==request.user.pk})


@login_required
@require_http_methods(['GET'])
def status(request,pk):
    admin(request);batch=get_object_or_404(InvitationBatch,pk=pk,company=request.user.company)
    return JsonResponse({'counts':invitations.counts(batch),'error':batch.last_error,'paused':batch.paused,'items':[{'id':str(item.pk),'status':item.status,'label':item.get_status_display(),'error':item.error} for item in Paginator(batch.items.all(),100).get_page(request.GET.get('page'))]})


@login_required
@require_http_methods(['GET'])
def history(request):
    admin(request);page=Paginator(InvitationBatch.objects.filter(company=request.user.company).select_related('created_by'),25).get_page(request.GET.get('page'))
    entries=[{'batch':batch,'counts':invitations.counts(batch)} for batch in page]
    return render(request,'accounts/invitation_history.html',{'entries':entries,'page':page})


@login_required
@sensitive_post_parameters('password')
@require_http_methods(['GET','POST'])
def settings_view(request):
    admin(request)
    row=CompanyMailSettings.objects.filter(company=request.user.company).first()
    if row is None:
        security='SSL' if settings.EMAIL_USE_SSL else 'STARTTLS' if settings.EMAIL_USE_TLS else 'NONE'
        # Provider-confirmed defaults apply only to an unconfigured form.
        # Existing company settings and configured server environment take priority.
        host=settings.EMAIL_HOST or 'smtp.lancloud.ru'
        port=settings.EMAIL_PORT if settings.EMAIL_HOST else 587
        if not settings.EMAIL_HOST:security='STARTTLS'
        row=CompanyMailSettings(company=request.user.company,host=host,port=port,security=security,username=settings.EMAIL_HOST_USER or settings.DEFAULT_FROM_EMAIL,from_email=settings.DEFAULT_FROM_EMAIL,portal_url=settings.PORTAL_PUBLIC_URL)
        if settings.EMAIL_HOST_PASSWORD:row.password_encrypted=invitation_mail.encrypt_password(request.user.company_id,settings.EMAIL_HOST_PASSWORD)
    form=invitation_mail.MailSettingsForm(request.POST if request.method=='POST' else None,instance=row,company=request.user.company)
    if request.method=='POST' and form.is_valid():
        row=form.save(commit=False);row.updated_by=request.user;row.save()
        from apps.audit.models import AuditLog
        AuditLog.objects.create(user=request.user,action='UPDATE',entity_type='accounts.CompanyMailSettings',entity_id=str(row.pk),new_value={'host':row.host,'port':row.port,'security':row.security,'from_email':row.from_email,'username':row.username,'portal_url':row.portal_url,'password_changed':bool(form.cleaned_data.get('password') or form.cleaned_data.get('clear_password'))},comment='Настройки отправки приглашений; пароль не записывается в журнал')
        if request.POST.get('action')=='test':
            cfg,error=invitation_mail.ready(request.user.company)
            if error:messages.error(request,error)
            else:
                conn=None
                try:conn=invitation_mail.connection(cfg);conn.open();messages.success(request,'Подключение к SMTP и авторизация успешны. Письма при проверке не отправлялись.')
                except Exception as exc:messages.error(request,invitations.safe_mail_error(exc))
                finally:
                    if conn:
                        try:conn.close()
                        except Exception:pass
        else:messages.success(request,'Настройки исходящей почты сохранены.')
        return redirect('accounts:invitation_settings')
    return render(request,'accounts/invitation_settings.html',{'form':form,'password_saved':bool(row.password_encrypted)})
