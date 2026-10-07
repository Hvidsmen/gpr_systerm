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
def delete_workspace(workspace, user, confirm_history=False):
    require_roles(user, PLAN_ROLES)
    workspace = get_object_or_404(PlanningWorkspace.objects.select_for_update(), pk=workspace.pk, company=user.company)
    versions = list(workspace.versions.select_for_update().order_by('-pk'))
    reason = workspace_reason(workspace, user)
    if reason:
        raise ValidationError(reason)
    if workspace_reason(workspace) and not confirm_history:
        raise ValidationError('Подтвердите удаление согласованных версий и истории плана.')
    # Break the baseline link before removing the versions' PROTECT relations.
    PlanningWorkspace.objects.filter(pk=workspace.pk).update(baseline_version=None)
    from .deletion_context import authorized_workspace_deletion
    GlobalPlanVersion.objects.filter(pk__in=[v.pk for v in versions]).update(baseline_review=None)
    with authorized_workspace_deletion(versions):
        for version in versions:
            version.delete()
        workspace.delete()


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
        return render(request, 'planning/plan_delete.html', {
            'record': record, 'workspace_mode': self.workspace_mode,
            'deleting_history': self.workspace_mode and bool(workspace_reason(record)),
            'reason': error or reason, 'can_delete': not reason and (not error or allow_retry),
            'back_route': 'planning:workspace_list' if self.workspace_mode else 'planning:global_list',
        }, status=status)

    def get(self, request, pk):
        return self.display(request, self.record(request, pk))

    def post(self, request, pk):
        record = self.record(request, pk)
        if self.workspace_mode and role_code(request.user) == 'ADMIN' and workspace_reason(record) and request.POST.get('confirm_history') != 'on':
            return self.display(request, record, 'Подтвердите удаление согласованных версий и истории плана.', status=400, allow_retry=True)
        try:
            if self.workspace_mode:
                delete_workspace(record, request.user, confirm_history=request.POST.get('confirm_history') == 'on')
            else:
                delete_version(record, request.user)
        except ValidationError as exc:
            return self.display(request, record, '; '.join(exc.messages), status=400)
        except ProtectedError:
            return self.display(request, record, 'План связан с другими версиями. Удаление отменено.', status=400)
        messages.success(request, 'План удалён.' if self.workspace_mode else 'Глобальная версия удалена.')
        return redirect('planning:workspace_list' if self.workspace_mode else 'planning:global_list')


class WorkspaceDelete(PlanDelete):
    model = PlanningWorkspace
    workspace_mode = True


class GlobalDelete(PlanDelete):
    model = GlobalPlanVersion
