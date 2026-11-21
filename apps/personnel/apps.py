from django.apps import AppConfig


class PersonnelConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.personnel'
    verbose_name = 'Кадровая служба'
    def ready(self):
        from . import lodging_sync_signals  # noqa: F401
