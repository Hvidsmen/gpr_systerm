from django.core.management.base import BaseCommand,CommandError
from apps.accounts.database_reset import create_users_database


class Command(BaseCommand):
    help='Создать отдельную базу с пользователями и правами, не изменяя исходную.'

    def add_arguments(self,parser):
        parser.add_argument('--source',required=True)
        parser.add_argument('--target',required=True)
        parser.add_argument('--object-access',choices=['keep','clear'],required=True)

    def handle(self,*args,**options):
        try:
            counts=create_users_database(options['source'],options['target'],options['object_access']=='keep')
        except Exception as error:raise CommandError(str(error)) from error
        self.stdout.write(self.style.SUCCESS(f"Новая база создана. Пользователей: {counts['accounts_user']}; групп доступа: {counts['accounts_accessgroup']}."))
        self.stdout.write('Сохранены пароли, AD-идентификаторы, роли, группы и разрешения. Сеансы входа не переносятся.')
