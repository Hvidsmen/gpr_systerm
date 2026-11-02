import sys
from django.conf import settings
from django.contrib.auth.hashers import make_password
from django.contrib.sessions.models import Session
from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction


class Command(BaseCommand):
    help = 'Sanitize an isolated acceptance DB and set a separate owner password from stdin.'

    def handle(self, *args, **options):
        if not getattr(settings, 'STAGING_MODE', False):raise CommandError('Команда разрешена только на отдельной тестовой базе.')
        password = sys.stdin.read(4097)
        if not 1 <= len(password) <= 4096:raise CommandError('Нужен непустой тестовый пароль длиной не более 4096 символов.')
        from apps.accounts.models import User, Role, OneCConnection, CompanyMailSettings, InvitationBatch, UserInvitation
        from apps.accounts.training_models import TrainingCopy, TrainingTicketUse
        with connection.cursor() as cursor:cursor.execute('PRAGMA secure_delete=ON')
        state = settings.STAGING_CONFIG
        with transaction.atomic():
            owner = User.objects.get(pk=state['owner_id'], company_id=state['company_id'], archived_at=None)
            # Keep role accounts active for the owner's impersonation, never for login.
            User.objects.all().update(password=make_password(None), is_superuser=False, is_staff=False)
            User.objects.filter(pk=owner.pk).update(password=make_password(password), role=Role.objects.get(code='ADMIN'),
                is_active=True, auth_source='LOCAL', access_group=None, access_group_from_ad=False)
            Session.objects.all().delete(); TrainingTicketUse.objects.all().delete()
            OneCConnection.objects.all().delete()
            CompanyMailSettings.objects.all().update(password_encrypted='', host='', username='', from_email='')
            InvitationBatch.objects.all().update(paused=True)
            UserInvitation.objects.exclude(status__in=['SENT', 'CANCELLED']).update(status='CANCELLED')
            TrainingCopy.objects.all().update(enabled=False, state='STOPPED')
        with connection.cursor() as cursor:
            cursor.execute('VACUUM'); cursor.execute('PRAGMA wal_checkpoint(TRUNCATE)')
        self.stdout.write('Отдельная тестовая база подготовлена. Пароли, сессии и действующие подключения очищены.')
