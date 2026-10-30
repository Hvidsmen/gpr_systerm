from django.core.management.base import BaseCommand
from apps.accounts.invitations import drain

class Command(BaseCommand):
    help='Отправить подготовленные и подтверждённые приглашения из очереди.'
    def add_arguments(self,parser):parser.add_argument('--limit',type=int,default=100)
    def handle(self,*args,**options):
        count=drain(limit=max(1,min(options['limit'],500)),seconds=45)
        self.stdout.write('Передано почтовому серверу: '+str(count))
