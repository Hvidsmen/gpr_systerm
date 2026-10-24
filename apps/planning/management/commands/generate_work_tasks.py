from django.core.management.base import BaseCommand
from core.permissions import DISPATCHER_ROLES
from apps.accounts.models import User,Company
from apps.planning.task_services import generate_daily,sync_company


class Command(BaseCommand):
    help='Создать ежедневные задания диспетчеров, восстановить очередь проверок и согласований. Можно запускать каждый день.'
    def handle(self,*args,**options):
        count=0
        for user in User.objects.filter(is_active=True,company__is_active=True,role__code__in=DISPATCHER_ROLES).select_related('company','role'):
            count+=generate_daily(user)
        for company in Company.objects.filter(is_active=True):sync_company(company)
        self.stdout.write(self.style.SUCCESS(f'Очередь обновлена. Обработано ежедневных заданий: {count}.'))
