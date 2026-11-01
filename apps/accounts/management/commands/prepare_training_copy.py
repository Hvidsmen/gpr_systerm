from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction, connection
from django.contrib.sessions.models import Session
from ops.windows_runtime import read_json


class Command(BaseCommand):
    help = 'Remove live credentials/queues and grant admins only inside an isolated training database.'

    def handle(self, *args, **options):
        if not getattr(settings, 'TRAINING_MODE', False):
            raise CommandError('Команда разрешена только в отдельном учебном процессе.')
        access = read_json(settings.TRAINING_CONTROL / 'access.json', {})
        from apps.accounts.models import User, Role, OneCConnection, CompanyMailSettings, InvitationBatch, UserInvitation
        from apps.accounts.training_models import TrainingCopy, TrainingTicketUse
        from django.contrib.auth.hashers import make_password
        with connection.cursor() as cursor:
            cursor.execute('PRAGMA secure_delete=ON')
        with transaction.atomic():
            Session.objects.all().delete()
            TrainingTicketUse.objects.all().delete()
            User.objects.all().update(password=make_password(None), is_active=False, is_superuser=False, is_staff=False)
            User.objects.filter(pk__in=access.get('members', []), company_id=access.get('company'), archived_at=None).update(
                is_active=True, role=Role.objects.get(code='ADMIN'), access_group=None, access_group_from_ad=False)
            OneCConnection.objects.all().delete()
            CompanyMailSettings.objects.all().update(password_encrypted='',host='',username='',from_email='')
            InvitationBatch.objects.all().update(paused=True)
            UserInvitation.objects.exclude(status__in=['SENT','CANCELLED']).update(status='CANCELLED')
            TrainingCopy.objects.all().update(enabled=False,state='STOPPED')
        # Discard old hashes/encrypted credentials from SQLite free pages as well.
        # This command has only the isolated database open and no HTTP workers yet.
        with connection.cursor() as cursor:
            cursor.execute('VACUUM')
            cursor.execute('PRAGMA wal_checkpoint(TRUNCATE)')
        self.stdout.write('Учебная база подготовлена; внешние интеграции отключены.')
