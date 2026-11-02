import sys
from django.conf import settings
from django.contrib.auth.hashers import make_password, identify_hasher, is_password_usable
from django.contrib.sessions.models import Session
from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction
from django.db.models import Q


class Command(BaseCommand):
    help = 'Sanitize an isolated acceptance DB and set a separate owner password from stdin.'

    def add_arguments(self, parser):
        parser.add_argument('--owner-password-hash', action='store_true',
                            help='Preserve an existing test owner hash supplied on stdin, never a production password.')

    def handle(self, *args, **options):
        if not getattr(settings, 'STAGING_MODE', False):raise CommandError('Команда разрешена только на отдельной тестовой базе.')
        password = sys.stdin.read(4097)
        if not 1 <= len(password) <= 4096:raise CommandError('Нужен непустой тестовый пароль длиной не более 4096 символов.')
        if options['owner_password_hash']:
            try:
                if not is_password_usable(password):raise ValueError()
                identify_hasher(password)
            except ValueError:raise CommandError('Нужен действующий хеш отдельного тестового пароля.')
            encoded = password
        else:
            encoded = make_password(password)
        from apps.accounts.models import User, Role, OneCConnection, CompanyMailSettings, InvitationBatch, UserInvitation
        from apps.accounts.training_models import TrainingCopy, TrainingTicketUse
        with connection.cursor() as cursor:cursor.execute('PRAGMA secure_delete=ON')
        state = settings.STAGING_CONFIG
        with transaction.atomic():
            owner = User.objects.get(pk=state['owner_id'], company_id=state['company_id'], archived_at=None)
            admins = User.objects.filter(Q(role__code='ADMIN') | Q(is_superuser=True),
                is_active=True, archived_at__isnull=True, company__isnull=False)
            admin_ids = list(admins.values_list('pk', flat=True))
            local_hashes = list(admins.filter(auth_source='LOCAL').values_list('pk', 'password'))
            # Keep working local passwords only for administrators. Directory
            # passwords are verified in AD, never copied or stored as plaintext.
            User.objects.all().update(password=make_password(None), is_superuser=False, is_staff=False)
            User.objects.filter(pk__in=admin_ids).update(role=Role.objects.get(code='ADMIN'))
            for pk, existing_hash in local_hashes:
                User.objects.filter(pk=pk).update(password=existing_hash)
            User.objects.filter(pk=owner.pk).update(password=encoded, role=Role.objects.get(code='ADMIN'),
                is_active=True, auth_source='LOCAL', access_group=None, access_group_from_ad=False)
            Session.objects.all().delete(); TrainingTicketUse.objects.all().delete()
            OneCConnection.objects.all().delete()
            CompanyMailSettings.objects.all().update(password_encrypted='', host='', username='', from_email='')
            InvitationBatch.objects.all().update(paused=True)
            UserInvitation.objects.exclude(status__in=['SENT', 'CANCELLED']).update(status='CANCELLED')
            TrainingCopy.objects.all().update(enabled=False, state='STOPPED')
        with connection.cursor() as cursor:
            cursor.execute('VACUUM'); cursor.execute('PRAGMA wal_checkpoint(TRUNCATE)')
        self.stdout.write('Тестовая база подготовлена. Хеши локальных паролей администраторов сохранены; пароли остальных пользователей, сессии и подключения очищены.')
