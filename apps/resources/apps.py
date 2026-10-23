from django.apps import AppConfig


class ResourcesConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.resources'
    verbose_name = 'Ресурсы'
    def ready(self):
        from django.db.models.signals import post_save
        from .models import Brigade
        from .position_catalog import sync_catalog_position
        post_save.connect(sync_catalog_position, sender=Brigade, dispatch_uid="resources.shared_position_catalog")
