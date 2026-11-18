# This file is the template's, edited by hand for the account model (class human).
import contextlib
import os
import sys
import threading
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager
from typing import Any

import pytest
from xippios.core.testing import keep_reserved_tenants

from xippios.google.mail._client import DEMO, TENANTS

# Core's plugin keeps the default run offline and free of real keys (ADR-0013), runs anyio
# tests on asyncio and serves the family check in tests/test_family.py; the variables it
# sets to the placeholder are named under xippios_placeholder_key_envs in pyproject.toml,
# not here: GOOGLE_DEMO_REFRESH_TOKEN, GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET. No
# network outside a test marked live or network, no rate-limit waits, and every
# RefreshTokenAuth sends PLACEHOLDER_KEY as every credential of the refresh, pinned on
# RenewingBearerAuth.credential. What stays here is which accounts exist in a test: demo
# only.
pytest_plugins = ["xippios.core.testing.plugin"]

# Once, when pytest imports this file, before any fixture of any scope: GOOGLE_ACCOUNTS and
# every account's refresh token but demo's go, GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET stay.
keep_reserved_tenants(TENANTS)


@pytest.fixture(autouse=True)
def demo_only(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Only the demo account can exist in a test.

    The process holds no account token but demo's (stripped at import), and
    ``GOOGLE_ACCOUNTS`` is removed again for every test; core's plugin gives an offline
    test the placeholder token. Every cached client is dropped before and after each test,
    so no test is handed a client, or an access token, another test got.
    """
    monkeypatch.delenv(TENANTS.list_var, raising=False)
    TENANTS.reset()
    yield
    TENANTS.reset()


@pytest.fixture
def demo_account() -> str:
    """The one account a live test may reach: ``demo``, and only when it is all there is."""
    names = TENANTS.names()
    assert names == (DEMO,), "live tests run against the demo account and nothing else"
    return DEMO


_WRITE_FLAGS = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_APPEND | os.O_TRUNC
_WRITE_EVENTS = frozenset(
    {
        "os.mkdir",
        "os.rename",
        "os.replace",
        "os.remove",
        "os.rmdir",
        "os.truncate",
        "os.link",
        "os.symlink",
        "shutil.copyfile",
        "tempfile.mkstemp",
        "tempfile.mkdtemp",
    }
)


class _WriteAudit:
    """Every file write the process attempts, in any thread, while ``watching``.

    One audit hook for the session (``sys.addaudithook`` cannot be removed), off unless
    a test watches: ``open`` of a path with a writing mode or flags, and the file-system
    events of ``_WRITE_EVENTS``. Not counted: Python's own bytecode cache
    (``__pycache__``), and an ``open`` whose path is an ``int``, a descriptor already open,
    which reaches no new file: from Python 3.13 on, ``socket.makefile`` wraps an accepted
    connection's descriptor through ``io.open`` and raises that event, as the consent
    tool's loopback listener does for the redirect. Writes through a descriptor opened
    before the watch are outside its reach.
    """

    def __init__(self) -> None:
        self._on = threading.Event()
        self.writes: list[tuple[str, str]] = []

    def __call__(self, event: str, args: tuple[Any, ...]) -> None:
        if not self._on.is_set():
            return
        if event == "open":
            path, mode, flags = (*args, None, None, None)[:3]
            if isinstance(path, int):
                return  # a descriptor already open: socket.makefile on 3.13+, no new file
            writing = (isinstance(mode, str) and any(c in mode for c in "wax+")) or (
                isinstance(flags, int) and bool(flags & _WRITE_FLAGS)
            )
        elif event in _WRITE_EVENTS:
            path, writing = (args[0] if args else None), True
        else:
            return
        if writing and "__pycache__" not in str(path):
            self.writes.append((event, str(path)))

    @contextlib.contextmanager
    def watching(self) -> Iterator[list[tuple[str, str]]]:
        self.writes = []
        self._on.set()
        try:
            yield self.writes
        finally:
            self._on.clear()


_AUDIT = _WriteAudit()
sys.addaudithook(_AUDIT)


@pytest.fixture
def no_writes() -> Callable[[], AbstractContextManager[list[tuple[str, str]]]]:
    """``with no_writes() as writes:`` collects every file write attempted inside the block."""
    return _AUDIT.watching
