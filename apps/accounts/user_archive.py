"""Reversible company-scoped bulk archive, with a signed review step."""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core import signing
from django.core.exceptions import PermissionDenied,ValidationError
from django.db import transaction
from django.shortcuts import render,redirect
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST
from core.permissions import require_roles
from .models import User

SALT='accounts.bulk-user-archive.v1'

def eligible(query,actor):
    return query.exclude(pk=actor.pk).filter(is_superuser=False,is_staff=False).exclude(role__code='ADMIN')

@transaction.atomic
def apply(actor,ids,action):
    require_roles(actor,{'ADMIN'})
    if action not in {'archive','restore'}:raise ValidationError('Неизвестное действие.')
    ids=set(ids)
    rows=list(User.objects.select_for_update().filter(company=actor.company,pk__in=ids).select_related('role'))
    if len(rows)!=len(ids) or eligible(User.objects.filter(pk__in=ids,company=actor.company),actor).count()!=len(ids):raise PermissionDenied('Нельзя архивировать себя, администраторов или пользователей другой компании.')
    from apps.audit.models import AuditLog
    changed=0
    for row in rows:
        if (action=='archive')==bool(row.archived_at):continue
        old={'archived_at':row.archived_at.isoformat() if row.archived_at else None,'is_active':row.is_active}
        if action=='archive':
            new={'archived_at':timezone.now(),'archive_was_active':row.is_active,'is_active':False}
        else:new={'archived_at':None,'is_active':row.archive_was_active,'archive_was_active':False}
        User.objects.filter(pk=row.pk,company=actor.company).update(**new)
        AuditLog.objects.create(user=actor,action='UPDATE',entity_type='accounts.user',entity_id=str(row.pk),old_value=old,new_value={**{k:v.isoformat() if hasattr(v,'isoformat') else v for k,v in new.items()},'company_id':actor.company_id},comment='Массовое архивирование пользователя' if action=='archive' else 'Восстановление пользователя из архива')
        changed+=1
    return changed

@login_required
@require_POST
def bulk_archive(request):
    require_roles(request.user,{'ADMIN'})
    if request.POST.get('stage')=='confirm':
        try:
            data=signing.loads(request.POST.get('token',''),salt=SALT,max_age=900)
        except signing.BadSignature:raise PermissionDenied('Подтверждение устарело. Выберите пользователей заново.')
        if data.get('actor')!=request.user.pk or data.get('company')!=request.user.company_id:raise PermissionDenied
        count=apply(request.user,data['ids'],data['action'])
        messages.success(request, f'В архив перемещено: {count}.' if data['action']=='archive' else f'Восстановлено: {count}.')
        return redirect(reverse('accounts:user_list')+('?archive=yes' if data['action']=='archive' else ''))
    action=request.POST.get('action')
    if action not in {'archive','restore'}:raise PermissionDenied
    from .views import UserListView
    view=UserListView();view.setup(request)
    query=view.get_queryset()
    if request.POST.get('all_filtered')=='yes':query=eligible(query,request.user)
    else:
        values=request.POST.getlist('selected')
        if not values or len(values)>5000 or any(not x.isdecimal() for x in values):
            messages.error(request,'Выберите пользователей.');return redirect(reverse('accounts:user_list'))
        ids={int(x) for x in values}
        query=query.filter(pk__in=ids)
        if query.count()!=len(ids) or eligible(query,request.user).count()!=len(ids):raise PermissionDenied('Выбор содержит недоступные учётные записи.')
    query=query.filter(archived_at__isnull=action=='archive')
    ids=list(query.values_list('pk',flat=True)[:5001])
    if not ids or len(ids)>5000:
        messages.error(request,'Выберите от 1 до 5000 пользователей.');return redirect(reverse('accounts:user_list'))
    token=signing.dumps({'ids':ids,'action':action,'actor':request.user.pk,'company':request.user.company_id},salt=SALT,compress=True)
    return render(request,'accounts/user_archive_confirm.html',{'selected_users':query[:100],'count':len(ids),'token':token,'action':action})
