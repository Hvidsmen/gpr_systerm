from pathlib import Path
from tempfile import TemporaryDirectory
from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase
from config.database import database_settings
from config.mssql import integrated_connection_string,available_sql_server_driver


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
