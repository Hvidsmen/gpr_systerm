"""
Сервисы для планирования.
Вся бизнес-логика здесь, а не в моделях или views.
"""
import datetime
from decimal import Decimal, ROUND_HALF_UP
from django.db import transaction
from django.utils import timezone

from core.enums import PlanStatus, MismatchStrategy
from core.exceptions import PlanImmutableError, PlanGenerationError, InsufficientPermissionsError
from .models import (
    ProductionCalendar, CalendarDay, MonthlyPlan, PlanVersion,
    DailyPlan, DailyBaseline, LoadProfile
)


# =============================================================================
# КОНСТАНТЫ ДЛЯ КАЛЕНДАРЯ
# =============================================================================

# Праздничные дни РФ (фиксированные)
RUSSIAN_HOLIDAYS = [
    (1, 1), (1, 2), (1, 3), (1, 4), (1, 5), (1, 6), (1, 7), (1, 8),  # Новогодние каникулы
    (2, 23),  # День защитника Отечества
    (3, 8),   # Международный женский день
    (5, 1),   # Праздник Весны и Труда
    (5, 9),   # День Победы
    (6, 12),  # День России
    (11, 4),  # День народного единства
]

# Переносы выходных на 2025 год
RUSSIAN_HOLIDAY_SHIFTS_2025 = {
    datetime.date(2025, 1, 1): datetime.date(2025, 5, 2),
    datetime.date(2025, 1, 4): datetime.date(2025, 5, 3),
    datetime.date(2025, 1, 5): datetime.date(2025, 5, 5),
}

# Предпраздничные дни (сокращённые на 1 час)
RUSSIAN_SHORTENED_DAYS = [
    (2, 22),  # перед 23 февраля
    (3, 7),   # перед 8 марта
    (4, 30),  # перед 1 мая
    (5, 8),   # перед 9 мая
    (6, 11),  # перед 12 июня
    (11, 3),  # перед 4 ноября
]


# =============================================================================
# СЕРВИС КАЛЕНДАРЯ
# =============================================================================

class CalendarService:
    """Работа с производственным календарем."""

    @staticmethod
    def get_working_days(calendar, start_date, end_date):
        """Получить список рабочих дней в диапазоне."""
        return CalendarDay.objects.filter(
            calendar=calendar,
            date__gte=start_date,
            date__lte=end_date,
            is_working=True
        ).order_by('date')

    @staticmethod
    def count_working_days(calendar, start_date, end_date):
        """Посчитать количество рабочих дней."""
        return CalendarService.get_working_days(
            calendar, start_date, end_date
        ).count()

    @staticmethod
    def get_default_calendar(company, year=None):
        """Получить календарь по умолчанию."""
        if year is None:
            year = timezone.now().year
        return ProductionCalendar.objects.filter(
            company=company,
            year=year,
            is_default=True
        ).first()


# =============================================================================
# СЕРВИС АВТОЗАПОЛНЕНИЯ КАЛЕНДАРЯ
# =============================================================================

class CalendarAutoFillService:
    """Автоматическое заполнение календаря днями."""

    @staticmethod
    def generate_year(calendar, year, shifts=None):
        """
        Сгенерировать все дни года для календаря.

        Args:
            calendar: ProductionCalendar
            year: год
            shifts: dict {date: date} переносов выходных
        """
        if shifts is None:
            shifts = RUSSIAN_HOLIDAY_SHIFTS_2025 if year == 2025 else {}

        # Удаляем старые дни этого календаря
        CalendarDay.objects.filter(calendar=calendar).delete()

        days_to_create = []
        current_date = datetime.date(year, 1, 1)
        end_date = datetime.date(year, 12, 31)

        # Запоминаем компанию календаря
        company = calendar.company

        while current_date <= end_date:
            is_holiday = False
            is_shortened = False
            note = ''

            # Проверяем, является ли день праздником
            month_day = (current_date.month, current_date.day)
            if month_day in RUSSIAN_HOLIDAYS:
                is_holiday = True
                note = 'Праздничный день'

            # Проверяем переносы
            if current_date in shifts:
                # Этот день был перенесён — становится рабочим
                is_holiday = False
                note = f'Перенос на {shifts[current_date].strftime("%d.%m.%Y")}'

            # Проверяем, не является ли день выходным (суббота/воскресенье)
            is_weekend = current_date.weekday() >= 5  # 5=сб, 6=вс

            # Проверяем сокращённые дни
            if (current_date.month, current_date.day) in RUSSIAN_SHORTENED_DAYS:
                is_shortened = True
                note = 'Предпраздничный день (сокращённый)'

            # Рабочий день = не праздник и не выходной
            is_working = not is_holiday and not is_weekend

            days_to_create.append(CalendarDay(
                calendar=calendar,
                company=company,
                date=current_date,
                is_working=is_working,
                is_holiday=is_holiday,
                is_shortened=is_shortened,
                note=note
            ))

            current_date += datetime.timedelta(days=1)

        # Массовое создание (быстрее, чем по одному)
        CalendarDay.objects.bulk_create(days_to_create)

        return len(days_to_create)


# =============================================================================
# СЕРВИС РАСПРЕДЕЛЕНИЯ НАГРУЗКИ
# =============================================================================

class LoadDistributionService:
    """Адаптация профиля нагрузки под количество рабочих дней."""

    @staticmethod
    def adapt_profile(profile, available_days, strategy):
        """
        Адаптировать профиль под доступное количество дней.

        Args:
            profile: LoadProfile
            available_days: количество доступных рабочих дней
            strategy: STRETCH/COMPRESS/TRUNCATE/STRICT

        Returns:
            list of tuples: [(workday_number: int, percentage: Decimal), ...]
        """
        items = list(profile.items.order_by('workday_number'))
        profile_days = len(items)

        if profile_days == 0:
            raise PlanGenerationError("Профиль нагрузки пуст")

        # Считаем сумму процентов с явным преобразованием в Decimal
        total_percentage = Decimal('0')
        for item in items:
            total_percentage += Decimal(str(item.percentage))

        if total_percentage == 0:
            raise PlanGenerationError("Сумма процентов профиля равна 0")

        # Нормализуем к 100%
        if total_percentage != Decimal('100'):
            factor = Decimal('100') / total_percentage
            for item in items:
                item.percentage = (Decimal(str(item.percentage)) * factor).quantize(
                    Decimal('0.01'), rounding=ROUND_HALF_UP
                )
            # Корректировка округления
            new_total = Decimal('0')
            for item in items:
                new_total += Decimal(str(item.percentage))
            diff = Decimal('100') - new_total
            if diff != 0:
                items[-1].percentage = Decimal(str(items[-1].percentage)) + diff

        # Если количество дней совпадает — возвращаем как есть
        if profile_days == available_days:
            return [(int(i.workday_number), Decimal(str(i.percentage))) for i in items]

        # Применяем стратегию
        if strategy == MismatchStrategy.STRICT:
            raise PlanGenerationError(
                f"Профиль рассчитан на {profile_days} дней, "
                f"доступно {available_days}. Используйте другую стратегию."
            )

        if strategy == MismatchStrategy.STRETCH:
            return LoadDistributionService._stretch(items, available_days)
        elif strategy == MismatchStrategy.COMPRESS:
            return LoadDistributionService._compress(items, available_days)
        elif strategy == MismatchStrategy.TRUNCATE:
            return LoadDistributionService._truncate(items, available_days)

        raise PlanGenerationError(f"Неизвестная стратегия: {strategy}")

    @staticmethod
    def _stretch(items, target_days):
        """
        Растянуть профиль на большее/меньшее количество дней.
        items: список объектов с workday_number и percentage
        target_days: целевое количество дней
        """
        if not items:
            return []

        source_days = len(items)
        result = []

        # Распределяем проценты пропорционально
        for i in range(target_days):
            # Находим соответствующий день в исходном профиле
            source_index = int(i * source_days / target_days)
            if source_index >= source_days:
                source_index = source_days - 1

            result.append((i + 1, Decimal(str(items[source_index].percentage))))

        # Нормализуем к 100%
        return LoadDistributionService._normalize(result, Decimal('100'))

    @staticmethod
    def _compress(items, target_days):
        """
        Сжать профиль — берём первые target_days дней.
        items: список объектов с workday_number и percentage
        target_days: целевое количество дней
        """
        if not items:
            return []

        # Если целевое количество дней >= исходного, возвращаем как есть
        if target_days >= len(items):
            return [(int(i.workday_number), Decimal(str(i.percentage))) for i in items]

        # Берём первые target_days элементов
        result = [(i + 1, Decimal(str(items[i].percentage))) for i in range(target_days)]

        # Нормализуем к 100%
        return LoadDistributionService._normalize(result, Decimal('100'))

    @staticmethod
    def _truncate(items, target_days):
        """
        Обрезать профиль и нормализовать.
        items: список объектов с workday_number и percentage
        target_days: целевое количество дней
        """
        if not items:
            return []

        # Берём минимум из target_days и len(items)
        count = min(target_days, len(items))
        result = [(i + 1, Decimal(str(items[i].percentage))) for i in range(count)]

        # Нормализуем к 100%
        return LoadDistributionService._normalize(result, Decimal('100'))

    @staticmethod
    def _normalize(result, target_total=Decimal('100')):
        """
        Нормализовать проценты до суммы = target_total.
        result: list of (day_number, percentage)
        target_total: целевая сумма (обычно 100)
        """
        if not result:
            return result

        # Считаем текущую сумму
        current_total = Decimal('0')
        for _, p in result:
            current_total += Decimal(str(p))

        if current_total == 0:
            return result

        factor = target_total / current_total

        # Нормализуем
        normalized = []
        for day, p in result:
            p_decimal = Decimal(str(p))
            p_normalized = (p_decimal * factor).quantize(
                Decimal('0.01'), rounding=ROUND_HALF_UP
            )
            normalized.append((day, p_normalized))

        # Корректировка округления на последний день
        final_total = Decimal('0')
        for _, p in normalized:
            final_total += p
        diff = target_total - final_total
        if diff != 0:
            last_day, last_pct = normalized[-1]
            normalized[-1] = (last_day, last_pct + diff)

        return normalized


# =============================================================================
# СЕРВИС ГЕНЕРАЦИИ ПЛАНОВ
# =============================================================================

class PlanGeneratorService:
    """Генератор дневных планов из месячного."""

    @staticmethod
    @transaction.atomic
    def generate(plan_version):
        if plan_version.is_immutable:
            raise PlanImmutableError('Утверждённый или завершённый план нельзя перегенерировать.')
        monthly = plan_version.monthly_plan
        work = monthly.project_work
        items = list(work.items.all())

        if not items:
            raise PlanGenerationError("У работы нет подработ")

        calendar = CalendarService.get_default_calendar(work.company, monthly.year)
        if not calendar:
            raise PlanGenerationError(f"Не найден календарь по умолчанию на {monthly.year} год")

        working_days = list(CalendarService.get_working_days(
            calendar, monthly.start_date, monthly.end_date
        ))
        if not working_days:
            raise PlanGenerationError("Нет рабочих дней в периоде")

        # Удаляем старые дневные планы этой версии
        plan_version.daily_plans.all().delete()

        created_count = 0
        work_quantity = Decimal(str(monthly.planned_quantity or 0))
        work_unit_price = Decimal(str(work.unit_price))

        # ИСПРАВЛЕНО: общий план работы в деньгах
        work_total_value = work_quantity * work_unit_price

        for item in items:
            if not item.load_profile:
                continue

            adapted = LoadDistributionService.adapt_profile(
                item.load_profile,
                len(working_days),
                monthly.mismatch_strategy
            )

            # Определяем объём подработы через норматив
            if item.quantity_per_unit and item.quantity_per_unit > 0:
                item_total_qty = (work_quantity * Decimal(str(item.quantity_per_unit))).quantize(
                    Decimal('0.001'), rounding=ROUND_HALF_UP
                )
            else:
                weight = Decimal(str(item.weight)) if item.weight else Decimal('0')
                item_total_qty = (work_quantity * weight / Decimal('100')).quantize(
                    Decimal('0.001'), rounding=ROUND_HALF_UP
                )

            # ИСПРАВЛЕНО: план подработы в деньгах = общий план × вес / 100
            weight = Decimal(str(item.weight)) if item.weight else Decimal('0')
            item_total_value = (work_total_value * weight / Decimal('100')).quantize(
                Decimal('0.01'), rounding=ROUND_HALF_UP
            )

            # Для каждого рабочего дня создаём DailyPlan
            for idx, (workday_num, percentage) in enumerate(adapted):
                if idx >= len(working_days):
                    break

                day = working_days[idx]

                # Объём на день
                qty = (item_total_qty * Decimal(str(percentage)) / Decimal('100')).quantize(
                    Decimal('0.001'), rounding=ROUND_HALF_UP
                )

                # ИСПРАВЛЕНО: стоимость на день = план подработы × процент дня / 100
                value = (item_total_value * Decimal(str(percentage)) / Decimal('100')).quantize(
                    Decimal('0.01'), rounding=ROUND_HALF_UP
                )

                DailyPlan.objects.create(
                    plan_version=plan_version,
                    company=plan_version.company,
                    work_item=item,
                    date=day.date,
                    workday_number=workday_num,
                    planned_quantity=qty,
                    planned_value=value
                )
                created_count += 1

        return created_count
    @staticmethod
    def _validate_sum(plan_version, monthly):
        """Проверить, что сумма DailyPlan = planned_quantity."""
        total = Decimal('0')
        for dp in plan_version.daily_plans.all():
            total += dp.planned_quantity

        diff = abs(total - Decimal(str(monthly.planned_quantity)))
        if diff > Decimal('0.01'):
            raise PlanGenerationError(
                f"Сумма дневных планов ({total}) не равна "
                f"месячному плану ({monthly.planned_quantity})"
            )


# =============================================================================
# СЕРВИС WORKFLOW ПЛАНОВ
# =============================================================================

class PlanWorkflowService:
    """Управление статусами плана."""

    ALLOWED_TRANSITIONS = {
        PlanStatus.DRAFT: [PlanStatus.SUBMITTED],
        PlanStatus.SUBMITTED: [PlanStatus.APPROVED, PlanStatus.REJECTED],
        PlanStatus.REJECTED: [PlanStatus.DRAFT],
        PlanStatus.APPROVED: [PlanStatus.COMPLETED],
    }

    @staticmethod
    @transaction.atomic
    def submit(version, user):
        """Отправить версию на согласование."""
        if version.status == PlanStatus.REJECTED:
            PlanWorkflowService._transition(version, PlanStatus.DRAFT, user)
        PlanWorkflowService._transition(version, PlanStatus.SUBMITTED, user)

    @staticmethod
    @transaction.atomic
    def complete(version, user):
        """Завершить утверждённый план, сохранив его данные и baseline."""
        if user.company_id != version.company_id or not (user.is_manager() or user.is_admin()):
            raise InsufficientPermissionsError('Нет прав для завершения плана.')
        PlanWorkflowService._transition(version, PlanStatus.COMPLETED, user)

    @staticmethod
    @transaction.atomic
    def approve(version, user):
        """Утвердить версию плана."""
        PlanWorkflowService._transition(version, PlanStatus.APPROVED, user)
        version.approved_by = user
        version.approved_at = timezone.now()

        # Если это первая утверждённая версия — делаем baseline
        if not version.monthly_plan.versions.filter(is_baseline=True).exists():
            version.is_baseline = True
            PlanWorkflowService._create_baseline(version)

        version.save()

    @staticmethod
    @transaction.atomic
    def reject(version, user, comment=''):
        """Отклонить версию плана."""
        PlanWorkflowService._transition(version, PlanStatus.REJECTED, user)
        version.comment = comment
        version.save()

    @staticmethod
    def _transition(version, new_status, user):
        """Выполнить переход статуса."""
        completing = version.status == PlanStatus.APPROVED and new_status == PlanStatus.COMPLETED
        if version.is_immutable and not completing:
            raise PlanImmutableError(
                f"Версия {version.version_number} утверждена и не может быть изменена"
            )
        allowed = PlanWorkflowService.ALLOWED_TRANSITIONS.get(version.status, [])
        if new_status not in allowed:
            raise PlanGenerationError(
                f"Нельзя перейти из {version.status} в {new_status}"
            )
        version.status = new_status
        version.save()

    @staticmethod
    def _create_baseline(version):
        """Создать immutable снимок baseline."""
        for dp in version.daily_plans.all():
            DailyBaseline.objects.create(
                project_work=version.monthly_plan.project_work,
                company=version.company,
                work_item=dp.work_item,
                date=dp.date,
                baseline_quantity=dp.planned_quantity,
                baseline_value=dp.planned_value,
                source_version=version
            )


# =============================================================================
# СЕРВИС РЕВИЗИЙ ПЛАНОВ
# =============================================================================

class PlanRevisionService:
    """Создание ревизий плана."""

    @staticmethod
    @transaction.atomic
    def create_revision(previous_version, user):
        """
        Создать новую версию на основе предыдущей.
        """
        if not previous_version.is_immutable:
            raise PlanImmutableError(
                "Ревизию можно создавать только от утверждённой версии"
            )

        monthly = previous_version.monthly_plan
        new_number = monthly.versions.count() + 1

        new_version = PlanVersion.objects.create(
            monthly_plan=monthly,
            company=previous_version.company,
            version_number=new_number,
            status=PlanStatus.DRAFT,
            created_by=user,
            comment=f"Ревизия от {previous_version.version_number}"
        )

        # Копируем дневные планы
        for dp in previous_version.daily_plans.all():
            DailyPlan.objects.create(
                plan_version=new_version,
                company=new_version.company,
                work_item=dp.work_item,
                date=dp.date,
                workday_number=dp.workday_number,
                planned_quantity=dp.planned_quantity,
                planned_value=dp.planned_value
            )

        return new_version
