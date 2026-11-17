from io import StringIO
from unittest.mock import patch

from django.core.management.base import CommandError
from django.test import SimpleTestCase

from core.management.commands.prepare_mssql_copy import Command


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
