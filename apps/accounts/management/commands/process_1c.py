from django.core.management.base import BaseCommand
from apps.accounts.one_c import process_queue

class Command(BaseCommand):
    help = 'Process queued and scheduled read-only 1C diagnostics.'
    def handle(self,*args,**options):
        process_queue()
