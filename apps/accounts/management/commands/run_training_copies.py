from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = 'Supervise training groups; refresh daily at 07:00 Europe/Moscow.'

    def handle(self, *args, **options):
        from apps.accounts.training_runtime import supervise
        try:
            supervise()
        except RuntimeError as error:
            raise CommandError(str(error))
