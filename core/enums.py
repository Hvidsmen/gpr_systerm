from django.db import models
from django.utils.translation import gettext_lazy as _


class UserRole(models.TextChoices):
    ADMIN = 'ADMIN', _('Администратор')
    PLANNER = 'PLANNER', _('Планировщик')
    MANAGER = 'MANAGER', _('Руководитель')
    FOREMAN = 'FOREMAN', _('Мастер')
    PRODUCTION_HEAD = 'PRODUCTION_HEAD', _('Руководитель производства')
    HR_HEAD = 'HR_HEAD', _('Руководитель кадровой службы')
    TECH_HEAD = 'TECH_HEAD', _('Руководитель технической службы')
    CEO = 'CEO', _('Генеральный директор')


class ProjectStatus(models.TextChoices):
    ACTIVE = 'ACTIVE', _('Активный')
    ARCHIVED = 'ARCHIVED', _('Архивный')
    CLOSED = 'CLOSED', _('Закрытый')


class PlanStatus(models.TextChoices):
    DRAFT = 'DRAFT', _('Черновик')
    SUBMITTED = 'SUBMITTED', _('На согласовании')
    APPROVED = 'APPROVED', _('Утвержден')
    REJECTED = 'REJECTED', _('Отклонен')
    COMPLETED = 'COMPLETED', _('Завершен')


class MismatchStrategy(models.TextChoices):
    STRETCH = 'STRETCH', _('Растянуть')
    COMPRESS = 'COMPRESS', _('Сжать')
    TRUNCATE = 'TRUNCATE', _('Обрезать')
    STRICT = 'STRICT', _('Строгий режим')


class DeviationReasonCode(models.TextChoices):
    WEATHER = 'WEATHER', _('Погодные условия')
    EQUIPMENT = 'EQUIPMENT', _('Техника')
    LABOR = 'LABOR', _('Люди')
    MATERIALS = 'MATERIALS', _('Материалы')
    ORGANIZATION = 'ORGANIZATION', _('Организация')
    ACCIDENT = 'ACCIDENT', _('Авария/Инцидент')
    DOWNTIME = 'DOWNTIME', _('Простой')
    OTHER = 'OTHER', _('Другое')


class AuditAction(models.TextChoices):
    CREATE = 'CREATE', _('Создание')
    UPDATE = 'UPDATE', _('Обновление')
    DELETE = 'DELETE', _('Удаление')
    SUBMIT = 'SUBMIT', _('Отправка на согласование')
    APPROVE = 'APPROVE', _('Утверждение')
    REJECT = 'REJECT', _('Отклонение')
    REVISION = 'REVISION', _('Создание ревизии')
    GENERATE = 'GENERATE', _('Генерация плана')