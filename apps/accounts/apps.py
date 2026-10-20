from django.apps import AppConfig


class AccountsConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.accounts'
    verbose_name = 'Пользователи и компании'

    def ready(self):
        import core.impersonation  # register audit attribution signals
        from . import access_groups  # register access profile synchronization
