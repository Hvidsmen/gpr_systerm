"""
Кастомные исключения для системы ГПР.
"""


class GPRException(Exception):
    """Базовое исключение системы ГПР."""
    pass


class PlanImmutableError(GPRException):
    """
    Вызывается при попытке изменить утверждённый план.
    Утверждённые версии (APPROVED/COMPLETED) immutable.
    """
    pass


class PlanGenerationError(GPRException):
    """
    Вызывается при ошибке генерации дневного плана.
    Например: нет календаря, пустой профиль, несовпадение стратегии.
    """
    pass


class WorkflowTransitionError(GPRException):
    """
    Вызывается при недопустимом переходе статуса плана.
    Например: DRAFT → APPROVED (минуя SUBMITTED).
    """
    pass


class CalendarValidationError(GPRException):
    """
    Вызывается при невалидном производственном календаре.
    Например: нет рабочих дней в периоде.
    """
    pass


class LoadProfileValidationError(GPRException):
    """
    Вызывается при невалидном профиле нагрузки.
    Например: сумма процентов != 100.
    """
    pass


class InsufficientPermissionsError(GPRException):
    """
    Вызывается при недостатке прав у пользователя.
    """
    pass