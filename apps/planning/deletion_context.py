"""Allow locked plan deletion only inside the authorized workspace service."""
from contextvars import ContextVar
from contextlib import contextmanager

_authorized_versions = ContextVar('authorized_plan_deletions', default=frozenset())


def version_deletion_authorized(pk):
    return pk in _authorized_versions.get()


@contextmanager
def authorized_workspace_deletion(versions):
    token = _authorized_versions.set(frozenset(version.pk for version in versions))
    try:
        yield
    finally:
        _authorized_versions.reset(token)
