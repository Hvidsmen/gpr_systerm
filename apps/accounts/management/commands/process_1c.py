from django.core.management.base import BaseCommand
from apps.accounts.one_c import process_queue

class Command(BaseCommand):
    help = 'Process queued and scheduled read-only 1C diagnostics.'
    def add_arguments(self,parser):
        parser.add_argument('--uat-only',action='store_true',help='Обработать только безопасную диагностику УАТ.')
    def handle(self,*args,**options):
        processed=process_queue(uat_only=options['uat_only'])
        self.stdout.write(f'Processed 1C jobs: {processed}')
