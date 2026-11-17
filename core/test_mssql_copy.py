from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock,patch

from django.core.management.base import CommandError
from django.test import SimpleTestCase

from apps.planning.models import EstimateReviewTask
from core.management.commands.prepare_mssql_copy import Command,dump_fixture,require_target_collation
from core.fixture_loading import fixture_loading,is_loading_fixture


class PrepareMssqlCopyTests(SimpleTestCase):
    def test_prefixed_estimate_fingerprint_fits_mssql_schema(self):
        value='works-v1:'+'a'*64
        self.assertLessEqual(len(value),EstimateReviewTask._meta.get_field('fingerprint').max_length)

    def test_target_requires_binary_collation_before_any_copy(self):
        connection=MagicMock()
        cursor=connection.cursor.return_value.__enter__.return_value
        cursor.fetchone.return_value=('Cyrillic_General_CI_AS',)
        with self.assertRaisesMessage(CommandError,'Latin1_General_100_BIN2'):
            require_target_collation(connection)
        cursor.fetchone.return_value=('Latin1_General_100_BIN2',)
        self.assertEqual(require_target_collation(connection),'Latin1_General_100_BIN2')

    def test_fixture_loading_context_is_scoped(self):
        self.assertFalse(is_loading_fixture())
        with fixture_loading():self.assertTrue(is_loading_fixture())
        self.assertFalse(is_loading_fixture())

    def test_complete_traceback_is_duplicated_to_stdout(self):
        output=StringIO();command=Command(stdout=output)
        with patch.object(command,'_handle',side_effect=ValueError('SQL failure')):
            with self.assertRaises(CommandError):
                command.handle(target='mssql',output='unused')
        log=output.getvalue()
        self.assertIn('ПОЛНАЯ ОШИБКА ПОДГОТОВКИ MSSQL',log)
        self.assertIn('ValueError: SQL failure',log)
        self.assertIn('Рабочая SQLite-база не изменялась',log)

    def test_fixture_is_written_as_utf8_on_non_utf8_windows_locale(self):
        with TemporaryDirectory() as directory:
            fixture=Path(directory)/'portal-data.json'
            def serialize(command,**options):
                self.assertEqual(command,'dumpdata')
                self.assertNotIn('output',options)
                options['stdout'].write('{"employee":"Øстров"}')
            with patch('core.management.commands.prepare_mssql_copy.call_command',side_effect=serialize):
                dump_fixture('source_snapshot',fixture)
            self.assertEqual(fixture.read_bytes(),'{"employee":"Øстров"}'.encode('utf-8'))
