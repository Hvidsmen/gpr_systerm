from django.apps import AppConfig


class ProductionConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.production'
    verbose_name = 'Производство'

    def ready(self):
        from . import task_signals  # noqa: F401
