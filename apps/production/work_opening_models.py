"""Opening work quantities are historical stock, separate from daily output."""
from datetime import date
from decimal import Decimal
from django.db import models
from django.core.exceptions import ValidationError
from core.models import BaseCompanyModel
from core.fact_history import ActualHistoryMixin


class WorkOpeningBalance(ActualHistoryMixin, BaseCompanyModel):
    project_work = models.ForeignKey('works.ProjectWork', on_delete=models.CASCADE, related_name='opening_balances')
    work_item = models.ForeignKey('works.ProjectWorkItem', on_delete=models.CASCADE, null=True, blank=True, related_name='opening_balances')
    date = models.DateField('На начало дня')
    quantity = models.DecimalField('Выполнено до начала учёта', max_digits=15, decimal_places=3, default=0)

    class Meta:
        verbose_name = 'входящий остаток работы'
        verbose_name_plural = 'входящие остатки работ'
        constraints = [
            models.UniqueConstraint(fields=['project_work', 'work_item'], condition=models.Q(work_item__isnull=False), name='work_opening_item_unique'),
            models.UniqueConstraint(fields=['project_work'], condition=models.Q(work_item__isnull=True), name='work_opening_simple_unique'),
            models.CheckConstraint(condition=models.Q(quantity__gte=0), name='work_opening_nonnegative'),
        ]

    def __str__(self):
        return f'{self.work_item or self.project_work} · входящий остаток на {self.date:%d.%m.%Y}'

    def clean(self):
        super().clean()
        if self.date and self.date <= date.min:
            raise ValidationError({'date': 'Выберите дату после 01.01.0001.'})
        if self.quantity is None or not Decimal(self.quantity).is_finite() or self.quantity < Decimal(0):
            raise ValidationError({'quantity': 'Объём должен быть неотрицательным числом.'})
        if not self.project_work_id:
            return
        work = self.project_work
        if work.company_id != self.company_id:
            raise ValidationError('Работа другой компании.')
        if hasattr(work, 'merged_source'):
            raise ValidationError('Исходная работа архивная. Откройте остатки комплекса.')
        if (work.kind == 'COMPOSITE') != bool(self.work_item_id):
            raise ValidationError('Для комплекса укажите работу в его составе; для самостоятельной работы состав не указывается.')
        if self.work_item_id and (self.work_item.company_id != self.company_id or self.work_item.project_work_id != work.pk):
            raise ValidationError('Работа в составе другого комплекса или компании.')
        from .models import DailyFact
        if self.quantity and self.date and DailyFact.objects.filter(company_id=self.company_id, project_work_id=work.pk,
                work_item_id=self.work_item_id, date__lt=self.date, actual_quantity__gt=0).exists():
            raise ValidationError('До этой даты уже есть дневной факт. Выберите начало учёта не позже первого факта, чтобы не задвоить объём.')
