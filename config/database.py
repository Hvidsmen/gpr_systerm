"""Database configuration shared by production and local development."""
import os
from pathlib import Path
from django.core.exceptions import ImproperlyConfigured
from config.sqlite import connection_options


def _required(environment, name):
    value=environment.get(name,'').strip()
    if not value:
        raise ImproperlyConfigured(f'Для MSSQL укажите {name}.')
    return value


def database_settings(base_dir, environment=None):
    environment=os.environ if environment is None else environment
    engine=environment.get('DJANGO_DB_ENGINE','sqlite').strip().casefold()
    if engine in {'sqlite','sqlite3'}:
        database_path=Path(environment.get('DJANGO_DB_PATH',str(Path(base_dir)/'db.sqlite3')))
        return {'ENGINE':'django.db.backends.sqlite3',
                'NAME':database_path if database_path.is_absolute() else Path(base_dir)/database_path,
                'OPTIONS':connection_options()}
    if engine not in {'mssql','sqlserver'}:
        raise ImproperlyConfigured('DJANGO_DB_ENGINE должен быть sqlite или mssql.')
    options={'unicode_results':True,'connection_timeout':10,
             'query_timeout':int(environment.get('MSSQL_QUERY_TIMEOUT','120')),
             'driver':environment.get('MSSQL_DRIVER','ODBC Driver 17 for SQL Server').strip() or 'ODBC Driver 17 for SQL Server'}
    if environment.get('MSSQL_TRUST_SERVER_CERTIFICATE','true').strip().casefold() in {'1','true','yes','on'}:
        options['extra_params']='TrustServerCertificate=yes'
    return {'ENGINE':'mssql','NAME':_required(environment,'MSSQL_NAME'),
            'HOST':_required(environment,'MSSQL_HOST'),'PORT':environment.get('MSSQL_PORT','').strip(),
            'Trusted_Connection':'yes','CONN_MAX_AGE':int(environment.get('MSSQL_CONN_MAX_AGE','60')),
            'OPTIONS':options}
