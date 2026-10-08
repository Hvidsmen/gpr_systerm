from django.db import models
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator, MaxValueValidator
from core.models import BaseCompanyModel

DAYS = [MinValueValidator(1), MaxValueValidator(365)]


class RotationModel(BaseCompanyModel):
    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)


class RotationPlan(RotationModel):
    title = models.CharField('Название', max_length=200)
    source = models.ForeignKey('planning.GlobalPlanVersion', on_delete=models.PROTECT, verbose_name='Версия общего плана')
    start = models.DateField('Начало периода')
    end = models.DateField('Конец периода')
    demand = models.JSONField(default=list, blank=True)

    def clean(self):
        if self.source_id and self.source.company_id != self.company_id:
            raise ValidationError('План другой компании.')
        if self.start and self.end:
            if self.end < self.start or (self.end-self.start).days > 730:
                raise ValidationError('Период должен быть от 1 дня до 2 лет.')
            if self.source_id and (self.start < self.source.start_date or self.end > self.source.end_date):
                raise ValidationError('Период должен находиться внутри периода общего плана.')

    def __str__(self):
        return self.title


class RotationRole(RotationModel):
    plan = models.ForeignKey(RotationPlan, on_delete=models.CASCADE, related_name='positions')
    brigade = models.ForeignKey('resources.Brigade', on_delete=models.PROTECT, verbose_name='Должность')
    on_days = models.PositiveIntegerField('Дней на вахте', default=45, validators=DAYS)
    off_days = models.PositiveIntegerField('Дней отдыха', default=45, validators=DAYS)
    anchor = models.DateField('Начало графика должности')

    class Meta:
        constraints = [models.UniqueConstraint(fields=['plan', 'brigade'], name='rotation_unique_position')]
        ordering = ['brigade__name', 'pk']

    def clean(self):
        if self.plan_id and self.plan.company_id != self.company_id or self.brigade_id and self.brigade.company_id != self.company_id:
            raise ValidationError('Должность или план другой компании.')

    def __str__(self):
        return str(self.brigade)


class RotationPerson(RotationModel):
    position = models.ForeignKey(RotationRole, on_delete=models.CASCADE, related_name='people')
    name = models.CharField('Обозначение человека', max_length=200)
    on_days = models.PositiveIntegerField('Дней на вахте', default=45, validators=DAYS)
    off_days = models.PositiveIntegerField('Дней отдыха', default=45, validators=DAYS)
    anchor = models.DateField('Дата начала вахты')

    class Meta:
        ordering = ['pk']

    def clean(self):
        if self.position_id and self.position.company_id != self.company_id:
            raise ValidationError('Должность другой компании.')

    def __str__(self):
        return self.name


class RotationStatus(RotationModel):
    person = models.ForeignKey(RotationPerson, on_delete=models.CASCADE, related_name='overrides')
    day = models.DateField('Дата')
    status = models.CharField('Статус', max_length=3, choices=[('ON', 'Вахта'), ('OFF', 'Отдых')])

    class Meta:
        constraints = [models.UniqueConstraint(fields=['person', 'day'], name='rotation_unique_person_day')]
        ordering = ['day']

    def clean(self):
        if self.person_id:
            if self.person.company_id != self.company_id:
                raise ValidationError('Человек другой компании.')
            plan = self.person.position.plan
            if self.day and not plan.start <= self.day <= plan.end:
                raise ValidationError('Дата за пределами плана перевахты.')
