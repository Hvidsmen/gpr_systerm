from django.core.exceptions import ValidationError
from django.db.models.signals import m2m_changed
from django.dispatch import receiver
from .models import GlobalPlanVersion, GlobalPlanReview, GlobalPlanDecision
from .deletion_context import version_deletion_authorized


@receiver(m2m_changed, sender=GlobalPlanVersion.source_versions.through)
def protect_global_sources(sender, instance, action, reverse, pk_set, **kwargs):
    if action not in ("pre_add", "pre_remove", "pre_clear"):
        return
    if reverse:
        versions = instance.global_versions.all()
        if pk_set is not None:
            versions = versions.filter(pk__in=pk_set)
    else:
        versions = GlobalPlanVersion.objects.filter(pk=instance.pk)
    if versions.filter(status__in=["SUBMITTED", "APPROVED", "COMPLETED"]).exists():
        raise ValidationError(
            "Источники отправленной или утверждённой глобальной версии менять нельзя."
        )


from django.db.models.signals import pre_delete
from .models import PlanVersion


@receiver(pre_delete, sender=PlanVersion)
def preserve_global_provenance(sender, instance, **kwargs):
    referenced = instance.global_versions.exists()
    if not referenced:
        from itertools import chain
        snapshots = chain(
            GlobalPlanVersion.objects.filter(company=instance.company).values_list("snapshot", flat=True),
            GlobalPlanReview.objects.filter(company=instance.company).values_list("snapshot", flat=True),
        )
        for snapshot in snapshots:
            if any(
                source["id"] == instance.pk
                for work in snapshot.get("works", [])
                for source in work.get("versions", [])
            ):
                referenced = True
                break
    if referenced:
        raise ValidationError(
            "План включён в глобальную версию. Его источник необходимо сохранить."
        )


@receiver(pre_delete, sender=GlobalPlanVersion)
def preserve_global_version(sender, instance, **kwargs):
    if version_deletion_authorized(instance.pk):
        return
    if instance.status in ["SUBMITTED", "APPROVED", "COMPLETED"] or instance.review_rounds.exists():
        raise ValidationError(
            "Отправленную или утверждённую глобальную версию удалять нельзя."
        )


from .models import WorkMonthAllocation, ResourceMonthAllocation


@receiver(pre_delete, sender=WorkMonthAllocation)
@receiver(pre_delete, sender=ResourceMonthAllocation)
def preserve_monthly_inputs(sender, instance, **kwargs):
    if version_deletion_authorized(instance.version_id):
        return
    if GlobalPlanVersion.objects.get(pk=instance.version_id).status not in [
        "DRAFT",
        "REJECTED",
    ]:
        raise ValidationError(
            "Месячные данные отправленного или утверждённого плана удалять нельзя."
        )


from apps.works.models import ProjectWorkItem


@receiver(pre_delete, sender=ProjectWorkItem)
def preserve_workspace_norm(sender, instance, **kwargs):
    if GlobalPlanVersion.objects.filter(
        work_allocations__work_id=instance.project_work_id,
        status__in=["SUBMITTED", "APPROVED", "COMPLETED"],
    ).exists():
        raise ValidationError(
            "Подработы отправленного или утверждённого плана удалять нельзя."
        )


@receiver(pre_delete, sender=GlobalPlanReview)
@receiver(pre_delete, sender=GlobalPlanDecision)
def preserve_review_history(sender, instance, **kwargs):
    version_id = instance.version_id if sender is GlobalPlanReview else instance.review.version_id
    if not version_deletion_authorized(version_id):
        raise ValidationError("Историю согласования нельзя удалять отдельно от плана.")


from .models import ProjectPlanVersion, ProjectPlanMember

@receiver(pre_delete, sender=ProjectPlanVersion)
@receiver(pre_delete, sender=ProjectPlanMember)
def preserve_project_composition(sender, instance, **kwargs):
    parent = instance if sender is ProjectPlanVersion else instance.consolidated_version
    if ProjectPlanVersion.objects.filter(pk=parent.pk, status='FIXED').exists():
        raise ValidationError('Зафиксированную сводную версию и её состав удалять нельзя.')
