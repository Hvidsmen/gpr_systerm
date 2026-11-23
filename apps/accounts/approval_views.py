from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.shortcuts import redirect, render

from core.permissions import require_roles
from .approval_assignments import PROCESS_STAGES, replace, sync_active
from .models import ApprovalAssignment, ApprovalAssignmentChange, ApprovalNotice, ApprovalObserver, Role, User


@login_required
def settings_view(request):
    require_roles(request.user,{'ADMIN'})
    if request.method=='POST':
        if request.POST.get('action')=='add_observer':
            try:
                user=User.objects.get(pk=request.POST.get('observer'),company=request.user.company,
                    is_active=True,archived_at__isnull=True)
                if not user.email:raise ValidationError('У выбранного пользователя не указан email.')
                _,created=ApprovalObserver.objects.get_or_create(company=request.user.company,user=user,
                    defaults={'added_by':request.user})
                messages.success(request,'Общий наблюдатель добавлен.' if created else 'Этот пользователь уже является наблюдателем.')
            except User.DoesNotExist:messages.error(request,'Выберите активного пользователя своей компании.')
            except ValidationError as exc:messages.error(request,'; '.join(exc.messages))
            return redirect('accounts:approval_settings')
        if request.POST.get('action')=='remove_observer':
            deleted,_=ApprovalObserver.objects.filter(pk=request.POST.get('observer_id'),company=request.user.company).delete()
            messages.success(request,'Наблюдатель удалён.' if deleted else 'Наблюдатель уже удалён.')
            return redirect('accounts:approval_settings')
        if request.POST.get('action')=='retry_mail':
            from .approval_mail import retry_failed
            count=retry_failed(request.user.company)
            messages.success(request,f'Повторная отправка поставлена в очередь: {count}.')
            return redirect('accounts:approval_settings')
        process=request.POST.get('process','');stage_code=request.POST.get('stage_code','')
        try:
            user=User.objects.get(pk=request.POST.get('user'),company=request.user.company)
            replace(company=request.user.company,process=process,stage_code=stage_code,user=user,
                    actor=request.user,reason=request.POST.get('reason',''))
            sync_active(request.user.company,process)
            messages.success(request,'Согласующий назначен. Открытые задания переназначены; завершённые решения не изменены.')
            return redirect('accounts:approval_settings')
        except (User.DoesNotExist,ValidationError) as exc:
            messages.error(request,'; '.join(exc.messages) if isinstance(exc,ValidationError) else 'Выберите пользователя своей компании.')
    assignments={(row.process,row.stage_code):row for row in ApprovalAssignment.objects.filter(company=request.user.company).select_related('user__role')}
    role_names=dict(Role.objects.values_list('code','name'))
    rows=[]
    for process,(process_label,stages) in PROCESS_STAGES.items():
        stage_rows=[]
        for code,(label,role) in stages.items():
            choices=User.objects.filter(company=request.user.company,is_active=True,archived_at__isnull=True,
                role__code__in={role,'ADMIN'}).select_related('role').order_by('last_name','first_name','username')
            stage_rows.append({'code':code,'label':label,'role':role,'role_label':role_names.get(role,role),'assignment':assignments.get((process,code)),'choices':choices})
        rows.append({'code':process,'label':process_label,'stages':stage_rows})
    history=ApprovalAssignmentChange.objects.filter(assignment__company=request.user.company).select_related(
        'assignment','previous_user','new_user','changed_by')[:100]
    notices=ApprovalNotice.objects.filter(company=request.user.company).select_related('intended_user')[:100]
    observers=ApprovalObserver.objects.filter(company=request.user.company).select_related('user__role','added_by')
    observer_choices=User.objects.filter(company=request.user.company,is_active=True,archived_at__isnull=True).exclude(
        email='').exclude(approval_observer_memberships__company=request.user.company).select_related('role').order_by(
        'last_name','first_name','username')
    return render(request,'accounts/approval_settings.html',{'processes':rows,'history':history,'notices':notices,
        'observers':observers,'observer_choices':observer_choices})
