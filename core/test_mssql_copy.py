from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.core.management.base import CommandError
from django.test import SimpleTestCase

from core.management.commands.prepare_mssql_copy import Command,dump_fixture


class PrepareMssqlCopyTests(SimpleTestCase):
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
