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
    ad_group_dn = models.CharField('DN группы AD', max_length=1024, blank=True, help_text='Полный distinguishedName группы AD. Пусто — только ручное назначение.')

    class Meta:
        ordering = ['name']
        constraints = [models.UniqueConstraint(fields=['company', 'name'], name='unique_access_group_name')]

    def __str__(self):
        return self.name


class User(AbstractUser):
    access_group = models.ForeignKey(AccessGroup, null=True, blank=True, on_delete=models.PROTECT, related_name='members', verbose_name='Группа доступа')
    access_group_from_ad = models.BooleanField(default=False, editable=False)

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

from django.core.exceptions import ValidationError
from django.db.models.signals import m2m_changed, pre_save
from django.dispatch import receiver


@receiver(m2m_changed, sender=User.assigned_objects.through)
def validate_object_assignments(sender, instance, action, reverse, pk_set, **kwargs):
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
def protect_assignment_company(sender, instance, **kwargs):
    if instance.pk and instance.assigned_objects.exclude(company_id=instance.company_id).exists():
        raise ValidationError('Перед сменой компании удалите назначения строительных объектов.')


@receiver(m2m_changed,sender=User.assigned_objects.through)
def reset_daily_task_subscriptions(sender,instance,action,reverse,**kwargs):
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
