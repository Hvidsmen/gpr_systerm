from django.apps import AppConfig


class ProductionConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.production'
    verbose_name = 'Производство'

    def ready(self):
        from . import task_signals  # noqa: F401
        from django.apps import apps
        from django.db.models.signals import pre_delete
        from core.fact_history import ActualHistoryMixin,record_deletion
        for model in apps.get_models():
            if issubclass(model,ActualHistoryMixin) and not model._meta.abstract:
                pre_delete.connect(record_deletion,sender=model,dispatch_uid='fact-history-delete:'+model._meta.label_lower)

