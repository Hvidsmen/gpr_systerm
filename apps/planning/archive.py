"""Reversible removal of a workspace and its versions from active planning."""
from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.shortcuts import get_object_or_404, render, redirect
from django.utils import timezone
from django.views import View
from django.views.generic import ListView
from core.permissions import require_roles, PLAN_ROLES, scope_queryset, role_code
from apps.audit.models import AuditLog
from .models import PlanningWorkspace


def archive_reason(workspace,user):
    if role_code(user) != 'ADMIN' and (workspace.versions.exclude(status__in=['DRAFT','REJECTED']).exists() or workspace.versions.filter(review_rounds__isnull=False).exists()):
        return 'План с согласованными версиями и историей согласования может архивировать или восстановить администратор.'
    return ''


@transaction.atomic
def set_archived(user, pk, archived):
    require_roles(user,PLAN_ROLES)
    workspace=get_object_or_404(scope_queryset(PlanningWorkspace.objects.select_for_update(),user,include_archived=True),pk=pk)
    reason=archive_reason(workspace,user)
    if reason:raise PermissionDenied(reason)
    if workspace.is_archived == archived:return workspace
    workspace.is_archived=archived
    workspace.archived_at=timezone.now() if archived else None
    workspace.archived_by=user if archived else None
    workspace.save(update_fields=['is_archived','archived_at','archived_by','updated_at'])
    AuditLog.objects.create(user=user,action='UPDATE',entity_type='planning.planningworkspace',entity_id=str(workspace.pk),old_value={'is_archived':not archived},new_value={'is_archived':archived,'company_id':user.company_id},comment='План перемещён в архив' if archived else 'План восстановлен из архива')
    return workspace


class ArchiveList(ListView):
    model=PlanningWorkspace
    template_name='planning/workspace_list.html'
    paginate_by=50

    def get_queryset(self):
        require_roles(self.request.user,PLAN_ROLES)
        return scope_queryset(PlanningWorkspace.objects.all(),self.request.user,include_archived=True).filter(is_archived=True).select_related('construction_object','baseline_version')

    def get_context_data(self,**kwargs):
        context=super().get_context_data(**kwargs);context['archive_mode']=True
        return context


class WorkspaceArchive(View):
    def display(self,request,pk,error=None):
        require_roles(request.user,PLAN_ROLES)
        workspace=get_object_or_404(scope_queryset(PlanningWorkspace.objects.all(),request.user,include_archived=True),pk=pk)
        reason=archive_reason(workspace,request.user)
        return render(request,'planning/workspace_archive.html',{'workspace':workspace,'reason':reason,'error':error,'version_count':workspace.versions.count()})

    def get(self,request,pk):return self.display(request,pk)

    def post(self,request,pk):
        if request.POST.get('action') not in {'archive','restore'}:return self.display(request,pk,'Выберите действие.')
        if request.POST.get('confirm')!='on':return self.display(request,pk,'Подтвердите действие галочкой.')
        workspace=set_archived(request.user,pk,request.POST.get('action')=='archive')
        messages.success(request,'План помещён в архив.' if workspace.is_archived else 'План восстановлен из архива.')
        return redirect('planning:workspace_archive_list' if workspace.is_archived else 'planning:workspace_list')
