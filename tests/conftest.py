# Queue row 281: the demo-only strip is a copy of sdk-cashctrl's tests/conftest.py, part of
# the kit that returns to xippios-core in 0.4.0 (ADR-0023, decision 4; ADR-0025, D-d).
# This file is the template's, edited by hand for the account model (class human).
import os
import re
from collections.abc import Iterator

import pytest

from xippios.google.mail._client import DEMO, TENANTS

# Core's plugin keeps the default run offline and free of real keys (ADR-0013), runs anyio
# tests on asyncio and serves the family check in tests/test_family.py; the variables it
# sets to the placeholder are named under xippios_placeholder_key_envs in pyproject.toml,
# not here: GOOGLE_DEMO_REFRESH_TOKEN, GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET. No
# network outside a test marked live or network, no rate-limit waits, and every
# RefreshTokenAuth sends PLACEHOLDER_KEY as the refresh token, pinned on EnvKeyAuth. What
# stays here is which accounts exist in a test: demo only.
pytest_plugins = ["xippios.core.testing.plugin"]

DEMO_TOKEN = TENANTS.var(DEMO, "REFRESH_TOKEN")
_ACCOUNT_TOKEN = re.compile(r"GOOGLE_[A-Z0-9_]+_REFRESH_TOKEN")


def _strip_productive_accounts() -> None:
    """Remove ``GOOGLE_ACCOUNTS`` and every account's refresh token but demo's.

    Runs once, when pytest imports this file, before any fixture of any scope: no
    session- or module-scoped fixture, and no test, can see another account's name or
    token. Demo's token stays for the live tests, and so do ``GOOGLE_CLIENT_ID`` and
    ``GOOGLE_CLIENT_SECRET``, the OAuth client every account shares.
    """
    for name in list(os.environ):
        if name == TENANTS.list_var or (_ACCOUNT_TOKEN.fullmatch(name) and name != DEMO_TOKEN):
            del os.environ[name]


_strip_productive_accounts()


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
