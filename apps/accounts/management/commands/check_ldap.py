from django.conf import settings
from django.core.management.base import BaseCommand,CommandError


class Command(BaseCommand):
    help='Проверить служебное подключение LDAP и доступ к базе поиска без вывода паролей.'
    def handle(self,*args,**options):
        if not settings.LDAP_ENABLED:raise CommandError('Включите LDAP_ENABLED в окружении сервера.')
        if not settings.LDAP_BIND_PASSWORD:raise CommandError('Не задан LDAP_BIND_PASSWORD.')
        try:
            from ldap3 import Server,Connection,SIMPLE,NONE,SUBTREE
            from ldap3.core.exceptions import LDAPException
        except ImportError:raise CommandError('Установите зависимости из requirements.txt.')
        successful=0
        for host in settings.LDAP_SERVERS:
            connection=None
            try:
                server=Server(host,port=settings.LDAP_PORT,use_ssl=False,get_info=NONE,connect_timeout=settings.LDAP_TIMEOUT)
                connection=Connection(server,user=settings.LDAP_BIND_USER,password=settings.LDAP_BIND_PASSWORD,authentication=SIMPLE,auto_bind=True,auto_referrals=False,raise_exceptions=True,receive_timeout=settings.LDAP_TIMEOUT)
                connection.search(settings.LDAP_BASE_DN,'(objectClass=organizationalUnit)',search_scope='BASE',attributes=['distinguishedName'])
                if not connection.entries:raise CommandError('База поиска не найдена.')
                self.stdout.write(self.style.SUCCESS(f'{host}:{settings.LDAP_PORT}: подключение и база поиска доступны.'))
                successful+=1
            except (LDAPException,OSError,CommandError):self.stdout.write(self.style.WARNING(f'{host}:{settings.LDAP_PORT}: проверка не пройдена. Проверьте адрес, учетные данные и Base DN.'))
            finally:
                if connection:
                    try:connection.unbind()
                    except LDAPException:pass
        if not successful:raise CommandError('Ни один LDAP-сервер не прошел проверку.')
