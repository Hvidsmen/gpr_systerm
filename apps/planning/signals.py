from django.core.exceptions import ValidationError
from django.db.models.signals import m2m_changed
from django.dispatch import receiver
from .models import GlobalPlanVersion


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
        for snapshot in GlobalPlanVersion.objects.filter(
            company=instance.company
        ).values_list("snapshot", flat=True):
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
    if instance.status in ["SUBMITTED", "APPROVED", "COMPLETED"]:
        raise ValidationError(
            "Отправленную или утверждённую глобальную версию удалять нельзя."
        )
