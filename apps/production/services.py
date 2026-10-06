"""
Сервисы для ввода факта.
"""
from decimal import Decimal
from django.db import transaction
from django.utils import timezone

from .models import DailyFact, LaborFact, EquipmentFact, FuelFact


class FactService:
    """Ввод факта. Никогда не меняет план."""

    @staticmethod
    @transaction.atomic
    def record_daily_fact(
        project_work, work_item, date, actual_quantity,
        user, comment='', deviation_reason=None
    ):
        """Записать дневной факт."""
        actual_value = (
            actual_quantity * project_work.unit_price
        ).quantize(Decimal('0.01'))

        fact, created = DailyFact.objects.update_or_create(
            project_work=project_work,
            work_item=work_item,
            date=date,
            defaults={
                'actual_quantity': actual_quantity,
                'actual_value': actual_value,
                'reported_by': user,
                'comment': comment,
                'deviation_reason': deviation_reason,
            }
        )
        return fact

    @staticmethod
    @transaction.atomic
    def record_labor_fact(
        project_work, date, brigade,
        planned_count, actual_count,
        planned_hours, actual_hours, hourly_rate
    ):
        fact, _ = LaborFact.objects.update_or_create(
            project_work=project_work,
            date=date,
            brigade=brigade,
            defaults={
                'planned_count': planned_count,
                'actual_count': actual_count,
                'planned_hours': planned_hours,
                'actual_hours': actual_hours,
                'hourly_rate': hourly_rate,
            }
        )
        return fact