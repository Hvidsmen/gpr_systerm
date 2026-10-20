from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404,redirect
from django.utils import timezone
from django.views.decorators.http import require_POST
from core.permissions import require_roles
from core.impersonation import KEY,permitted,record
from .models import User


@login_required
@require_POST
def start(request,pk):
    require_roles(request.user,{'ADMIN'})
    if request.session.get(KEY):raise PermissionDenied('Сначала завершите текущий режим пользователя.')
    target=get_object_or_404(User.objects.select_related('role','company'),pk=pk,company=request.user.company)
    if not permitted(request.user,target):raise PermissionDenied('Просмотр этой учетной записи недоступен.')
    record(request.user,target,'Администратор начал работу в режиме пользователя')
    request.session.cycle_key()
    request.session[KEY]=dict(actor=request.user.pk,target=target.pk,started=timezone.now().timestamp())
    return redirect('dashboard:index')


@login_required
@require_POST
def stop(request):
    data=request.session.pop(KEY,None)
    if data and data.get('actor')==request.user.pk:
        target=User.objects.filter(pk=data.get('target')).first()
        if target:record(request.user,target,'Администратор завершил работу в режиме пользователя')
    request.session.cycle_key()
    return redirect('accounts:user_list')


@login_required
def history(request):
    from django.shortcuts import render
    from django.core.paginator import Paginator
    from apps.audit.models import AuditLog
    require_roles(request.user,{'ADMIN'})
    logs=AuditLog.objects.filter(new_value__company_id=request.user.company_id,new_value__acting_user_id__isnull=False).select_related('user').order_by('-created_at','-pk')
    page=Paginator(logs,50).get_page(request.GET.get('page'))
    targets={u.pk:u.full_name for u in User.objects.filter(company=request.user.company,pk__in=[row.new_value['acting_user_id'] for row in page])}
    for row in page:row.acting_name=targets.get(row.new_value['acting_user_id'],str(row.new_value['acting_user_id']))
    return render(request,'accounts/impersonation_history.html',{'page':page})
