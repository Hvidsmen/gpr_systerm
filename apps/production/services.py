"""
Сервисы для ввода факта.
"""

from django.db import transaction
from core.exceptions import InsufficientPermissionsError

from .models import DailyFact, LaborFact, EquipmentFact, FuelFact


class FactService:
    """Ввод факта. Никогда не меняет план."""

    @staticmethod
    @transaction.atomic
    def record_daily_fact(
        project_work,
        work_item,
        date,
        actual_quantity,
        user,
        comment="",
        deviation_reason=None,
    ):
        """Записать дневной факт."""
        if user.company_id != project_work.company_id or (
            work_item and work_item.project_work_id != project_work.pk
        ):
            raise InsufficientPermissionsError(
                "Работа и подработа должны принадлежать компании пользователя."
            )
        probe = DailyFact(
            company=project_work.company,
            project_work=project_work,
            work_item=work_item,
            date=date,
            actual_quantity=actual_quantity,
            deviation_reason=deviation_reason,
        )
        probe.full_clean(validate_unique=False, validate_constraints=False)
        fact, created = DailyFact.objects.update_or_create(
            company=project_work.company,
            project_work=project_work,
            work_item=work_item,
            date=date,
            defaults={
                "actual_quantity": actual_quantity,
                "reported_by": user,
                "comment": comment,
                "deviation_reason": deviation_reason,
            },
        )
        return fact

    @staticmethod
    @transaction.atomic
    def record_labor_fact(
        construction_object,
        date,
        brigade,
        planned_count,
        actual_count,
        planned_hours,
        actual_hours,
        hourly_rate,
    ):
        if brigade.company_id != construction_object.company_id:
            raise InsufficientPermissionsError(
                "Бригада должна принадлежать компании работы."
            )
        probe = LaborFact(
            company=construction_object.company,
            construction_object=construction_object,
            brigade=brigade,
            date=date,
            planned_workers=planned_count,
            actual_workers=actual_count,
            planned_hours=planned_hours,
            actual_hours=actual_hours,
            hourly_rate=hourly_rate,
        )
        probe.full_clean(validate_unique=False, validate_constraints=False)
        fact, _ = LaborFact.objects.update_or_create(
            company=construction_object.company,
            construction_object=construction_object,
            date=date,
            brigade=brigade,
            defaults={
                "planned_workers": planned_count,
                "actual_workers": actual_count,
                "planned_hours": planned_hours,
                "actual_hours": actual_hours,
                "hourly_rate": hourly_rate,
            },
        )
        return fact
