"""Recruitment requests and candidates; neither is a production employee."""
from pathlib import Path
from uuid import uuid4
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.files.storage import FileSystemStorage
from django.db import models
from .models import PersonnelModel


class RecruitmentStorage(FileSystemStorage):
    @property
    def base_location(self):
        # Reuse the existing private backup / staging isolation boundary.
        return Path(settings.ACTUATION_FILES_ROOT) / 'recruitment'
    @property
    def location(self):return str(Path(self.base_location).resolve())
    @property
    def base_url(self):return None


def resume_storage():return RecruitmentStorage()
def resume_path(instance, filename):return f'{instance.company_id}/{uuid4().hex}'


class TalentPoolCandidate(PersonnelModel):
    full_name=models.CharField('ФИО',max_length=250)
    phone=models.CharField('Телефон',max_length=80,blank=True)
    notes=models.TextField('Комментарий',blank=True)
    is_active=models.BooleanField('Доступен для вакансий',default=True)
    created_by=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.PROTECT,related_name='talent_pool_candidates')
    class Meta:ordering=['full_name','pk']
    def __str__(self):return self.full_name


class RecruitmentRequest(PersonnelModel):
    construction_object = models.ForeignKey('projects.ConstructionObject', on_delete=models.PROTECT, verbose_name='Объект')
    position = models.CharField('Должность', max_length=250)
    quantity = models.PositiveIntegerField('Требуется человек')
    required_on = models.DateField('Нужны на объекте к')
    requirements = models.TextField('Требования и комментарий', blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='recruitment_requests')
    assigned_to = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='recruitment_assignments', null=True, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    revision = models.PositiveIntegerField(default=0)
    class Meta:
        ordering = ['required_on', 'pk']
        constraints = [models.CheckConstraint(condition=models.Q(quantity__gt=0), name='recruitment_positive_quantity')]
    def __str__(self):return f'Заявка №{self.pk} · {self.position}'
    def clean(self):
        super().clean()
        for field in ('construction_object', 'created_by', 'assigned_to'):
            if getattr(self, field+'_id') and getattr(self, field).company_id != self.company_id:
                raise ValidationError({field:'Выберите запись своей компании.'})
        if not self.quantity:raise ValidationError({'quantity':'Укажите хотя бы одного человека.'})


class RecruitmentCandidate(PersonnelModel):
    STATES = [('NEW','Новый'), ('SCREENING','На рассмотрении'), ('READY','Готов к заезду'),
              ('TRANSFERRED','Передан диспетчеру'), ('REJECTED','Не подошёл'), ('WITHDRAWN','Отказался')]
    request = models.ForeignKey(RecruitmentRequest, on_delete=models.PROTECT, related_name='candidates')
    pool_candidate=models.ForeignKey(TalentPoolCandidate,on_delete=models.PROTECT,null=True,blank=True,related_name='vacancy_candidates',verbose_name='Кандидат из пула')
    full_name = models.CharField('ФИО кандидата', max_length=250)
    phone = models.CharField('Телефон', max_length=80, blank=True)
    status = models.CharField('Статус', max_length=16, choices=STATES, default='NEW')
    ready_on = models.DateField('Готов к заезду с', null=True, blank=True)
    notes = models.TextField('Комментарий по кандидату', blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='recruitment_candidates')
    revision = models.PositiveIntegerField(default=0)
    class Meta:ordering = ['created_at','pk']
    def __str__(self):return self.full_name
    def clean(self):
        super().clean()
        if self.request_id and self.request.company_id != self.company_id:raise ValidationError('Заявка другой компании.')
        if self.pool_candidate_id and self.pool_candidate.company_id!=self.company_id:raise ValidationError('Кандидат общего пула другой компании.')
        if self.created_by_id and self.created_by.company_id != self.company_id:raise ValidationError('Автор другой компании.')
        if self.status in {'READY','TRANSFERRED'} and not self.ready_on:raise ValidationError({'ready_on':'Укажите дату готовности.'})


class ArrivalPreparation(PersonnelModel):
    STATES = [('EXPECTED','Ожидается'), ('ARRIVED','Заезд подтверждён'), ('CANCELLED','Подготовка отменена')]
    candidate = models.OneToOneField(RecruitmentCandidate, on_delete=models.PROTECT, related_name='arrival')
    expected_on = models.DateField('Плановая дата заезда')
    camp = models.ForeignKey('personnel.Camp', on_delete=models.PROTECT, null=True, blank=True, verbose_name='ВЖГ')
    employee = models.ForeignKey('resources.Employee', on_delete=models.PROTECT, null=True, blank=True, verbose_name='Оформленный сотрудник')
    status = models.CharField(max_length=12, choices=STATES, default='EXPECTED')
    actual_stay = models.OneToOneField('personnel.CampStay', on_delete=models.PROTECT, null=True, blank=True, related_name='recruitment_arrival')
    movement_request = models.OneToOneField('personnel.MovementReviewRequest',on_delete=models.PROTECT,null=True,blank=True,related_name='recruitment_arrival')
    handed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='handed_candidates')
    notes = models.TextField('Комментарий к заезду', blank=True)
    revision = models.PositiveIntegerField(default=0)
    class Meta:
        ordering = ['expected_on','pk']
        constraints = [models.UniqueConstraint(fields=['company','employee'], condition=models.Q(status='EXPECTED',employee__isnull=False), name='recruitment_one_expected_employee')]
    @property
    def actually_arrived(self):return bool(self.actual_stay_id and self.actual_stay.cancelled_at is None)
    def clean(self):
        super().clean()
        for field in ('candidate','camp','employee','actual_stay','handed_by'):
            if getattr(self,field+'_id') and getattr(self,field).company_id != self.company_id:raise ValidationError({field:'Запись другой компании.'})
        if self.candidate_id and self.candidate.ready_on and self.expected_on < self.candidate.ready_on:
            raise ValidationError({'expected_on':'Дата заезда раньше готовности кандидата.'})
        if self.actual_stay_id and (not self.employee_id or self.actual_stay.person.employee_id != self.employee_id or self.actual_stay.camp_id != self.camp_id):
            raise ValidationError('Подтверждённое размещение не соответствует сотруднику или ВЖГ.')


class CandidateDocument(PersonnelModel):
    candidate = models.ForeignKey(RecruitmentCandidate, on_delete=models.PROTECT, related_name='documents')
    file = models.FileField(storage=resume_storage, upload_to=resume_path)
    original_name = models.CharField(max_length=255)
    size = models.PositiveBigIntegerField()
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='candidate_documents')
    class Meta:ordering = ['created_at','pk']
    def clean(self):
        super().clean()
        if self.candidate_id and self.candidate.company_id != self.company_id:raise ValidationError('Кандидат другой компании.')
        if self.uploaded_by_id and self.uploaded_by.company_id != self.company_id:raise ValidationError('Автор другой компании.')


class RecruitmentEvent(PersonnelModel):
    request = models.ForeignKey(RecruitmentRequest, on_delete=models.PROTECT, related_name='events')
    candidate = models.ForeignKey(RecruitmentCandidate, on_delete=models.PROTECT, null=True, blank=True)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    description = models.TextField()
    class Meta:ordering = ['-created_at','-pk']
    def clean(self):
        super().clean()
        if self.request.company_id != self.company_id or self.actor.company_id != self.company_id:raise ValidationError('Событие другой компании.')
        if self.candidate_id and self.candidate.request_id != self.request_id:raise ValidationError('Кандидат другой заявки.')
