"""Delete plans with role checks while preserving production facts."""
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models.deletion import ProtectedError
from django.shortcuts import get_object_or_404, render, redirect
from django.views import View
from core.permissions import require_roles, PLAN_ROLES, role_code
from .models import PlanningWorkspace, GlobalPlanVersion

EDITABLE = ['DRAFT', 'REJECTED']


def workspace_reason(workspace, user=None):
    if user is not None and role_code(user) == 'ADMIN':
        return ''
    if workspace.versions.exclude(status__in=EDITABLE).exists() or workspace.versions.filter(review_rounds__isnull=False).exists():
        return 'План содержит версии на согласовании, утверждённые или завершённые версии. Удаление недоступно.'
    return ''


def version_reason(version):
    if version.subobject_smg_workspaces.exists():
        return 'С этой версией ГПР связаны СМГ субобъектов.'
    if version.review_rounds.exists():
        return "Версия содержит историю согласования. Удаление доступно администратору вместе с планом на период."
    if version.status not in EDITABLE:
        return 'Версию на согласовании, утверждённую или завершённую версию удалять нельзя.'
    if PlanningWorkspace.objects.filter(baseline_version=version).exists():
        return 'Базовую версию удаляют вместе с планом на период.'
    if version.revisions.exists():
        return 'От этой версии созданы другие редакции. Сначала удалите зависимые черновики.'
    return ''


@transaction.atomic
def delete_workspace(workspace, user, confirm_history=False, preview_token=None):
    require_roles(user, PLAN_ROLES)
    workspace = get_object_or_404(PlanningWorkspace.objects.select_for_update(), pk=workspace.pk, company=user.company)
    from core.permissions import scope_queryset
    if not scope_queryset(PlanningWorkspace.objects.filter(pk=workspace.pk),user).exists():
        from django.core.exceptions import PermissionDenied
        raise PermissionDenied('Объект не назначен пользователю.')
    from .deletion_graph import DeletionGraph,ROOT_MODELS
    graph=DeletionGraph(workspace,user)
    for label,model in ROOT_MODELS.items():list(model.objects.select_for_update().filter(pk__in=graph.ids[label]).values_list('pk',flat=True))
    graph=DeletionGraph(workspace,user)
    graph.check_permission()
    if (graph.history or graph.linked) and not confirm_history:
        raise ValidationError('Подтвердите удаление всех перечисленных планов, их версий и истории.')
    if graph.linked:graph.verify(preview_token)
    graph.delete()


@transaction.atomic
def delete_version(version, user):
    require_roles(user, PLAN_ROLES)
    version = get_object_or_404(GlobalPlanVersion.objects.select_for_update(), pk=version.pk, company=user.company)
    reason = version_reason(version)
    if reason:
        raise ValidationError(reason)
    version.delete()


class PlanDelete(View):
    model = None
    workspace_mode = False

    def record(self, request, pk):
        return get_object_or_404(self.model, pk=pk, company=request.user.company)

    def display(self, request, record, error=None, status=200, allow_retry=False):
        reason = workspace_reason(record, request.user) if self.workspace_mode else version_reason(record)
        graph=None
        if self.workspace_mode:
            from .deletion_graph import DeletionGraph
            graph=DeletionGraph(record,request.user)
            try:graph.check_permission()
            except ValidationError as exc:reason='; '.join(exc.messages)
        return render(request, 'planning/plan_delete.html', {
            'record': record, 'workspace_mode': self.workspace_mode,
            'deleting_history': bool(graph and (graph.history or graph.linked)),
            'deletion_groups':graph.groups if graph else [],'preview_token':graph.token() if graph else '',
            'has_linked_plans':bool(graph and graph.linked),
            'reason': error or reason, 'can_delete': not reason and (not error or allow_retry),
            'back_route': 'planning:workspace_list' if self.workspace_mode else 'planning:global_list',
        }, status=status)

    def get(self, request, pk):
        return self.display(request, self.record(request, pk))

    def post(self, request, pk):
        record = self.record(request, pk)
        if self.workspace_mode and role_code(request.user) == 'ADMIN' and workspace_reason(record) and request.POST.get('confirm_history') != 'on':
            return self.display(request, record, 'Подтвердите удаление всех перечисленных планов, их версий и истории.', status=400, allow_retry=True)
        try:
            if self.workspace_mode:
                delete_workspace(record, request.user, confirm_history=request.POST.get('confirm_history') == 'on',preview_token=request.POST.get('preview_token'))
            else:
                delete_version(record, request.user)
        except ValidationError as exc:
            return self.display(request, record, '; '.join(exc.messages), status=400,allow_retry=True)
        except ProtectedError:
            return self.display(request, record, 'Связи плана изменились во время удаления. Проверьте обновлённый список и повторите удаление.', status=400,allow_retry=True)
        messages.success(request, 'План удалён.' if self.workspace_mode else 'Глобальная версия удалена.')
        return redirect('planning:workspace_list' if self.workspace_mode else 'planning:global_list')


class WorkspaceDelete(PlanDelete):
    model = PlanningWorkspace
    workspace_mode = True


class GlobalDelete(PlanDelete):
    model = GlobalPlanVersion
