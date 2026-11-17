from django.core.management.base import BaseCommand,CommandError
from config.mssql import integrated_connection_string


class Command(BaseCommand):
    help='Проверить MSSQL и Windows-права без переключения рабочей базы портала.'

    def add_arguments(self,parser):
        parser.add_argument('--server',default='MSK-DB2')
        parser.add_argument('--database',default='YDS_PORTAL')
        parser.add_argument('--driver',default='ODBC Driver 18 for SQL Server')
        parser.add_argument('--expected-login',default=r'YAMALGAZPROM\dvivanov')

    def handle(self,*args,**options):
        try:import pyodbc
        except ImportError as exc:raise CommandError('Не установлен pyodbc. Обновите зависимости портала.') from exc
        driver=options['driver']
        if driver not in pyodbc.drivers():
            available=', '.join(pyodbc.drivers()) or 'не найдены'
            raise CommandError(f'Не найден «{driver}». Установленные ODBC-драйверы: {available}.')
        try:
            connection=pyodbc.connect(integrated_connection_string(options['server'],options['database'],driver),timeout=10,autocommit=True)
            with connection:
                row=connection.cursor().execute("SELECT DB_NAME(),SUSER_SNAME(),CONVERT(varchar(128),SERVERPROPERTY('ProductVersion')),IS_ROLEMEMBER('db_owner')").fetchone()
                connection.cursor().execute('SELECT TOP (1) 1 FROM sys.tables').fetchone()
        except pyodbc.Error as exc:
            raise CommandError('MSSQL недоступен либо у Windows-учётной записи нет доступа к базе. '+str(exc)) from exc
        if str(row[0]).casefold()!=options['database'].casefold():
            raise CommandError(f'Открыта база {row[0]}, ожидалась {options["database"]}.')
        if str(row[1]).casefold()!=options['expected_login'].casefold():
            raise CommandError(f'Подключение выполнено как {row[1]}, ожидалась учётная запись {options["expected_login"]}.')
        if row[3]!=1:
            raise CommandError(f'У {row[1]} нет роли db_owner в базе {row[0]}; миграции портала выполнять нельзя.')
        self.stdout.write(self.style.SUCCESS(f'ГОТОВО: сервер {options["server"]}; база {row[0]}; вход {row[1]}; SQL Server {row[2]}.'))
        self.stdout.write('Рабочая база портала не переключалась.')
