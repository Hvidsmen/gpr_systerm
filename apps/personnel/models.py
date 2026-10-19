from django.db import models
from django.core.exceptions import ValidationError
from core.models import BaseCompanyModel


class PersonnelModel(BaseCompanyModel):
    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)


class Board(PersonnelModel):
    name = models.CharField('Название', max_length=200)
    project = models.CharField('Проект', max_length=200, blank=True)
    site = models.CharField('Объект', max_length=200, blank=True)
    import_digest = models.CharField(max_length=64, blank=True, editable=False)
    class Meta:
        ordering = ['name', 'pk']
    def __str__(self): return self.name


class Person(PersonnelModel):
    full_name = models.CharField('ФИО', max_length=250)
    employee_number = models.CharField('Табельный номер', max_length=60, blank=True)
    phone = models.CharField('Телефон', max_length=80, blank=True)
    notes = models.TextField('Примечание', blank=True)
    class Meta:
        ordering = ['full_name', 'pk']
        constraints = [models.UniqueConstraint(fields=['company', 'employee_number'], condition=~models.Q(employee_number=''), name='personnel_unique_employee_number')]
    def __str__(self): return self.full_name


class StaffingGroup(PersonnelModel):
    board = models.ForeignKey(Board, on_delete=models.CASCADE, related_name='groups')
    position = models.CharField('Должность', max_length=250)
    attendance_plan = models.PositiveIntegerField('План по явке', default=0)
    staffing_plan = models.PositiveIntegerField('План по штатному расписанию', default=0)
    order = models.PositiveIntegerField(default=0)
    class Meta:
        ordering = ['order', 'pk']
    def clean(self):
        if self.board_id and self.board.company_id != self.company_id: raise ValidationError('Объект другой компании.')
    def __str__(self): return self.position


class ShiftPattern(PersonnelModel):
    board = models.ForeignKey(Board, on_delete=models.CASCADE, related_name='patterns')
    name = models.CharField('График', max_length=150)
    kind = models.CharField('Вид графика', max_length=20, blank=True)
    calendar = models.JSONField(default=dict, blank=True)
    class Meta:
        ordering = ['kind', 'name', 'pk']
    def clean(self):
        if self.board_id and self.board.company_id != self.company_id: raise ValidationError('График другой компании.')
    def __str__(self): return f'{self.kind} · {self.name}'


class StaffingSlot(PersonnelModel):
    STATUSES = [('МВ', 'МВ — на вахте'), ('МО', 'МО — межвахтовый отдых'), ('OTHER', 'Другое / уточнить')]
    group = models.ForeignKey(StaffingGroup, on_delete=models.CASCADE, related_name='slots')
    person = models.ForeignKey(Person, null=True, blank=True, on_delete=models.PROTECT, related_name='assignments', verbose_name='Сотрудник (пусто — вакансия)')
    camp = models.CharField('ВЖГ', max_length=100, blank=True)
    status = models.CharField('Текущий статус', choices=STATUSES, max_length=10, blank=True)
    departure_date = models.DateField('Дата выезда на межвахтовый отдых', null=True, blank=True)
    arrival_date = models.DateField('Дата заезда на участок', null=True, blank=True)
    pattern = models.ForeignKey(ShiftPattern, on_delete=models.PROTECT, null=True, blank=True, verbose_name='График')
    calendar = models.JSONField(default=dict, blank=True)
    comment = models.TextField('Комментарии', blank=True)
    source_row = models.PositiveIntegerField(null=True, blank=True, editable=False)
    class Meta:
        ordering = ['group__order', 'pk']
    def clean(self):
        for name in ['group', 'person', 'pattern']:
            obj = getattr(self, name, None)
            if obj and obj.company_id != self.company_id: raise ValidationError('Данные другой компании.')
        if self.pattern_id and self.group_id and self.pattern.board_id != self.group.board_id: raise ValidationError('График другого объекта.')
        if not self.person_id and self.status: raise ValidationError('У вакансии не может быть фактического статуса.')
