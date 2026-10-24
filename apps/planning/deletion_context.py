"""Allow locked plan deletion only inside the authorized workspace service."""
from contextvars import ContextVar
from contextlib import contextmanager

_authorized_versions = ContextVar('authorized_plan_deletions', default=frozenset())
_authorized_projects = ContextVar('authorized_project_plan_deletions', default=frozenset())

def project_deletion_authorized(pk):
    return pk in _authorized_projects.get()


def version_deletion_authorized(pk):
    return pk in _authorized_versions.get()


@contextmanager
def authorized_workspace_deletion(versions,project_versions=()):
    token = _authorized_versions.set(frozenset(version.pk for version in versions))
    project_token = _authorized_projects.set(frozenset(version.pk for version in project_versions))
    try:
        yield
    finally:
        _authorized_versions.reset(token)
        _authorized_projects.reset(project_token)
