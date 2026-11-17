"""Suppress application side effects while trusted fixtures are restored."""
from contextlib import contextmanager
from contextvars import ContextVar


_active=ContextVar('gpr_fixture_loading',default=False)


def is_loading_fixture():
    return _active.get()


@contextmanager
def fixture_loading():
    token=_active.set(True)
    try:
        yield
    finally:
        _active.reset(token)
