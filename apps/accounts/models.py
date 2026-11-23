from datetime import time

from django.contrib.auth.models import AbstractUser
from django.db import models
from django.utils.translation import gettext_lazy as _

from core.models import BaseTimestampedModel


class Company(BaseTimestampedModel):
    name = models.CharField(_('название'), max_length=255)
    inn = models.CharField(_('ИНН'), max_length=12, unique=True, blank=True, null=True)
    is_active = models.BooleanField(_('активна'), default=True)

    class Meta:
        verbose_name = _('компания')
        verbose_name_plural = _('компании')
        ordering = ['name']

    def __str__(self):
        return self.name


class Role(BaseTimestampedModel):
    code = models.CharField(_('код роли'), max_length=50, unique=True)
    name = models.CharField(_('название'), max_length=100)
    description = models.TextField(_('описание'), blank=True)

    class Meta:
        verbose_name = _('роль')
        verbose_name_plural = _('роли')
        ordering = ['name']

    def __str__(self):
        return self.name


class AccessGroup(BaseTimestampedModel):
    company = models.ForeignKey(Company, on_delete=models.CASCADE)
    name = models.CharField('Название группы', max_length=150)
    role = models.ForeignKey(Role, on_delete=models.PROTECT, verbose_name='Роль')
    assigned_objects = models.ManyToManyField('projects.ConstructionObject', blank=True, verbose_name='Объекты')
    production_additional_objects = models.ManyToManyField('projects.ConstructionObject', blank=True, related_name='production_view_groups', verbose_name='Производство: дополнительные объекты без ежедневных заданий')
    assigned_camps = models.ManyToManyField('personnel.Camp', blank=True, related_name='commandant_access_groups', verbose_name='Местонахождения коменданта')
    ad_group_dn = models.CharField('DN группы AD', max_length=1024, blank=True, help_text='Полный distinguishedName группы AD. Пусто — только ручное назначение.')

    class Meta:
        ordering = ['name']
        constraints = [models.UniqueConstraint(fields=['company', 'name'], name='unique_access_group_name')]

    def __str__(self):
        return self.name


class User(AbstractUser):
    access_group = models.ForeignKey(AccessGroup, null=True, blank=True, on_delete=models.PROTECT, related_name='members', verbose_name='Группа доступа')
    access_group_from_ad = models.BooleanField(default=False, editable=False)

    ldap_login = models.CharField('Логин Windows',max_length=150,blank=True,editable=False)

    auth_source = models.CharField('Источник входа', max_length=5, default='LOCAL', choices=[('LOCAL','Локальный'),('LDAP','Active Directory')], editable=False)
    ldap_guid = models.UUIDField('Идентификатор AD', unique=True, null=True, blank=True, editable=False)
    company = models.ForeignKey(
        Company, on_delete=models.CASCADE,
        related_name='users', verbose_name=_('компания'),
        null=True, blank=True
    )
    role = models.ForeignKey(
        Role, on_delete=models.SET_NULL,
        related_name='users', verbose_name=_('роль'),
        null=True, blank=True
    )
    assigned_objects = models.ManyToManyField(
        'projects.ConstructionObject', blank=True, related_name='assigned_users',
        verbose_name=_('назначенные строительные объекты'),
    )
    production_additional_objects = models.ManyToManyField(
        'projects.ConstructionObject', blank=True, related_name='production_viewers',
        verbose_name='Производство: дополнительные объекты без ежедневных заданий',
    )
    position = models.CharField(_('должность'), max_length=100, blank=True)
    phone = models.CharField(_('телефон'), max_length=20, blank=True)
    archived_at = models.DateTimeField('В архиве с', null=True, blank=True, editable=False)
    archive_was_active = models.BooleanField(default=False, editable=False)

    def save(self, *args, **kwargs):
        # Only the archive service changes archive state; stale edit forms must not undo it.
        if self.pk:
            archive = type(self).objects.filter(pk=self.pk).values('archived_at', 'archive_was_active').first()
            if archive:
                self.archived_at = archive['archived_at']
                self.archive_was_active = archive['archive_was_active']
        if self.archived_at:
            self.is_active = False
            if kwargs.get('update_fields') is not None:
                kwargs['update_fields'] = set(kwargs['update_fields']) | {'is_active'}
        return super().save(*args, **kwargs)

    class Meta:
        verbose_name = _('пользователь')
        verbose_name_plural = _('пользователи')
        ordering = ['last_name', 'first_name']

    def __str__(self):
        return f"{self.last_name} {self.first_name}" if self.last_name else self.username

    @property
    def full_name(self):
        return f"{self.last_name} {self.first_name}".strip() or self.username

    @property
    def login_display(self):
        return (self.ldap_login or 'Логин Windows уточняется') if self.auth_source=='LDAP' else self.username

    def has_role(self, role_code):
        return (role_code == 'ADMIN' and self.is_superuser) or (self.role and self.role.code == role_code)

    def is_admin(self):
        return self.has_role('ADMIN')

    def is_planner(self):
        return self.has_role('PLANNER')

    def is_manager(self):
        return self.has_role('MANAGER')

    def is_foreman(self):
        return self.has_role('FOREMAN')


class ApprovalAssignment(BaseTimestampedModel):
    """The single portal user responsible for one company approval stage."""
    PROCESS_CHOICES = [
        ('MOVEMENT', 'Заявки на заезд и выезд'),
        ('GPR', 'ГПР'),
        ('SMG', 'СМГ'),
    ]
    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name='approval_assignments')
    process = models.CharField('Процесс', max_length=20, choices=PROCESS_CHOICES)
    stage_code = models.CharField('Код этапа', max_length=50)
    user = models.ForeignKey(User, on_delete=models.PROTECT, related_name='approval_assignments', verbose_name='Согласующий')
    changed_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')

    class Meta:
        ordering = ['process', 'stage_code']
        constraints = [models.UniqueConstraint(fields=['company', 'process', 'stage_code'], name='unique_company_approval_assignment')]

    def __str__(self):
        return f'{self.get_process_display()} · {self.stage_code}: {self.user}'


class ApprovalAssignmentChange(models.Model):
    assignment = models.ForeignKey(ApprovalAssignment, on_delete=models.CASCADE, related_name='history')
    previous_user = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    new_user = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    changed_by = models.ForeignKey(User, null=True, on_delete=models.SET_NULL, related_name='+')
    reason = models.TextField('Причина замены', blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at', '-pk']


class ApprovalObserver(BaseTimestampedModel):
    """Company-wide informational recipient for every approval stage."""
    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name='approval_observers')
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='approval_observer_memberships', verbose_name='Наблюдатель')
    added_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')

    class Meta:
        ordering = ['user__last_name', 'user__first_name', 'user__username']
        constraints = [models.UniqueConstraint(fields=['company', 'user'], name='unique_company_approval_observer')]

    def __str__(self):
        return f'{self.company}: {self.user}'


class ApprovalNotice(models.Model):
    STATUS_CHOICES = [('PENDING', 'Ожидает отправки'), ('SENT', 'Отправлено'), ('FAILED', 'Ошибка')]
    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name='approval_notices')
    process = models.CharField(max_length=20, choices=ApprovalAssignment.PROCESS_CHOICES)
    stage_code = models.CharField(max_length=50)
    task_id = models.PositiveBigIntegerField(db_index=True)
    intended_user = models.ForeignKey(User, null=True, on_delete=models.SET_NULL, related_name='intended_approval_notices')
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='PENDING')
    intended_email = models.EmailField(blank=True)
    delivered_to = models.JSONField(default=list, blank=True)
    error = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    sent_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at', '-pk']
        constraints = [models.UniqueConstraint(fields=['company', 'process', 'stage_code', 'task_id', 'intended_user'], name='unique_approval_notice')]

from django.core.exceptions import ValidationError
from django.db.models.signals import m2m_changed, pre_save
from django.dispatch import receiver
from core.fixture_loading import is_loading_fixture


@receiver(m2m_changed, sender=User.production_additional_objects.through)
@receiver(m2m_changed, sender=User.assigned_objects.through)
def validate_object_assignments(sender, instance, action, reverse, pk_set, **kwargs):
    if is_loading_fixture():return
    if action != 'pre_add':
        return
    if reverse:
        invalid = User.objects.filter(pk__in=pk_set).exclude(company_id=instance.company_id).exists()
    else:
        from apps.projects.models import ConstructionObject
        invalid = ConstructionObject.objects.filter(pk__in=pk_set).exclude(company_id=instance.company_id).exists()
    if invalid:
        raise ValidationError('Нельзя назначить объект другой компании.')


@receiver(pre_save, sender=User)
def protect_assignment_company(sender, instance, raw=False, **kwargs):
    if raw:return
    if instance.pk and (instance.assigned_objects.exclude(company_id=instance.company_id).exists() or instance.production_additional_objects.exclude(company_id=instance.company_id).exists()):
        raise ValidationError('Перед сменой компании удалите назначения строительных объектов.')


@receiver(m2m_changed,sender=User.assigned_objects.through)
def reset_daily_task_subscriptions(sender,instance,action,reverse,**kwargs):
    if is_loading_fixture():return
    if action not in {'post_remove','post_clear'} or reverse:return
    from apps.planning.models import DailyTaskCursor,WorkTask
    from apps.planning.task_services import state
    ids=instance.assigned_objects.values_list('pk',flat=True)
    DailyTaskCursor.objects.filter(user=instance).exclude(construction_object_id__in=ids).delete()
    for task in WorkTask.objects.filter(assigned_to=instance,kind='FACT',status='OPEN').exclude(construction_object_id__in=ids):state(task,'SUPERSEDED',text='Объект больше не закреплен за исполнителем.')


class OneCConnection(models.Model):
    metadata_labels = models.JSONField(default=dict, blank=True)
    metadata_server = models.CharField(max_length=255, blank=True)
    metadata_database = models.CharField(max_length=128, blank=True)
    company = models.OneToOneField(Company, on_delete=models.CASCADE)
    server = models.CharField(max_length=255, default='msk-db')
    database = models.CharField(max_length=128, default='zup')
    trust_certificate = models.BooleanField(default=False)
    diagnostic_interval_minutes = models.PositiveIntegerField(default=0)
    positions_interval_minutes = models.PositiveIntegerField(default=0)
    employees_interval_minutes = models.PositiveIntegerField(default=0)
    positions_schedule = models.JSONField(default=dict, blank=True)
    employees_schedule = models.JSONField(default=dict, blank=True)
    uat_sync_enabled = models.BooleanField(default=True)
    uat_sync_time = models.TimeField(default=time(1, 0))
    uat_sync_lookback_days = models.PositiveSmallIntegerField(default=7)
    active_until = models.DateTimeField(null=True, blank=True)


class OneCDiagnosticRun(models.Model):
    import uuid
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    connection = models.ForeignKey(OneCConnection, on_delete=models.CASCADE, related_name='runs')
    created_by = models.ForeignKey('User', null=True, on_delete=models.SET_NULL)
    created_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True)
    status = models.CharField(max_length=12, default='QUEUED', choices=[('QUEUED','Ожидает'),('RUNNING','Выполняется'),('DONE','Завершено'),('ERROR','Ошибка')])
    server = models.CharField(max_length=255)
    database = models.CharField(max_length=128)
    trust_certificate = models.BooleanField(default=False)
    report = models.JSONField(default=dict)
    error = models.TextField(blank=True)

from .invitation_models import CompanyMailSettings,InvitationBatch,UserInvitation
from .training_models import TrainingCopy, TrainingResetRun, TrainingTicketUse
from .uat_sync_models import UatDepartment,UatVehicle,UatWaybill,UatWaybillTask,UatRepairSheet
