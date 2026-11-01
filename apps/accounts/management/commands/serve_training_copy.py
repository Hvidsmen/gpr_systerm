import threading
import os
import sys
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = 'Run one isolated training portal; started only by the training supervisor.'

    def add_arguments(self, parser):
        parser.add_argument('--port', type=int, required=True)

    def handle(self, *args, **options):
        if not getattr(settings, 'TRAINING_MODE', False) or not 8101 <= options['port'] <= 8199:
            raise CommandError('Неверные настройки учебного процесса.')
        from django.core.wsgi import get_wsgi_application
        from waitress import create_server
        from whitenoise import WhiteNoise
        application = WhiteNoise(get_wsgi_application(), root=str(settings.STATIC_ROOT), prefix=settings.STATIC_URL)
        server = create_server(application, listen='0.0.0.0:'+str(options['port']), threads=4, max_request_body_size=128*1024*1024)
        thread = threading.Thread(target=server.run, daemon=True); thread.start()
        stop = settings.CONFIG_DIR / 'stop'
        parent_gone = threading.Event()
        if os.environ.get('GPR_TRAINING_PARENT_PIPE') == '1':
            def monitor_parent():
                sys.stdin.buffer.read(1)
                parent_gone.set()
            threading.Thread(target=monitor_parent,daemon=True).start()
        try:
            while thread.is_alive() and not stop.exists() and not parent_gone.is_set():
                thread.join(.5)
        finally:
            server.close(); server.task_dispatcher.shutdown(timeout=90); thread.join(timeout=5)
