"""Only the role-checked workflow may change global approval metadata."""
from contextlib import contextmanager
from contextvars import ContextVar

_authorized = ContextVar('global_plan_transitions', default=frozenset())


def transition_authorized(pk):
    return pk in _authorized.get()


@contextmanager
def authorized_transition(pk):
    token = _authorized.set(_authorized.get() | {pk})
    try:
        yield
    finally:
        _authorized.reset(token)
