"""Freeze complete project compositions; never replace an object implicitly."""

from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Max
from django.utils import timezone
from core.permissions import PLAN_ROLES, require_roles
from apps.projects.models import Project
from apps.works.models import ProjectWork
from apps.resources.models import Brigade, EquipmentType
from .models import GlobalPlanVersion, ProjectPlanVersion, ProjectPlanMember
from .approval_workflow import current_review

_authorized = ContextVar("project_plan_fixation", default=frozenset())


def fixation_authorized(pk):
    return pk in _authorized.get()


@contextmanager
def authorized_fixation(pk):
    token = _authorized.set(_authorized.get() | {pk})
    try:
        yield
    finally:
        _authorized.reset(token)


def snapshot_with_catalogs(version):
    """Store grouping and units alongside quantities, so fixed reports stay stable."""
    snapshot = deepcopy(version.snapshot)
    snapshot["object_name"] = version.construction_object.name
    snapshot["project_id"] = version.construction_object.project_id
    work_ids = [spec["id"] for spec in snapshot.get("works", [])]
    works = {
        work.pk: work
        for work in ProjectWork.objects.filter(
            company=version.company, pk__in=work_ids
        ).select_related("work_group")
    }
    for spec in snapshot.get("works", []):
        work = works.get(spec["id"])
        if work:
            spec.update(
                group_id=work.work_group_id,
                group_name=work.work_group.name if work.work_group_id else "Без группы",
            )
    catalogs = {"labor": {}, "equipment": {}}
    inputs = snapshot.get("monthly_inputs", {}).get("resources", [])
    labor_ids = {
        row["brigade_id"] for row in snapshot.get("resources", {}).get("labor", [])
    } | {row["brigade_id"] for row in inputs if row["kind"] == "labor"}
    equipment_ids = {
        row["equipment_type_id"]
        for row in snapshot.get("resources", {}).get("equipment", [])
    } | {row["equipment_type_id"] for row in inputs if row["kind"] == "equipment"}
    for row in Brigade.objects.filter(
        company=version.company, pk__in=labor_ids
    ).select_related("macro_group", "group"):
        catalogs["labor"][str(row.pk)] = {
            "name": row.name,
            "unit": row.unit,
            "macro_group_id": row.macro_group_id,
            "macro_group_name": (
                row.macro_group.name if row.macro_group_id else "Без макрогруппы"
            ),
            "group_id": row.group_id,
            "group_name": row.group.name if row.group_id else "Без группы",
        }
    for row in EquipmentType.objects.filter(
        company=version.company, pk__in=equipment_ids
    ).select_related("category"):
        catalogs["equipment"][str(row.pk)] = {
            "name": row.name,
            "unit": row.unit,
            "category_id": row.category_id,
            "category_name": row.category.name if row.category_id else "Без категории",
        }
    snapshot["catalogs"] = catalogs
    return snapshot


def check_company(user, record):
    require_roles(user, PLAN_ROLES)
    if record.company_id != user.company_id:
        raise PermissionDenied("Данные другой компании.")


class ProjectPlanService:
    @staticmethod
    @transaction.atomic
    def create(user, project, title, start, end):
        check_company(user, project)
        Project.objects.select_for_update().get(pk=project.pk)
        number = (
            ProjectPlanVersion.objects.filter(project=project).aggregate(
                n=Max("version_number")
            )["n"]
            or 0
        ) + 1
        return ProjectPlanVersion.objects.create(
            company=user.company,
            project=project,
            title=title,
            start_date=start,
            end_date=end,
            version_number=number,
            created_by=user,
        )

    @staticmethod
    @transaction.atomic
    def assign(user, parent, version, replace=False):
        check_company(user, parent)
        check_company(user, version)
        parent = ProjectPlanVersion.objects.select_for_update().get(pk=parent.pk)
        version = (
            GlobalPlanVersion.objects.select_for_update()
            .select_related("construction_object")
            .get(pk=version.pk)
        )
        if parent.status != "DRAFT":
            raise ValidationError(
                "Состав уже зафиксирован. Создайте новую сводную версию."
            )
        existing = parent.members.filter(
            construction_object=version.construction_object
        ).first()
        if existing:
            if existing.version_id == version.pk:
                raise ValidationError("Эта версия объекта уже включена в состав.")
            if not replace:
                raise ValidationError(
                    "Объект уже включён. Для замены явно отметьте «Заменить версию объекта»."
                )
            existing.version = version
            existing.save()
            return existing
        return ProjectPlanMember.objects.create(
            company=user.company,
            consolidated_version=parent,
            construction_object=version.construction_object,
            version=version,
        )

    @staticmethod
    @transaction.atomic
    def remove(user, parent, member_id):
        check_company(user, parent)
        parent = ProjectPlanVersion.objects.select_for_update().get(pk=parent.pk)
        if parent.status != "DRAFT":
            raise ValidationError("Зафиксированный состав менять нельзя.")
        parent.members.get(pk=member_id).delete()

    @staticmethod
    @transaction.atomic
    def fix(user, parent):
        check_company(user, parent)
        parent = ProjectPlanVersion.objects.select_for_update().get(pk=parent.pk)
        if parent.status != "DRAFT":
            raise ValidationError("Состав уже зафиксирован.")
        members = list(parent.members.select_related("construction_object", "version"))
        if not members:
            raise ValidationError("Добавьте хотя бы один согласованный план объекта.")
        versions = {
            v.pk: v
            for v in GlobalPlanVersion.objects.select_for_update()
            .filter(pk__in=[m.version_id for m in members])
            .select_related("construction_object")
            .order_by("pk")
        }
        with authorized_fixation(parent.pk):
            for member in members:
                member.version = versions[member.version_id]
                member.review = current_review(member.version)
                if not member.review or not member.review.was_approved:
                    raise ValidationError(
                        "У плана объекта нет утверждённого раунда. Выполните миграции."
                    )
                member.snapshot = snapshot_with_catalogs(member.version)
                member.save()
            parent.status = "FIXED"
            parent.fixed_by = user
            parent.fixed_at = timezone.now()
            parent.save()
        return parent

    @staticmethod
    @transaction.atomic
    def revision(user, previous):
        check_company(user, previous)
        if previous.status != "FIXED":
            raise ValidationError(
                "Новая редакция создаётся от зафиксированной сводной версии."
            )
        result = ProjectPlanService.create(
            user,
            previous.project,
            previous.title,
            previous.start_date,
            previous.end_date,
        )
        result.previous_version = previous
        result.save()
        for member in previous.members.select_related("version", "construction_object"):
            # A revoked source must be selected again after reapproval, never silently substituted.
            if member.version.is_immutable:
                ProjectPlanService.assign(user, result, member.version)
        return result
