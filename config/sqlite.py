"""SQLite readers must not block commits made by another portal request."""


def connection_options():
    return {
        'timeout': 10,
        # WAL allows a report or an open plan read to coexist with a committing
        # writer. Keep FULL durability; backups use SQLite's backup API so they
        # also include committed data which has not been checkpointed yet.
        'init_command': 'PRAGMA journal_mode=WAL; PRAGMA synchronous=FULL;',
    }
