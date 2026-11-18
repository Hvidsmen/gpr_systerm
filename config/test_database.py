import importlib
from pathlib import Path
from tempfile import TemporaryDirectory
from django.core.exceptions import ImproperlyConfigured
from django.apps import apps
from django.db.models import UniqueConstraint
from django.test import SimpleTestCase
from config.database import database_settings
from config.mssql import integrated_connection_string,available_sql_server_driver
from mssql.base import DatabaseWrapper
from apps.personnel.models import Person
from apps.resources.models import Employee,Equipment


class DatabaseSettingsTests(SimpleTestCase):
    def test_sqlite_remains_default(self):
        with TemporaryDirectory() as directory:
            settings=database_settings(Path(directory),{})
        self.assertEqual(settings['ENGINE'],'django.db.backends.sqlite3')
        self.assertTrue(str(settings['NAME']).endswith('db.sqlite3'))

    def test_mssql_uses_integrated_windows_authentication(self):
        settings=database_settings(Path('.'),{'DJANGO_DB_ENGINE':'mssql','MSSQL_HOST':'MSK-DB2','MSSQL_NAME':'YDS_PORTAL'})
        self.assertEqual(settings['ENGINE'],'mssql')
        self.assertEqual((settings['HOST'],settings['NAME']),('MSK-DB2','YDS_PORTAL'))
        self.assertEqual(settings['Trusted_Connection'],'yes')
        self.assertNotIn('USER',settings);self.assertNotIn('PASSWORD',settings)
        self.assertIn('TrustServerCertificate=yes',settings['OPTIONS']['extra_params'])
        self.assertEqual(settings['OPTIONS']['connection_timeout'],10)
        self.assertEqual(settings['OPTIONS']['query_timeout'],120)

    def test_mssql_query_timeout_can_be_extended_for_controlled_bulk_copy(self):
        settings=database_settings(Path('.'),{'DJANGO_DB_ENGINE':'mssql','MSSQL_HOST':'MSK-DB2',
            'MSSQL_NAME':'YDS_PORTAL_TEST','MSSQL_QUERY_TIMEOUT':'900'})
        self.assertEqual(settings['OPTIONS']['query_timeout'],900)

    def test_mssql_defaults_to_confirmed_windows_driver(self):
        settings=database_settings(Path('.'),{'DJANGO_DB_ENGINE':'mssql','MSSQL_HOST':'MSK-DB2','MSSQL_NAME':'YDS_PORTAL'})
        self.assertEqual(settings['OPTIONS']['driver'],'ODBC Driver 17 for SQL Server')

    def test_mssql_requires_server_and_database(self):
        for values in ({'DJANGO_DB_ENGINE':'mssql','MSSQL_NAME':'YDS_PORTAL'},
                       {'DJANGO_DB_ENGINE':'mssql','MSSQL_HOST':'MSK-DB2'}):
            with self.assertRaises(ImproperlyConfigured):database_settings(Path('.'),values)

    def test_readiness_connection_is_encrypted_and_integrated(self):
        value=integrated_connection_string('MSK-DB2','YDS_PORTAL')
        self.assertIn('Trusted_Connection=yes',value);self.assertIn('Encrypt=yes',value)
        self.assertIn('SERVER={MSK-DB2}',value);self.assertIn('DATABASE={YDS_PORTAL}',value)

    def test_readiness_prefers_driver_18_and_falls_back_to_installed_17(self):
        both=['SQL Server','ODBC Driver 17 for SQL Server','ODBC Driver 18 for SQL Server']
        self.assertEqual(available_sql_server_driver(both),'ODBC Driver 18 for SQL Server')
        self.assertEqual(available_sql_server_driver(both[:-1]),'ODBC Driver 17 for SQL Server')
        self.assertEqual(available_sql_server_driver(both,'ODBC Driver 17 for SQL Server'),'ODBC Driver 17 for SQL Server')

    def test_filtered_nonempty_unique_indexes_use_sql_server_supported_predicates(self):
        values={'ENGINE':'mssql','NAME':'test','HOST':'server','PORT':'','USER':'','PASSWORD':'',
                'OPTIONS':{'driver':'ODBC Driver 17 for SQL Server'},'TIME_ZONE':None,
                'CONN_HEALTH_CHECKS':False,'CONN_MAX_AGE':0,'AUTOCOMMIT':True,
                'ATOMIC_REQUESTS':False,'TEST':{}}
        connection=DatabaseWrapper(values,'mssql-test')
        editor=connection.schema_editor(collect_sql=True,atomic=False)
        statements=[]
        for model in (Equipment,Employee,Person):
            for constraint in model._meta.constraints:
                if getattr(constraint,'condition',None) is not None:
                    statements.append(str(constraint.create_sql(model,editor)))
        sql='\n'.join(statements)
        self.assertNotIn(' WHERE NOT ',sql)
        self.assertIn("WHERE [plate_number] > ''",sql)
        self.assertIn("WHERE [zup_ref] > ''",sql)
        self.assertIn("WHERE [employee_number] > ''",sql)

    def test_nullable_columns_never_use_unfiltered_composite_uniqueness(self):
        unsafe=[]
        for model in apps.get_models():
            for fields in model._meta.unique_together:
                if any(model._meta.get_field(field).null for field in fields):
                    unsafe.append(f'{model._meta.label}: unique_together {fields}')
            for constraint in model._meta.constraints:
                if isinstance(constraint,UniqueConstraint) and not constraint.condition:
                    if any(model._meta.get_field(field).null for field in constraint.fields):
                        unsafe.append(f'{model._meta.label}: {constraint.name}')
        self.assertEqual(unsafe,[])

    def test_carryover_unique_index_is_added_after_field_becomes_nullable(self):
        migration=importlib.import_module('apps.planning.migrations.0029_alter_carryoverreviewtask_source_version_and_more').Migration
        self.assertEqual(type(migration.operations[0]).__name__,'AlterField')
        self.assertEqual(migration.operations[0].name,'source_version')
        self.assertEqual(type(migration.operations[1]).__name__,'AddConstraint')
        self.assertEqual(migration.operations[1].constraint.name,'unique_carryover_review_task')
