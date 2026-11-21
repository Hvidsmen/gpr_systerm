from django.core.management.base import BaseCommand,CommandError
from apps.personnel import lodging_sync


class Command(BaseCommand):
    help='Calculate one queued lodging reconciliation snapshot.'
    def add_arguments(self,parser):parser.add_argument('state',type=int)
    def handle(self,*args,**options):
        try:lodging_sync.calculate_state(options['state'])
        except Exception as exc:raise CommandError(str(exc)) from exc
