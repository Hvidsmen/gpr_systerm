from copy import deepcopy
from pathlib import Path
import sqlite3
import tempfile
from unittest import TestCase
from unittest.mock import patch
from django.conf import settings
from django.db import OperationalError
from django.db.backends.sqlite3.base import DatabaseWrapper
from config.sqlite import connection_options
from core.sqlite_busy import retry_sqlite_busy, is_sqlite_busy
from ops import windows_runtime


class SQLiteBusyTests(TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.path=Path(self.temp.name)/'portal.sqlite3'
        config=deepcopy(settings.DATABASES['default'])
        config.update(ENGINE='django.db.backends.sqlite3',NAME=str(self.path),AUTOCOMMIT=True,
            OPTIONS={**connection_options(),'timeout':.03})
        self.writer=DatabaseWrapper(config,alias='sqlite-contention-test')
        self.writer.ensure_connection();self.addCleanup(self.writer.close)
        with self.writer.cursor() as cursor:
            cursor.execute('CREATE TABLE sample (value integer)')
            cursor.execute('INSERT INTO sample VALUES (0)')

    def test_configured_wal_allows_commit_while_another_reader_is_open(self):
        reader=sqlite3.connect(self.path);self.addCleanup(reader.close)
        reader.execute('BEGIN');self.assertEqual(reader.execute('SELECT value FROM sample').fetchone(),(0,))
        with self.writer.cursor() as cursor:
            self.assertEqual(cursor.execute('PRAGMA journal_mode').fetchone()[0],'wal')
            self.assertEqual(cursor.execute('PRAGMA synchronous').fetchone()[0],2)
            cursor.execute('BEGIN');cursor.execute('UPDATE sample SET value=1')
        self.writer.commit()
        self.assertEqual(reader.execute('SELECT value FROM sample').fetchone(),(0,))
        reader.commit();self.assertEqual(reader.execute('SELECT value FROM sample').fetchone(),(1,))

    def test_busy_commit_rolls_back_before_retry_and_does_not_double_the_write(self):
        # Reproduce the journal from the deployed portal, with a reader blocking COMMIT.
        self.writer.connection.execute('PRAGMA journal_mode=DELETE')
        reader=sqlite3.connect(self.path);self.addCleanup(reader.close)
        reader.execute('BEGIN');reader.execute('SELECT value FROM sample').fetchone()
        attempts=[]
        @retry_sqlite_busy
        def increment():
            attempts.append(1)
            try:
                with self.writer.wrap_database_errors:
                    with self.writer.connection:
                        self.writer.connection.execute('BEGIN')
                        self.writer.connection.execute('UPDATE sample SET value=value+1')
            except OperationalError:
                self.assertFalse(self.writer.connection.in_transaction)
                reader.commit()
                raise
        with patch('core.sqlite_busy.connection',self.writer),patch('core.sqlite_busy.time.sleep'):
            increment()
        self.assertEqual(len(attempts),2)
        self.assertEqual(self.writer.connection.execute('SELECT value FROM sample').fetchone(),(1,))

    def test_retry_limit_and_unrelated_errors_are_not_hidden(self):
        attempts=[]
        @retry_sqlite_busy
        def busy():
            attempts.append(1);raise OperationalError('database is locked')
        with patch('core.sqlite_busy.connection',self.writer),patch('core.sqlite_busy.time.sleep'):
            with self.assertRaises(OperationalError):busy()
        self.assertEqual(len(attempts),3)
        attempts.clear()
        with patch('core.sqlite_busy.connection',self.writer),patch.object(self.writer,'in_atomic_block',True):
            with self.assertRaises(OperationalError):busy()
        self.assertEqual(len(attempts),1)
        with patch('core.sqlite_busy.connection',self.writer):
            self.assertFalse(is_sqlite_busy(OperationalError('no such table: missing')))
            with patch.object(self.writer,'vendor','postgresql'):
                self.assertFalse(is_sqlite_busy(OperationalError('database is locked')))

    def test_backup_includes_committed_wal_data_without_copying_sidecars(self):
        self.writer.connection.execute('PRAGMA wal_autocheckpoint=0')
        self.writer.connection.execute('UPDATE sample SET value=7')
        self.assertTrue(Path(str(self.path)+'-wal').exists())
        backup=windows_runtime.backup_database({'database':str(self.path)},Path(self.temp.name)/'.runtime')
        with sqlite3.connect(backup) as copy:
            self.assertEqual(copy.execute('SELECT value FROM sample').fetchone(),(7,))
