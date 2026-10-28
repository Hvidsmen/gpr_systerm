from core.fact_history import ActualHistoryMixin
from django.conf import settings
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
    employee = models.OneToOneField('resources.Employee', on_delete=models.PROTECT, null=True, blank=True, related_name='personnel_card', verbose_name='Сотрудник производственного учёта')
    schedule_slot = models.ForeignKey('StaffingSlot', on_delete=models.SET_NULL, null=True, blank=True, related_name='primary_schedules', verbose_name='Строка графика')
    class Meta:
        ordering = ['full_name', 'pk']
        constraints = [models.UniqueConstraint(fields=['company', 'employee_number'], condition=~models.Q(employee_number=''), name='personnel_unique_employee_number')]
    def clean(self):
        super().clean()
        if self.employee_id and self.employee.company_id != self.company_id: raise ValidationError({'employee':'Сотрудник другой компании.'})
        if self.schedule_slot_id and (self.schedule_slot.company_id != self.company_id or self.schedule_slot.person_id != self.pk): raise ValidationError({'schedule_slot':'Выберите строку графика этого сотрудника.'})
    @property
    def display_employee_number(self):return self.employee.employee_number if self.employee_id else self.employee_number
    @property
    def display_name(self):return self.employee.display_name if self.employee_id else self.full_name
    def __str__(self): return self.display_name


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
    on_days = models.PositiveSmallIntegerField('Дней на вахте',default=0)
    off_days = models.PositiveSmallIntegerField('Дней межвахтового отдыха',default=0)
    cycle_codes = models.JSONField('Дни повторяющегося цикла',default=list,blank=True)
    class Meta:
        ordering = ['kind', 'name', 'pk']
    def clean(self):
        if self.board_id and self.board.company_id != self.company_id: raise ValidationError('График другой компании.')
        if bool(self.on_days)!=bool(self.off_days) or self.on_days>365 or self.off_days>365:raise ValidationError('Укажите обе длительности цикла от 1 до 365 дней.')
        if self.cycle_codes and (not self.on_days or not isinstance(self.cycle_codes,list) or len(self.cycle_codes)!=self.on_days+self.off_days):raise ValidationError('Число дней календаря цикла должно соответствовать вахте и отдыху.')
        from .importer import valid_code
        if any(not isinstance(code,str) or not valid_code(code) for code in self.cycle_codes):raise ValidationError('В цикле допустимы часы от 0 до 24 или МВ, МО, В, ДП.')
    def __str__(self): return ' · '.join(value for value in [self.kind,self.name] if value)


class StaffingSlot(PersonnelModel):
    STATUSES = [('МВ', 'МВ — на вахте'), ('МО', 'МО — межвахтовый отдых'), ('OTHER', 'Другое / уточнить')]
    group = models.ForeignKey(StaffingGroup, on_delete=models.CASCADE, related_name='slots')
    person = models.ForeignKey(Person, null=True, blank=True, on_delete=models.PROTECT, related_name='assignments', verbose_name='Сотрудник (пусто — вакансия)')
    camp = models.CharField('ВЖГ из исходного файла', max_length=100, blank=True)
    housing_camp = models.ForeignKey('Camp', on_delete=models.PROTECT, null=True, blank=True, verbose_name='ВЖГ')
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
        for name in ['group', 'person', 'pattern', 'housing_camp']:
            obj = getattr(self, name, None)
            if obj and obj.company_id != self.company_id: raise ValidationError('Данные другой компании.')
        if self.pattern_id and self.group_id and self.pattern.board_id != self.group.board_id: raise ValidationError('График другого объекта.')
        if not self.person_id and self.status: raise ValidationError('У вакансии не может быть фактического статуса.')


class Camp(PersonnelModel):
    name = models.CharField('Название ВЖГ', max_length=200)
    address = models.CharField('Расположение / адрес', max_length=300, blank=True)
    is_active = models.BooleanField('Действующий ВЖГ', default=True)
    objects_served = models.ManyToManyField('projects.ConstructionObject', through='CampObjectLink', related_name='housing_camps', verbose_name='Связанные объекты проекта')
    class Meta:
        ordering = ['name','pk']
        constraints = [models.UniqueConstraint(fields=['company','name'], name='unique_company_camp_name')]
    def clean(self):
        super().clean()
        self.name = ' '.join(self.name.split())
        if not self.name:raise ValidationError({'name':'Укажите название ВЖГ.'})
        if any(' '.join(name.split()).casefold()==self.name.casefold() for name in type(self).objects.filter(company_id=self.company_id).exclude(pk=self.pk).values_list('name',flat=True)):
            raise ValidationError({'name':'Такой ВЖГ уже существует.'})
    def __str__(self): return self.name


class CampObjectLink(PersonnelModel):
    camp = models.ForeignKey(Camp,on_delete=models.CASCADE,related_name='object_links')
    construction_object = models.ForeignKey('projects.ConstructionObject',on_delete=models.CASCADE,related_name='camp_links')
    class Meta:
        constraints = [models.UniqueConstraint(fields=['camp','construction_object'],name='unique_camp_object_link')]
    def clean(self):
        super().clean()
        if self.camp.company_id != self.company_id or self.construction_object.company_id != self.company_id:
            raise ValidationError('Связь ВЖГ и объекта другой компании.')


class CampStay(ActualHistoryMixin, PersonnelModel):
    person = models.ForeignKey(Person,on_delete=models.PROTECT,related_name='stays')
    camp = models.ForeignKey(Camp,on_delete=models.PROTECT,related_name='stays')
    arrived_on = models.DateField('Фактический заезд')
    departed_on = models.DateField('Фактический выезд',null=True,blank=True)
    comment = models.TextField('Комментарий',blank=True)
    recorded_by = models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.SET_NULL,null=True,blank=True,related_name='+')
    cancelled_at = models.DateTimeField(null=True,blank=True)
    cancelled_by = models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.SET_NULL,null=True,blank=True,related_name='+')
    cancellation_reason = models.CharField(max_length=500,blank=True)
    class Meta:
        ordering = ['-arrived_on','-pk']
        indexes = [models.Index(fields=['company','person','arrived_on','departed_on'],name='camp_stay_presence')]
        constraints = [models.CheckConstraint(condition=models.Q(departed_on__isnull=True)|models.Q(departed_on__gte=models.F('arrived_on')),name='camp_departure_after_arrival')]
    def clean(self):
        super().clean()
        for name in ('person','camp'):
            if getattr(self,name).company_id != self.company_id:raise ValidationError('Размещение другой компании.')
        if self.departed_on and self.arrived_on and self.departed_on < self.arrived_on:raise ValidationError({'departed_on':'Выезд не может быть раньше заезда.'})
        if not self.cancelled_at and self.arrived_on and self.departed_on != self.arrived_on:
            overlapping=type(self).objects.filter(company_id=self.company_id,person_id=self.person_id,cancelled_at=None).exclude(pk=self.pk).exclude(departed_on=models.F('arrived_on')).filter(models.Q(departed_on=None)|models.Q(departed_on__gt=self.arrived_on))
            if self.departed_on:overlapping=overlapping.filter(arrived_on__lt=self.departed_on)
            if overlapping.exists():raise ValidationError('У сотрудника уже есть размещение на этот период. Оформите перевод или исправьте историю.')
    def __str__(self):return f'{self.person} · {self.camp} · {self.arrived_on}'


class HousingEvent(ActualHistoryMixin, PersonnelModel):
    KINDS=[('ARRIVAL','Заезд'),('DEPARTURE','Выезд'),('TRANSFER','Перевод'),('CANCEL','Отмена записи')]
    departure_destination=models.CharField('Куда выезжает',max_length=10,choices=[('REST','На межвахтовый отдых'),('CAMP','В другой ВЖГ'),('DISMISSAL','По увольнению')],blank=True)
    person=models.ForeignKey(Person,on_delete=models.PROTECT,related_name='housing_events')
    date=models.DateField()
    kind=models.CharField(max_length=12,choices=KINDS)
    from_camp=models.ForeignKey(Camp,on_delete=models.PROTECT,null=True,blank=True,related_name='+')
    to_camp=models.ForeignKey(Camp,on_delete=models.PROTECT,null=True,blank=True,related_name='+')
    stay=models.ForeignKey(CampStay,on_delete=models.PROTECT,null=True,blank=True,related_name='events')
    comment=models.TextField(blank=True)
    recorded_by=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.SET_NULL,null=True,blank=True,related_name='+')
    class Meta:
        ordering=['-date','-pk']
    def clean(self):
        super().clean()
        for name in ('person','from_camp','to_camp','stay'):
            obj=getattr(self,name,None)
            if obj and obj.company_id!=self.company_id:raise ValidationError('Событие другой компании.')

from .lodging_models import LodgingBuilding,LodgingBed,LodgingStay,LodgingEvent,LodgingControl,LodgingResolution
