from core.fact_history import ActualHistoryMixin
from django.conf import settings
from django.db import models
from django.core.exceptions import ValidationError
from core.models import BaseCompanyModel
from .russian_cities import normalize_city_name


DISPATCHER_BULK_CLEANUP_REASON = 'Административная массовая очистка диспетчерской'
CAMP_STAY_OVERLAP_ERROR = 'У сотрудника уже есть размещение на этот период. Оформите перевод или исправьте историю.'


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
        constraints = [models.UniqueConstraint(fields=['company', 'employee_number'], condition=models.Q(employee_number__gt=''), name='personnel_unique_employee_number')]
    def clean(self):
        super().clean()
        if self.employee_id and self.employee.company_id != self.company_id: raise ValidationError({'employee':'Сотрудник другой компании.'})
        if self.schedule_slot_id and (self.schedule_slot.company_id != self.company_id or self.schedule_slot.person_id != self.pk): raise ValidationError({'schedule_slot':'Выберите строку графика этого сотрудника.'})
        if self.dispatcher_removed_by_id and self.dispatcher_removed_by.company_id != self.company_id:raise ValidationError({'dispatcher_removed_by':'Пользователь другой компании.'})
    def save(self,*args,**kwargs):
        result=super().save(*args,**kwargs)
        if self.employee_id and self.phone:
            from apps.resources.models import Employee
            Employee.objects.filter(pk=self.employee_id,company_id=self.company_id,phone='').update(phone=self.phone)
        return result
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
    construction_object = models.ForeignKey('projects.ConstructionObject', on_delete=models.PROTECT, null=True, blank=True, related_name='personnel_assignments', verbose_name='Объект привязки')
    assigned_from = models.DateField('Привязан с', null=True, blank=True)
    assigned_until = models.DateField('Привязан по (включительно)', null=True, blank=True)
    initial_movement = models.ForeignKey('HousingEvent', on_delete=models.PROTECT, null=True, blank=True, related_name='initial_assignments', verbose_name='Начальная запись перемещения')
    previous_assignment = models.OneToOneField('self', on_delete=models.PROTECT, null=True, blank=True, related_name='next_assignment', verbose_name='Предыдущая привязка')
    replaced_on = models.DateField('Заменено с', null=True, blank=True)
    assigned_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='+', verbose_name='Привязал')
    cancelled_at = models.DateTimeField('Привязка удалена', null=True, blank=True)
    cancelled_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='+', verbose_name='Удалил привязку')
    cancellation_reason = models.TextField('Комментарий к удалению привязки', blank=True)
    camp = models.CharField('Местонахождение из исходного файла', max_length=100, blank=True)
    housing_camp = models.ForeignKey('Camp', on_delete=models.PROTECT, null=True, blank=True, verbose_name='Местонахождение')
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
                raise ValidationError('Для привязки укажите сотрудника, должность плана, объект, местонахождение и дату начала.')
        if self.assigned_until and (not self.assigned_from or self.assigned_until < self.assigned_from):
            raise ValidationError({'assigned_until':'Дата окончания не может быть раньше начала привязки.'})
        if self.initial_movement_id and self.initial_movement.person_id!=self.person_id:
            raise ValidationError({'initial_movement':'Начальная запись принадлежит другому сотруднику.'})
        if self.replaced_on and (not self.assigned_from or self.replaced_on<self.assigned_from):
            raise ValidationError({'replaced_on':'Смена привязки не может быть раньше её начала.'})
        if self.previous_assignment_id and (self.previous_assignment_id==self.pk or self.previous_assignment.person_id!=self.person_id):
            raise ValidationError({'previous_assignment':'Предыдущая привязка должна принадлежать этому сотруднику.'})


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
    name = models.CharField('Название местонахождения', max_length=200)
    address = models.CharField('Расположение / адрес', max_length=300, blank=True)
    is_active = models.BooleanField('Действующее местонахождение', default=True)
    objects_served = models.ManyToManyField('projects.ConstructionObject', through='CampObjectLink', related_name='housing_camps', verbose_name='Связанные объекты проекта')
    class Meta:
        ordering = ['name','pk']
        constraints = [models.UniqueConstraint(fields=['company','name'], name='unique_company_camp_name')]
    def clean(self):
        super().clean()
        self.name = ' '.join(self.name.split())
        if not self.name:raise ValidationError({'name':'Укажите название местонахождения.'})
        if any(' '.join(name.split()).casefold()==self.name.casefold() for name in type(self).objects.filter(company_id=self.company_id).exclude(pk=self.pk).values_list('name',flat=True)):
            raise ValidationError({'name':'Такое местонахождение уже существует.'})
    def __str__(self): return self.name


class CampObjectLink(PersonnelModel):
    camp = models.ForeignKey(Camp,on_delete=models.CASCADE,related_name='object_links')
    construction_object = models.ForeignKey('projects.ConstructionObject',on_delete=models.CASCADE,related_name='camp_links')
    class Meta:
        constraints = [models.UniqueConstraint(fields=['camp','construction_object'],name='unique_camp_object_link')]
    def clean(self):
        super().clean()
        if self.camp.company_id != self.company_id or self.construction_object.company_id != self.company_id:
            raise ValidationError('Связь местонахождения и объекта другой компании.')


class MovementExcelSiteMapping(PersonnelModel):
    source_site=models.CharField('Участок из Excel',max_length=250)
    source_site_normalized=models.CharField(max_length=250,editable=False)
    construction_object=models.ForeignKey('projects.ConstructionObject',on_delete=models.PROTECT,
        related_name='movement_excel_site_mappings',verbose_name='Строительный объект')
    camp=models.ForeignKey(Camp,on_delete=models.PROTECT,related_name='movement_excel_site_mappings',verbose_name='Местонахождение')
    class Meta:
        ordering=['source_site','pk']
        constraints=[models.UniqueConstraint(fields=['company','source_site_normalized'],name='movement_excel_site_unique')]
    @staticmethod
    def normalize(value):return ' '.join(str(value or '').replace('ё','е').replace('Ё','Е').split()).casefold()
    def clean(self):
        super().clean();self.source_site=' '.join(self.source_site.split());self.source_site_normalized=self.normalize(self.source_site)
        if not self.source_site:raise ValidationError({'source_site':'Укажите название участка из Excel.'})
        for field in ('construction_object','camp'):
            value=getattr(self,field,None)
            if value and value.company_id!=self.company_id:raise ValidationError({field:'Выберите значение своей компании.'})
        if self.construction_object_id and self.camp_id:
            object_ids={self.construction_object_id};front=set(object_ids)
            pairs=list(self.construction_object.__class__.objects.filter(company_id=self.company_id,
                project_id=self.construction_object.project_id).values_list('pk','parent_id'))
            while front:
                children={pk for pk,parent_id in pairs if parent_id in front};object_ids|=children;front=children
            if not CampObjectLink.objects.filter(company_id=self.company_id,camp_id=self.camp_id,
                    construction_object_id__in=object_ids).exists():
                raise ValidationError({'camp':'Местонахождение должно быть связано с выбранным строительным объектом.'})
    def __str__(self):return f'{self.source_site} → {self.construction_object} → {self.camp}'


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
            if overlapping.exists():raise ValidationError(CAMP_STAY_OVERLAP_ERROR)
    def __str__(self):return f'{self.person} · {self.camp} · {self.arrived_on}'


class HousingEvent(ActualHistoryMixin, PersonnelModel):
    KINDS=[('ARRIVAL','Заезд'),('DEPARTURE','Выезд'),('TRANSFER','Перевод'),('CANCEL','Отмена записи')]
    departure_destination=models.CharField('Куда выезжает',max_length=10,choices=[('REST','На межвахтовый отдых'),('CAMP','В другое местонахождение'),('DISMISSAL','По увольнению')],blank=True)
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
        indexes=[
            models.Index(fields=['company','person','date','kind'],name='housing_event_person_day'),
            models.Index(fields=['company','kind','date'],name='housing_event_kind_day'),
        ]
    def clean(self):
        super().clean()
        for name in ('person','from_camp','to_camp','stay'):
            obj=getattr(self,name,None)
            if obj and obj.company_id!=self.company_id:raise ValidationError('Событие другой компании.')


class BaseCity(PersonnelModel):
    name=models.CharField('Базовый город',max_length=150)
    is_active=models.BooleanField('Действующий',default=True)
    archived_at=models.DateTimeField('Дата архивации',null=True,blank=True)
    archived_by=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.SET_NULL,null=True,blank=True,
        related_name='+',verbose_name='Кто архивировал')
    merged_into=models.ForeignKey('self',on_delete=models.SET_NULL,null=True,blank=True,
        related_name='merged_sources',verbose_name='Объединён с городом')
    status_reason=models.CharField('Причина изменения статуса',max_length=500,blank=True)
    class Meta:
        ordering=['name','pk']
        constraints=[models.UniqueConstraint(fields=['company','name'],name='personnel_unique_base_city')]
    def clean(self):
        super().clean();self.name=normalize_city_name(self.name)
        if not self.name:raise ValidationError({'name':'Укажите название города.'})
        duplicate=any(' '.join(value.split()).casefold()==self.name.casefold() for value in
            type(self).objects.filter(company_id=self.company_id).exclude(pk=self.pk).values_list('name',flat=True)) if self.company_id else False
        if duplicate and not self.merged_into_id:
            raise ValidationError({'name':'Город с таким названием уже существует.'})
        if self.merged_into_id:
            if self.merged_into_id==self.pk:raise ValidationError({'merged_into':'Нельзя объединить город с самим собой.'})
            if self.merged_into.company_id!=self.company_id:raise ValidationError({'merged_into':'Выберите город своей компании.'})
    def __str__(self):return self.name


class MovementReviewRequest(PersonnelModel):
    STATUSES=[
        ('OPEN','Ожидает проверки'),('PENDING_APPROVAL','На согласовании'),
        ('RETURNED','Возвращено на доработку'),('PEREVAKHTA','Передано в перевахту'),
        ('AT_HUB','В ОП Дудинка'),('EN_ROUTE','В пути из ОП Дудинка'),('COMPLETED','Завершено'),('ACCEPTED','Принято'),
        ('CANCELLED','Отменено'),
    ]
    SOURCES=[('DISPATCHER','Портал'),('EXCEL','Excel')]
    EMPLOYMENT_KINDS=[('STAFF','Штат'),('NEW','Новый'),('REHIRE','Повторный приём'),
        ('TRANSFER','Перевод'),('BUSINESS_TRIP','Командировка')]
    # User-facing lifecycle stages are more precise than the persisted status:
    # one PENDING_APPROVAL or AT_HUB value contains several real workflow steps.
    LIFECYCLE_STAGES=[
        ('DRAFT','Черновик'),('REVIEW','Проверка перевахтой'),
        ('APPROVAL_FIRST','Руководитель / начальник участка'),
        ('APPROVAL_PEREVAKHTA','Согласование перевахтой'),
        ('APPROVAL_PARALLEL','Параллельное согласование'),
        ('RETURNED','На доработке'),('TICKETS','Покупка билетов'),
        ('HUB_ARRIVAL','Прибытие в ОП Дудинка'),('HUB_ZUP','Оформление в ЗУП'),
        ('HUB_CLEARANCE','Обходной лист'),('HUB_READY','Документы готовы'),
        ('EN_ROUTE','В пути'),('COMPLETED','Маршрут завершён'),
        ('ACCEPTED','Принято'),('CANCELLED','Отменено'),
    ]
    event=models.OneToOneField(HousingEvent,on_delete=models.PROTECT,related_name='review_request',null=True,blank=True)
    person=models.ForeignKey(Person,on_delete=models.PROTECT,related_name='movement_review_requests')
    pool_candidate=models.ForeignKey('personnel.TalentPoolCandidate',on_delete=models.PROTECT,null=True,blank=True,
        related_name='movement_requests',verbose_name='Карточка кандидата')
    kind=models.CharField('Действие',max_length=12,choices=HousingEvent.KINDS)
    movement_date=models.DateField('Дата заезда / выезда')
    requested_movement_date=models.DateField('Плановая дата, указанная в заявке',null=True,blank=True,editable=False)
    from_camp=models.ForeignKey(Camp,on_delete=models.PROTECT,null=True,blank=True,related_name='+')
    to_camp=models.ForeignKey(Camp,on_delete=models.PROTECT,null=True,blank=True,related_name='+')
    departure_destination=models.CharField(max_length=10,blank=True)
    requested_by=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.SET_NULL,null=True,blank=True,related_name='+')
    approval_batch=models.UUIDField(null=True,blank=True,db_index=True)
    source=models.CharField('Источник заявки',max_length=12,choices=SOURCES,default='DISPATCHER')
    status=models.CharField('Статус',max_length=20,choices=STATUSES,default='OPEN')
    employment_kind=models.CharField('Статус сотрудника',max_length=16,choices=EMPLOYMENT_KINDS,blank=True)
    planning_position_name=models.CharField('Должность по ГПР / СМГ',max_length=250,blank=True)
    position_name=models.CharField('Должность по справочнику сотрудников',max_length=250,blank=True)
    construction_object=models.ForeignKey('projects.ConstructionObject',on_delete=models.PROTECT,null=True,blank=True,related_name='movement_review_object_requests',verbose_name='Объект')
    site_object=models.ForeignKey('projects.ConstructionObject',on_delete=models.PROTECT,null=True,blank=True,related_name='movement_review_site_requests',verbose_name='Участок')
    site=models.CharField('Участок',max_length=250,blank=True)
    base_city=models.CharField('Базовый город',max_length=150,blank=True)
    phone=models.CharField('Номер телефона',max_length=80,blank=True)
    request_number=models.CharField('№ заявки',max_length=50,blank=True)
    travel_time=models.TimeField('Время прилёта / вылета',null=True,blank=True)
    planned_hub_departure_date=models.DateField('Плановая дата выезда из ОП Дудинка',null=True,blank=True)
    planned_hub_departure_time=models.TimeField('Плановое время выезда из ОП Дудинка',null=True,blank=True)
    travel_status=models.CharField('Статус поездки',max_length=80,blank=True)
    note=models.TextField('Комментарий / примечание',blank=True)
    reviewed_by=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.SET_NULL,null=True,blank=True,related_name='+')
    reviewed_at=models.DateTimeField(null=True,blank=True)
    approved_by=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.SET_NULL,null=True,blank=True,related_name='+')
    approved_at=models.DateTimeField(null=True,blank=True)
    approval_comment=models.TextField('Комментарий согласования',blank=True)
    approval_route=models.JSONField('Маршрут согласования',default=dict,blank=True)
    hub_arrived_on=models.DateField('Прибытие в ОП Дудинка',null=True,blank=True)
    hub_departed_on=models.DateField('Выезд из ОП Дудинка',null=True,blank=True)
    final_arrived_on=models.DateField('Прибытие в местонахождение',null=True,blank=True)
    tickets_ready=models.BooleanField('Билеты приобретены',default=False)
    tickets_ready_at=models.DateTimeField(null=True,blank=True)
    ticket_cost=models.DecimalField('Стоимость билета',max_digits=12,decimal_places=2,null=True,blank=True)
    ticket_document=models.FileField('Файл билета',upload_to='personnel/tickets/%Y/%m/',blank=True)
    ticket_uploaded_at=models.DateTimeField(null=True,blank=True)
    clearance_ready=models.BooleanField('Обходной лист имеется',default=False)
    clearance_document=models.FileField('Обходной лист',upload_to='personnel/clearance/%Y/%m/',blank=True)
    clearance_uploaded_at=models.DateTimeField(null=True,blank=True)
    cancellation_reason=models.TextField('Причина отмены',blank=True)
    cancelled_by=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.SET_NULL,null=True,blank=True,related_name='+')
    cancelled_at=models.DateTimeField(null=True,blank=True)
    recommendation_planned_on=models.DateField('Исходная дата рекомендации',null=True,blank=True)
    class Meta:
        ordering=['status','-movement_date','-pk']
        indexes=[
            models.Index(fields=['company','requested_by','status'],name='movement_author_status'),
            models.Index(fields=['company','status','kind'],name='movement_status_kind'),
            models.Index(fields=['company','person','status'],name='movement_person_status'),
            models.Index(fields=['company','movement_date'],name='movement_company_day'),
            models.Index(fields=['company','approval_batch','status'],name='movement_batch_status'),
        ]
    def save(self,*args,**kwargs):
        if self.requested_movement_date is None and self.movement_date:
            self.requested_movement_date=self.movement_date
            if kwargs.get('update_fields') is not None:
                kwargs['update_fields']=set(kwargs['update_fields'])|{'requested_movement_date'}
        if self.clearance_document and not self.clearance_ready:
            self.clearance_ready=True
            if kwargs.get('update_fields') is not None:
                kwargs['update_fields']=set(kwargs['update_fields'])|{'clearance_ready'}
        super().save(*args,**kwargs)
        if self.source=='DISPATCHER' and not self.request_number:
            self.request_number=str(self.pk)
            type(self).objects.filter(pk=self.pk,request_number='').update(request_number=self.request_number)
    def requested_movement_date_label(self):
        if self.source=='EXCEL':return 'Исходная плановая дата из Excel'
        return {
            'ARRIVAL':'Плановая дата заезда, указанная диспетчером',
            'DEPARTURE':'Плановая дата выезда, указанная диспетчером',
            'TRANSFER':'Плановая дата перевода, указанная диспетчером',
        }.get(self.kind,'Плановая дата, указанная диспетчером')
    def workflow_status_label(self):
        if self.source=='EXCEL' and self.status=='COMPLETED':return f'Завершена по Excel: {self.travel_status or "Прибыл / убыл"}'
        if self.status=='OPEN' and self.source=='DISPATCHER' and not self.approved_at and not self.has_approved_stage():return 'Черновик диспетчера'
        if self.status=='OPEN':return 'Ожидает проверки специалистом перевахты'
        if self.status=='PENDING_APPROVAL':
            from .departure_workflow import FIRST_GATE,PARALLEL,ROLE_LABELS,route_for
            route=route_for(self);pending=[code for code,data in route.items() if data.get('status')=='PENDING']
            if any(code in PARALLEL for code in pending):
                approved=sum(route[code].get('status')=='APPROVED' for code in PARALLEL)
                return f'Параллельное согласование: {approved} из {len(PARALLEL)}'
            if all(code in pending for code in FIRST_GATE):return 'Согласование: руководитель проекта или начальник участка'
            if pending:return 'Согласование: '+ROLE_LABELS[pending[0]]
            return 'На согласовании'
        if self.status=='RETURNED':return 'Возвращена на доработку'
        if self.status=='PEREVAKHTA':
            if self.kind=='ARRIVAL' and not self.tickets_ready:return 'Ожидается покупка билетов'
            return 'Ожидается прибытие в ОП Дудинка'
        if self.status=='AT_HUB':
            if self.kind=='ARRIVAL' and self.employment_kind=='NEW' and not self.person.employee_id:return 'В ОП Дудинка — оформление нового сотрудника в ЗУП'
            if not self.clearance_ready:return 'В ОП Дудинка — ожидается обходной лист' if self.kind=='ARRIVAL' else 'В ОП Дудинка — билеты и обходной лист'
            return 'В ОП Дудинка — документы готовы'
        if self.status=='EN_ROUTE':return 'В пути к местонахождению' if self.kind=='ARRIVAL' else 'В пути в базовый город'
        if self.status=='COMPLETED':return 'Прибыл в местонахождение' if self.kind=='ARRIVAL' else 'Выбыл из ОП Дудинка'
        if self.status=='CANCELLED':return 'Отменена'
        if self.status=='ACCEPTED':return 'Принята'
        return 'Ожидает обработки перевахтой'
    def lifecycle_stage_code(self):
        if self.status=='OPEN':
            return 'DRAFT' if self.source=='DISPATCHER' and not self.approved_at and not self.has_approved_stage() else 'REVIEW'
        if self.status=='PENDING_APPROVAL':
            from .departure_workflow import FIRST_GATE,PARALLEL,active_route_roles
            active=active_route_roles(self)
            if active.intersection(FIRST_GATE):return 'APPROVAL_FIRST'
            if 'PEREVAKHTA_SPECIALIST' in active:return 'APPROVAL_PEREVAKHTA'
            if active.intersection(PARALLEL):return 'APPROVAL_PARALLEL'
            return 'APPROVAL_PARALLEL'
        if self.status=='RETURNED':return 'RETURNED'
        if self.status=='PEREVAKHTA':
            return 'TICKETS' if self.kind=='ARRIVAL' and not self.tickets_ready else 'HUB_ARRIVAL'
        if self.status=='AT_HUB':
            if self.kind=='ARRIVAL' and self.employment_kind=='NEW' and not self.person.employee_id:return 'HUB_ZUP'
            return 'HUB_CLEARANCE' if not self.clearance_ready else 'HUB_READY'
        if self.status=='EN_ROUTE':return 'EN_ROUTE'
        if self.status=='COMPLETED':return 'COMPLETED'
        if self.status=='ACCEPTED':return 'ACCEPTED'
        if self.status=='CANCELLED':return 'CANCELLED'
        return 'REVIEW'
    def route_label(self):
        if self.kind=='ARRIVAL':
            origin=self.base_city or 'Базовый город не указан'
            return f'{origin} → ОП Дудинка → {self.to_camp or "Местонахождение не указано"}'
        destination=self.base_city or ('Увольнение' if self.departure_destination=='DISMISSAL' else 'МО')
        return f'{self.from_camp or "Местонахождение не указано"} → ОП Дудинка → {destination}'
    def next_action_label(self):
        if self.status=='OPEN':return 'Диспетчер: заполнить и отправить черновик'
        if self.status=='PENDING_APPROVAL':return self.workflow_status_label()
        if self.status=='RETURNED':return 'Диспетчер: исправить заявку и повторно отправить'
        if self.status=='PEREVAKHTA':
            return 'Специалист перевахты: приобрести билеты и уточнить дату' if self.kind=='ARRIVAL' and not self.tickets_ready else 'Специалист перевахты: зафиксировать прибытие в ОП Дудинка'
        if self.status=='AT_HUB':
            if self.kind=='ARRIVAL' and self.employment_kind=='NEW' and not self.person.employee_id:return 'Специалист по персоналу: оформить сотрудника в ЗУП'
            if not self.clearance_ready:return 'Специалист перевахты: подтвердить наличие обходного листа'
            return 'Специалист перевахты: зафиксировать выезд из ОП Дудинка'
        if self.status=='EN_ROUTE' and self.kind=='ARRIVAL':return 'Диспетчер: подтвердить прибытие в местонахождение'
        if self.status in {'COMPLETED','ACCEPTED'}:return 'Маршрут завершён'
        if self.status=='CANCELLED':return 'Заявка отменена'
        return self.workflow_status_label()
    def workflow_stage_code(self):
        if self.status=='PENDING_APPROVAL':return 'APPROVAL'
        if self.status=='RETURNED':return 'RETURNED'
        if self.status=='PEREVAKHTA':return 'TICKETS' if self.kind=='ARRIVAL' and not self.tickets_ready else 'DUDINKA_ARRIVAL'
        if self.status=='AT_HUB':return 'DUDINKA'
        if self.status=='EN_ROUTE':return 'EN_ROUTE'
        if self.status in {'COMPLETED','ACCEPTED'}:return 'COMPLETED'
        if self.status=='CANCELLED':return 'CANCELLED'
        return 'PROCESSING'
    def departure_reason_label(self):
        if self.kind!='DEPARTURE':return '—'
        return {'REST':'Межвахтовый отдых','DISMISSAL':'Увольнение'}.get(self.departure_destination,'Не указана')
    def has_approved_stage(self):
        return any(data.get('status')=='APPROVED' for data in (self.approval_route or {}).values())
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
    class Meta:
        ordering=['created_at','pk']
        indexes=[models.Index(fields=['request','created_at'],name='movement_event_timeline')]
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

from .lodging_models import LodgingOrganization,LodgingGuest,LodgingBuilding,LodgingBed,LodgingStay,LodgingEvent,LodgingControl,LodgingPresence,LodgingResolution,LodgingSyncState

from .lodging_import_models import LodgingImportBatch,LodgingImportRow

from .recruitment_models import (RecruitmentRequest,RecruitmentCandidate,ArrivalPreparation,CandidateDocument,
    RecruitmentEvent,RecruitmentSpecialist,TalentPoolCandidate,TalentPoolStageEvent)
