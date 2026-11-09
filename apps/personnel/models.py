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
    dispatcher_removed_at=models.DateTimeField('Удалён из диспетчерской',null=True,blank=True)
    dispatcher_removed_by=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.SET_NULL,null=True,blank=True,related_name='+',verbose_name='Удалил из диспетчерской')
    dispatcher_removal_reason=models.TextField('Причина удаления из диспетчерской',blank=True)
    class Meta:
        ordering = ['full_name', 'pk']
        constraints = [models.UniqueConstraint(fields=['company', 'employee_number'], condition=~models.Q(employee_number=''), name='personnel_unique_employee_number')]
    def clean(self):
        super().clean()
        if self.employee_id and self.employee.company_id != self.company_id: raise ValidationError({'employee':'Сотрудник другой компании.'})
        if self.schedule_slot_id and (self.schedule_slot.company_id != self.company_id or self.schedule_slot.person_id != self.pk): raise ValidationError({'schedule_slot':'Выберите строку графика этого сотрудника.'})
        if self.dispatcher_removed_by_id and self.dispatcher_removed_by.company_id != self.company_id:raise ValidationError({'dispatcher_removed_by':'Пользователь другой компании.'})
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


class ZupWorkSchedule(PersonnelModel):
    source_ref=models.CharField('Идентификатор ЗУП',max_length=32,blank=True)
    name=models.CharField('График ЗУП',max_length=250)
    is_active=models.BooleanField('Доступен в ЗУП',default=True)
    synced_at=models.DateTimeField('Обновлён из ЗУП',null=True,blank=True)
    class Meta:
        ordering=['name','pk']
        constraints=[models.UniqueConstraint(fields=['company','name'],name='personnel_unique_zup_schedule_name')]
    def __str__(self):return self.name


class StaffingSlot(PersonnelModel):
    STATUSES = [('МВ', 'МВ — на вахте'), ('МО', 'МО — межвахтовый отдых'), ('OTHER', 'Другое / уточнить')]
    group = models.ForeignKey(StaffingGroup, on_delete=models.CASCADE, related_name='slots')
    person = models.ForeignKey(Person, null=True, blank=True, on_delete=models.PROTECT, related_name='assignments', verbose_name='Сотрудник (пусто — вакансия)')
    planning_position = models.ForeignKey('resources.Brigade', on_delete=models.PROTECT, null=True, blank=True, related_name='personnel_assignments', verbose_name='Должность плана')
    construction_object = models.ForeignKey('projects.ConstructionObject', on_delete=models.PROTECT, null=True, blank=True, related_name='personnel_assignments', verbose_name='Объект назначения')
    assigned_from = models.DateField('Назначен с', null=True, blank=True)
    assigned_until = models.DateField('Назначен по (включительно)', null=True, blank=True)
    initial_movement = models.ForeignKey('HousingEvent', on_delete=models.PROTECT, null=True, blank=True, related_name='initial_assignments', verbose_name='Начальная запись перемещения')
    previous_assignment = models.OneToOneField('self', on_delete=models.PROTECT, null=True, blank=True, related_name='next_assignment', verbose_name='Предыдущее назначение')
    replaced_on = models.DateField('Заменено с', null=True, blank=True)
    assigned_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='+', verbose_name='Назначил')
    cancelled_at = models.DateTimeField('Назначение удалено', null=True, blank=True)
    cancelled_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='+', verbose_name='Удалил назначение')
    cancellation_reason = models.TextField('Комментарий к удалению назначения', blank=True)
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
        constraints = [models.CheckConstraint(condition=models.Q(assigned_until__isnull=True)|models.Q(assigned_until__gte=models.F('assigned_from')), name='personnel_assignment_dates'),
                       models.CheckConstraint(condition=models.Q(replaced_on__isnull=True)|models.Q(replaced_on__gte=models.F('assigned_from')), name='personnel_replacement_date')]
    def clean(self):
        for name in ['group', 'person', 'pattern', 'housing_camp', 'planning_position', 'construction_object', 'initial_movement', 'previous_assignment', 'assigned_by', 'cancelled_by']:
            obj = getattr(self, name, None)
            if obj and obj.company_id != self.company_id: raise ValidationError('Данные другой компании.')
        if self.pattern_id and self.group_id and self.pattern.board_id != self.group.board_id: raise ValidationError('График другого объекта.')
        if not self.person_id and self.status: raise ValidationError('У вакансии не может быть фактического статуса.')
        if self.planning_position_id or self.construction_object_id:
            if not all((self.planning_position_id,self.construction_object_id,self.person_id,self.housing_camp_id,self.assigned_from)):
                raise ValidationError('Для назначения укажите сотрудника, должность плана, объект, ВЖГ и дату начала.')
        if self.assigned_until and (not self.assigned_from or self.assigned_until < self.assigned_from):
            raise ValidationError({'assigned_until':'Дата окончания не может быть раньше начала назначения.'})
        if self.initial_movement_id and self.initial_movement.person_id!=self.person_id:
            raise ValidationError({'initial_movement':'Начальная запись принадлежит другому сотруднику.'})
        if self.replaced_on and (not self.assigned_from or self.replaced_on<self.assigned_from):
            raise ValidationError({'replaced_on':'Смена назначения не может быть раньше его начала.'})
        if self.previous_assignment_id and (self.previous_assignment_id==self.pk or self.previous_assignment.person_id!=self.person_id):
            raise ValidationError({'previous_assignment':'Предыдущее назначение должно принадлежать этому сотруднику.'})


class MovementRecommendationOverride(PersonnelModel):
    KINDS=[('ARRIVAL','Заезд'),('DEPARTURE','Выезд')]
    ACTIONS=[('ADD','Добавить'),('EXCLUDE','Исключить')]
    person=models.ForeignKey(Person,on_delete=models.CASCADE,related_name='movement_recommendation_overrides')
    camp=models.ForeignKey('Camp',on_delete=models.PROTECT,related_name='movement_recommendation_overrides')
    kind=models.CharField('Направление',max_length=12,choices=KINDS)
    planned_on=models.DateField('Рекомендуемая дата')
    action=models.CharField('Изменение',max_length=8,choices=ACTIONS)
    recorded_by=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.SET_NULL,null=True,blank=True,related_name='+')
    class Meta:
        ordering=['planned_on','person__full_name','pk']
        constraints=[models.UniqueConstraint(fields=['company','person','camp','kind','planned_on'],name='personnel_unique_movement_recommendation_override')]
    def clean(self):
        for value in (self.person,self.camp,self.recorded_by):
            if value and value.company_id!=self.company_id:raise ValidationError('Данные другой компании.')


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


class BaseCity(PersonnelModel):
    name=models.CharField('Базовый город',max_length=150)
    class Meta:
        ordering=['name','pk']
        constraints=[models.UniqueConstraint(fields=['company','name'],name='personnel_unique_base_city')]
    def clean(self):
        super().clean();self.name=' '.join(self.name.split())
        if not self.name:raise ValidationError({'name':'Укажите название города.'})
    def __str__(self):return self.name


class MovementReviewRequest(PersonnelModel):
    STATUSES=[
        ('OPEN','Ожидает проверки'),('PENDING_APPROVAL','На согласовании'),
        ('RETURNED','Возвращено на доработку'),('PEREVAKHTA','Передано в перевахту'),
        ('AT_HUB','В ОП Дудинка'),('EN_ROUTE','В пути из ОП Дудинка'),('COMPLETED','Завершено'),('ACCEPTED','Принято'),
        ('CANCELLED','Отменено'),
    ]
    SOURCES=[('DISPATCHER','Портал'),('EXCEL','Excel')]
    EMPLOYMENT_KINDS=[('STAFF','Штат'),('NEW','Новый'),('TRANSFER','Перевод'),('BUSINESS_TRIP','Командировка')]
    event=models.OneToOneField(HousingEvent,on_delete=models.PROTECT,related_name='review_request',null=True,blank=True)
    person=models.ForeignKey(Person,on_delete=models.PROTECT,related_name='movement_review_requests')
    kind=models.CharField('Действие',max_length=12,choices=HousingEvent.KINDS)
    movement_date=models.DateField('Дата заезда / выезда')
    from_camp=models.ForeignKey(Camp,on_delete=models.PROTECT,null=True,blank=True,related_name='+')
    to_camp=models.ForeignKey(Camp,on_delete=models.PROTECT,null=True,blank=True,related_name='+')
    departure_destination=models.CharField(max_length=10,blank=True)
    requested_by=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.SET_NULL,null=True,blank=True,related_name='+')
    source=models.CharField('Источник заявки',max_length=12,choices=SOURCES,default='DISPATCHER')
    status=models.CharField('Статус',max_length=20,choices=STATUSES,default='OPEN')
    employment_kind=models.CharField('Штат / новый / перевод',max_length=16,choices=EMPLOYMENT_KINDS,blank=True)
    planning_position_name=models.CharField('Должность по ГПР / СМГ',max_length=250,blank=True)
    position_name=models.CharField('Должность по справочнику сотрудников',max_length=250,blank=True)
    construction_object=models.ForeignKey('projects.ConstructionObject',on_delete=models.PROTECT,null=True,blank=True,related_name='movement_review_object_requests',verbose_name='Объект')
    site_object=models.ForeignKey('projects.ConstructionObject',on_delete=models.PROTECT,null=True,blank=True,related_name='movement_review_site_requests',verbose_name='Участок')
    site=models.CharField('Участок',max_length=250,blank=True)
    base_city=models.CharField('Базовый город',max_length=150,blank=True)
    phone=models.CharField('Номер телефона',max_length=80,blank=True)
    request_number=models.CharField('№ заявки',max_length=50,blank=True)
    travel_time=models.TimeField('Время прилёта / вылета',null=True,blank=True)
    travel_status=models.CharField('Статус поездки',max_length=80,blank=True)
    note=models.TextField('Комментарий / примечание',blank=True)
    reviewed_by=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.SET_NULL,null=True,blank=True,related_name='+')
    reviewed_at=models.DateTimeField(null=True,blank=True)
    approved_by=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.SET_NULL,null=True,blank=True,related_name='+')
    approved_at=models.DateTimeField(null=True,blank=True)
    approval_comment=models.TextField('Комментарий согласования',blank=True)
    hub_arrived_on=models.DateField('Прибытие в ОП Дудинка',null=True,blank=True)
    hub_departed_on=models.DateField('Выезд из ОП Дудинка',null=True,blank=True)
    final_arrived_on=models.DateField('Прибытие в ВЖГ',null=True,blank=True)
    tickets_ready=models.BooleanField('Билеты приобретены',default=False)
    tickets_ready_at=models.DateTimeField(null=True,blank=True)
    clearance_document=models.FileField('Обходной лист',upload_to='personnel/clearance/%Y/%m/',blank=True)
    clearance_uploaded_at=models.DateTimeField(null=True,blank=True)
    cancellation_reason=models.TextField('Причина отмены',blank=True)
    cancelled_by=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.SET_NULL,null=True,blank=True,related_name='+')
    cancelled_at=models.DateTimeField(null=True,blank=True)
    class Meta:
        ordering=['status','-movement_date','-pk']
    def clean(self):
        super().clean()
        for value in (self.event,self.person,self.from_camp,self.to_camp,self.requested_by,self.cancelled_by,self.construction_object,self.site_object):
            if value and value.company_id!=self.company_id:raise ValidationError('Заявка другой компании.')
        if self.site_object_id and self.construction_object_id:
            if self.site_object_id!=self.construction_object_id and self.site_object.parent_id!=self.construction_object_id:raise ValidationError({'site_object':'Участок должен относиться к выбранному объекту.'})
        if self.reviewed_by_id and self.reviewed_by.company_id!=self.company_id:raise ValidationError('Проверяющий другой компании.')
        if self.approved_by_id and self.approved_by.company_id!=self.company_id:raise ValidationError('Согласующий другой компании.')


class MovementReviewEvent(PersonnelModel):
    request=models.ForeignKey(MovementReviewRequest,on_delete=models.CASCADE,related_name='workflow_events')
    actor=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.SET_NULL,null=True,blank=True,related_name='+')
    action=models.CharField(max_length=120)
    comment=models.TextField(blank=True)
    class Meta:ordering=['created_at','pk']
    def clean(self):
        super().clean()
        if self.request.company_id!=self.company_id or self.actor_id and self.actor.company_id!=self.company_id:raise ValidationError('Событие другой компании.')


class ScheduleChangeRequest(PersonnelModel):
    STATUSES=[('PENDING','Ожидает изменения в ЗУП'),('APPLIED','Применено из ЗУП'),('CANCELLED','Отменено')]
    person=models.ForeignKey(Person,on_delete=models.PROTECT,related_name='schedule_change_requests')
    requested_schedule=models.ForeignKey(ZupWorkSchedule,on_delete=models.PROTECT,related_name='change_requests')
    previous_schedule=models.CharField('График до изменения',max_length=250,blank=True)
    status=models.CharField(max_length=12,choices=STATUSES,default='PENDING')
    requested_by=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.SET_NULL,null=True,blank=True,related_name='+')
    applied_at=models.DateTimeField(null=True,blank=True)
    task_key=models.CharField(max_length=180,blank=True)
    class Meta:
        ordering=['-created_at','-pk']
        constraints=[models.UniqueConstraint(fields=['person'],condition=models.Q(status='PENDING'),name='personnel_one_pending_schedule_change')]
    def clean(self):
        super().clean()
        for value in (self.person,self.requested_schedule,self.requested_by):
            if value and value.company_id!=self.company_id:raise ValidationError('Запрос графика другой компании.')

from .lodging_models import LodgingBuilding,LodgingBed,LodgingStay,LodgingEvent,LodgingControl,LodgingResolution

from .lodging_import_models import LodgingImportBatch,LodgingImportRow

from .recruitment_models import RecruitmentRequest, RecruitmentCandidate, ArrivalPreparation, CandidateDocument, RecruitmentEvent,TalentPoolCandidate
