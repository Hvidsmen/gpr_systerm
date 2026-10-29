"""Retry a complete rolled-back write, never a statement inside a transaction."""
import logging
import sqlite3
import time
from functools import wraps
from django.db import connection, OperationalError

logger = logging.getLogger(__name__)
BUSY_MESSAGE = ('База занята другим сохранением. Изменения не сохранены; введённые значения остались в форме. '
                'Повторите сохранение через несколько секунд.')


def is_sqlite_busy(error):
    if connection.vendor != 'sqlite' or not isinstance(error, OperationalError):
        return False
    cause = error.__cause__
    code = getattr(cause, 'sqlite_errorcode', None)
    if code is not None:
        # Include SQLITE_BUSY_SNAPSHOT: the entire transaction must start again
        # from fresh rows and revisions rather than reuse its old read snapshot.
        return code & 0xff in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED)
    message = str(error).strip().lower()
    return message == 'database is locked' or message.startswith(('database table is locked', 'database schema is locked'))


def retry_sqlite_busy(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        # An outer atomic block belongs to the caller. Retrying its inner savepoint
        # would keep stale reads or an aborted transaction, so let the caller handle it.
        if connection.vendor != 'sqlite' or connection.in_atomic_block:
            return function(*args, **kwargs)
        for attempt in range(3):
            try:
                return function(*args, **kwargs)
            except OperationalError as exc:
                if not is_sqlite_busy(exc) or connection.in_atomic_block or attempt == 2:
                    raise
                logger.warning('Retrying SQLite write %s after contention (attempt %s/3)', function.__qualname__, attempt + 1)
                time.sleep((.05, .15)[attempt])
    return wrapped
