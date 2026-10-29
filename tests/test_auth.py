"""RefreshTokenAuth (ADR-0025, D-b): one refresh, its reuse, the 401 retry, the refusals, no disk.

Every exchange is a recorded one: the refresh (``oauth2_token/ok``), the token endpoint's
``invalid_grant`` and ``invalid_client``, the profile and its 401 to a garbage bearer
(``get_profile/unauthenticated``). Offline, core's plugin pins ``EnvKeyAuth.key`` and sets
``GOOGLE_DEMO_REFRESH_TOKEN``, ``GOOGLE_CLIENT_ID`` and ``GOOGLE_CLIENT_SECRET`` to its
placeholder (``xippios_placeholder_key_envs``), so the refresh sends the placeholder.
"""

import json
import os
from collections.abc import Callable
from contextlib import AbstractContextManager
from pathlib import Path
from urllib.parse import parse_qsl

import httpx
import pytest
import respx
from xippios.core import AsyncClient, AuthError, ConfigError, ValidationError
from xippios.core.testing import Fixture, ReplayFixture, fixture_path, load_fixture
from xippios.core.testing.plugin import PLACEHOLDER_KEY

from xippios.google.mail import api, tasks
from xippios.google.mail._auth import (
    CLIENT_ID_ENV,
    CLIENT_SECRET_ENV,
    EXPIRY_MARGIN,
    SCOPE_SETS,
    TOKEN_URL,
    RefreshTokenAuth,
)
from xippios.google.mail._client import DEMO, SOURCE, TENANTS, account_config

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures"
PROFILE_URL = "https://gmail.googleapis.com/gmail/v1/users/me/profile"
REVOKED = (
    "account demo: refresh token revoked or expired; re-run xippios-google-mail-auth "
    "--account demo and set GOOGLE_DEMO_REFRESH_TOKEN"
)


def recorded(endpoint: str, case: str) -> Fixture:
    return load_fixture(fixture_path(FIXTURES, endpoint, case))


def answer(fixture: Fixture) -> httpx.Response:
    """The recorded answer as a fresh response, for a route that answers in turn."""
    return httpx.Response(
        fixture.response.status,
        headers=fixture.response.headers,
        content=fixture.response.content,
    )


class Clock:
    """Monotonic seconds a test moves by hand."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def demo_client(clock: Clock | None = None) -> tuple[AsyncClient, RefreshTokenAuth]:
    """Demo's client as ``client_for`` builds it, on an auth the test holds."""
    var = TENANTS.var(DEMO, "REFRESH_TOKEN")
    auth = (
        RefreshTokenAuth(var, account=DEMO, source=SOURCE)
        if clock is None
        else RefreshTokenAuth(var, account=DEMO, source=SOURCE, clock=clock)
    )
    return AsyncClient(account_config(auth), auth=auth), auth


def test_one_refresh_serves_every_call_within_the_expiry(replay_fixture: ReplayFixture) -> None:
    token = replay_fixture("oauth2_token", "ok")
    profile = replay_fixture("get_profile", "ok")
    tasks.get_profile(DEMO)
    tasks.get_profile(DEMO)
    assert (token.call_count, profile.call_count) == (1, 2)


def test_the_refresh_sends_the_documented_form_and_the_bearer_the_access_token(
    replay_fixture: ReplayFixture,
) -> None:
    """``oauth2-web-server.md``'s refresh: four form fields; then the access token as bearer."""
    token = replay_fixture("oauth2_token", "ok")
    profile = replay_fixture("get_profile", "ok")
    tasks.get_profile(DEMO)
    form = dict(parse_qsl(token.calls.last.request.content.decode()))
    assert sorted(form) == ["client_id", "client_secret", "grant_type", "refresh_token"]
    assert form["grant_type"] == "refresh_token"
    assert form["refresh_token"] == PLACEHOLDER_KEY, "the key is the refresh token"
    assert form["client_id"] == form["client_secret"] == PLACEHOLDER_KEY
    sent = profile.calls.last.request.headers["authorization"]
    assert sent == "Bearer REDACTED", "the recorded access token, never the refresh token"


@pytest.mark.anyio
async def test_a_token_is_renewed_within_the_margin_of_its_expiry(
    replay_fixture: ReplayFixture,
) -> None:
    token = replay_fixture("oauth2_token", "ok")
    replay_fixture("get_profile", "ok")
    expires_in = json.loads(recorded("oauth2_token", "ok").response.body)["expires_in"]
    clock = Clock()
    client, _ = demo_client(clock)
    await api.get_profile(client)
    clock.now += expires_in - EXPIRY_MARGIN - 1
    await api.get_profile(client)
    assert token.call_count == 1, "outside the margin the token is reused"
    clock.now += 2
    await api.get_profile(client)
    assert token.call_count == 2, "within 60 s of its expiry it is refreshed"
    await client.aclose()


@pytest.mark.anyio
async def test_the_answer_s_granted_scopes_and_refresh_lifetime_are_kept(
    replay_fixture: ReplayFixture,
) -> None:
    replay_fixture("oauth2_token", "ok")
    replay_fixture("get_profile", "ok")
    client, auth = demo_client()
    assert (auth.granted_scopes, auth.refresh_token_expires_in) == (None, None)
    await api.get_profile(client)
    recorded_answer = json.loads(recorded("oauth2_token", "ok").response.body)
    assert auth.granted_scopes == frozenset(SCOPE_SETS["read"])
    assert auth.refresh_token_expires_in == recorded_answer["refresh_token_expires_in"]
    auth.reset()
    assert auth.granted_scopes == frozenset(SCOPE_SETS["read"]), "reset forgets the token only"
    await client.aclose()


def test_a_401_refreshes_once_and_sends_the_request_again(
    replay_fixture: ReplayFixture, respx_mock: respx.MockRouter
) -> None:
    token = replay_fixture("oauth2_token", "ok")
    profile = respx_mock.get(PROFILE_URL)
    profile.side_effect = [
        answer(recorded("get_profile", "unauthenticated")),
        answer(recorded("get_profile", "ok")),
    ]
    result = tasks.get_profile(DEMO)
    assert result.data.email_address == "user@example.com"
    assert (token.call_count, profile.call_count) == (2, 2)


def test_a_second_401_is_an_auth_error(
    replay_fixture: ReplayFixture, respx_mock: respx.MockRouter
) -> None:
    token = replay_fixture("oauth2_token", "ok")
    profile = replay_fixture("get_profile", "unauthenticated")
    with pytest.raises(AuthError) as info:
        tasks.get_profile(DEMO)
    assert info.value.status == 401
    assert info.value.message == (
        "GET /gmail/v1/users/me/profile answered HTTP 401 authError: Gmail refused account "
        "demo's access token, also after one refresh; if the account's grant was revoked, "
        "re-run xippios-google-mail-auth --account demo --scopes read and set "
        "GOOGLE_DEMO_REFRESH_TOKEN"
    ), "the refresh granted gmail.readonly, the read set"
    assert (token.call_count, profile.call_count) == (2, 2), "one refresh, one resend, no more"


def test_invalid_grant_is_the_reconsent_error_and_never_retried(
    replay_fixture: ReplayFixture,
) -> None:
    token = replay_fixture("oauth2_token", "invalid_grant")
    profile = replay_fixture("get_profile", "ok")
    with pytest.raises(AuthError) as info:
        tasks.get_profile(DEMO)
    assert info.value.message == REVOKED
    assert info.value.status == 400
    assert (token.call_count, profile.call_count) == (1, 0), "no retry, no Gmail request"


def test_invalid_client_is_a_config_error_naming_the_client_variables(
    replay_fixture: ReplayFixture,
) -> None:
    token = replay_fixture("oauth2_token", "invalid_client")
    profile = replay_fixture("get_profile", "ok")
    with pytest.raises(ConfigError) as info:
        tasks.get_profile(DEMO)
    assert CLIENT_ID_ENV in info.value.message
    assert CLIENT_SECRET_ENV in info.value.message
    assert "invalid_client" in info.value.message
    assert (token.call_count, profile.call_count) == (1, 0)


@pytest.mark.parametrize("variable", [CLIENT_ID_ENV, CLIENT_SECRET_ENV])
def test_an_unset_client_variable_sends_nothing(
    replay_fixture: ReplayFixture, monkeypatch: pytest.MonkeyPatch, variable: str
) -> None:
    token = replay_fixture("oauth2_token", "ok")
    profile = replay_fixture("get_profile", "ok")
    monkeypatch.delenv(variable)
    with pytest.raises(ConfigError, match=f"^{variable} is not set; export"):
        tasks.get_profile(DEMO)
    assert (token.call_count, profile.call_count) == (0, 0)


def test_an_answer_that_is_not_the_documented_one_is_not_quoted(
    respx_mock: respx.MockRouter,
) -> None:
    """A synthetic 200 without the documented fields: refused, nothing of it in the message."""
    respx_mock.post(TOKEN_URL).respond(200, json={"unexpected": "a-value-never-shown"})
    with pytest.raises(ValidationError) as info:
        tasks.get_profile(DEMO)
    assert "a-value-never-shown" not in str(info.value)
    assert info.value.message.startswith("POST /token answered HTTP 200, but not the documented")


def _files(root: Path) -> set[Path]:
    """Every file under ``root``, but caches, the virtual environment and git's own."""
    skipped = {".git", ".venv", ".mypy_cache", ".ruff_cache", ".pytest_cache", "__pycache__"}
    found: set[Path] = set()
    for directory, names, files in os.walk(root):
        names[:] = [name for name in names if name not in skipped]
        found.update(Path(directory) / name for name in files)
    return found


def test_nothing_is_written_to_disk(
    replay_fixture: ReplayFixture,
    respx_mock: respx.MockRouter,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    no_writes: Callable[[], AbstractContextManager[list[tuple[str, str]]]],
) -> None:
    """A refresh, its reuse and a 401's renewal attempt no file write anywhere.

    An audit hook watches every ``open`` for writing and every file-system change of the
    process, in every thread, the sync facade's loop among them; a first call outside the
    watch loads what the calls import. HOME, the cwd and the repository are listed too.
    """
    home, cwd = tmp_path / "home", tmp_path / "cwd"
    home.mkdir()
    cwd.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.chdir(cwd)
    before = _files(ROOT)
    token = replay_fixture("oauth2_token", "ok")
    ok, refused = recorded("get_profile", "ok"), recorded("get_profile", "unauthenticated")
    profile = respx_mock.get(PROFILE_URL)
    profile.side_effect = [answer(ok), answer(ok), answer(ok), answer(refused), answer(ok)]
    tasks.get_profile(DEMO)  # outside the watch: the first imports
    TENANTS.reset()
    with no_writes() as writes:
        tasks.get_profile(DEMO)  # a refresh
        tasks.get_profile(DEMO)  # its reuse
        tasks.get_profile(DEMO)  # a 401, renewed and sent again
    assert writes == []
    assert (token.call_count, profile.call_count) == (3, 5)
    assert list(home.iterdir()) == []
    assert list(cwd.iterdir()) == []
    assert _files(ROOT) == before


def test_the_write_audit_sees_a_write(
    tmp_path: Path, no_writes: Callable[[], AbstractContextManager[list[tuple[str, str]]]]
) -> None:
    """The watch the test above relies on reports an ``open`` for writing and a ``mkdir``."""
    with no_writes() as writes:
        (tmp_path / "made").mkdir()
        (tmp_path / "made" / "file").write_text("x")
    assert [event for event, _ in writes] == ["os.mkdir", "open"]
