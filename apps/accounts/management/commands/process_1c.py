import errno
import os
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand
from apps.accounts.one_c import process_queue
from ops.windows_runtime import atomic_json, singleton

class Command(BaseCommand):
    help = 'Process queued and scheduled read-only 1C diagnostics.'
    def add_arguments(self,parser):
        parser.add_argument('--uat-only',action='store_true',help='Обработать только безопасную диагностику УАТ.')
    def handle(self,*args,**options):
        if not options['uat_only'] or not getattr(settings,'STAGING_MODE',False):
            processed=process_queue(uat_only=options['uat_only'])
            self.stdout.write(f'Processed 1C jobs: {processed}')
            return
        runtime=Path(settings.CONFIG_DIR)/'.runtime';runtime.mkdir(parents=True,exist_ok=True)
        marker=runtime/'uat-worker.json'
        try:
            with singleton(runtime/'uat-worker.lock'):
                atomic_json(marker,{'pid':os.getpid()})
                try:processed=process_queue(uat_only=True)
                finally:marker.unlink(missing_ok=True)
        except OSError as exc:
            if exc.errno not in {errno.EACCES,errno.EAGAIN,errno.EDEADLK}:raise
            self.stdout.write('Processed 1C jobs: 0 (UAT worker already running)')
            return
        self.stdout.write(f'Processed 1C jobs: {processed}')
