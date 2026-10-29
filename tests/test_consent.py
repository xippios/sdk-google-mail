"""``xippios-google-mail-auth`` offline: the PKCE pair, the URL, one consent end to end.

No consent is run (a consent is the operator's step): the browser is a plain ``urllib``
request to the loopback listener, and the token endpoint a ``respx`` route whose answer
is written here, shaped as ``docs/upstream/pages/oauth2-native-app.md``'s exchange answer.
"""

import base64
import hashlib
import secrets
import socket
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from contextlib import AbstractContextManager
from urllib.parse import parse_qsl, urlsplit

import httpx
import pytest
import respx

from xippios.google.mail import _consent
from xippios.google.mail._auth import CLIENT_ID_ENV, CLIENT_SECRET_ENV, SCOPE_SETS, TOKEN_URL

STATE = "fixed-state-for-the-test"
REFRESH = "test-refresh-token-value"  # pragma: allowlist secret (a made-up value)
READ_ANSWER = {
    "access_token": "test-access-token-value",  # pragma: allowlist secret
    "expires_in": 3599,
    "refresh_token": REFRESH,
    "refresh_token_expires_in": 604799,
    "scope": SCOPE_SETS["read"][0],
    "token_type": "Bearer",
}


def test_help_needs_no_environment_and_no_network(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv(CLIENT_ID_ENV)
    monkeypatch.delenv(CLIENT_SECRET_ENV)
    with pytest.raises(SystemExit) as info:
        _consent.main(["--help"])
    assert info.value.code == 0
    out = capsys.readouterr().out
    for option in ("--account", "--scopes", "--port", "{read,write}"):
        assert option in out


def test_scopes_default_to_the_read_set() -> None:
    """The re-run a revoked token's message prints, ``--account <name>`` alone, consents read."""
    assert _consent._parser().parse_args(["--account", "work"]).scopes == "read"
    assert _consent._parser().parse_args(["--account", "w", "--scopes", "write"]).scopes == "write"


@pytest.mark.parametrize("account", ["Work", "my-account", ""])
def test_an_account_name_the_registry_refuses_is_a_usage_error(account: str) -> None:
    with pytest.raises(SystemExit) as info:
        _consent.main(["--account", account, "--scopes", "read"])
    assert info.value.code == 2


def test_without_the_client_pair_nothing_listens(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv(CLIENT_SECRET_ENV)
    assert _consent.main(["--account", "work", "--scopes", "read"]) == 2
    err = capsys.readouterr().err
    assert CLIENT_ID_ENV in err
    assert CLIENT_SECRET_ENV in err
    assert "listening" not in err


def test_the_pkce_pair_is_s256() -> None:
    verifier, challenge = _consent.pkce_pair()
    assert len(verifier) == len(challenge) == 43
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    assert challenge == base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    assert _consent.pkce_pair()[0] != verifier


def test_the_authorization_url_asks_for_an_offline_consent() -> None:
    url = _consent.authorization_url(
        "client", "http://127.0.0.1:1234/", SCOPE_SETS["write"], "challenge", STATE
    )
    parts = urlsplit(url)
    assert f"{parts.scheme}://{parts.netloc}{parts.path}" == _consent.AUTH_URL
    assert dict(parse_qsl(parts.query)) == {
        "client_id": "client",
        "redirect_uri": "http://127.0.0.1:1234/",
        "response_type": "code",
        "scope": "https://www.googleapis.com/auth/gmail.modify",
        "code_challenge": "challenge",
        "code_challenge_method": "S256",
        "state": STATE,
        "access_type": "offline",
        "prompt": "consent",
    }


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port: int = probe.getsockname()[1]
        return port


def _browse(url: str) -> int:
    """What the browser gets: the HTTP status of a GET to the loopback listener."""
    try:
        with urllib.request.urlopen(url, timeout=5) as answer:
            status: int = answer.status
            return status
    except urllib.error.HTTPError as refused:
        return refused.code


def _consent_in_a_thread(argv: list[str]) -> tuple[threading.Thread, list[int]]:
    codes: list[int] = []
    thread = threading.Thread(target=lambda: codes.append(_consent.main(argv)), daemon=True)
    thread.start()
    return thread, codes


def _wait_for_listener(port: int) -> None:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        with socket.socket() as probe:
            if probe.connect_ex(("127.0.0.1", port)) == 0:
                return
        time.sleep(0.02)
    raise AssertionError("the listener did not start")


def test_one_consent_prints_the_refresh_token_once_and_stores_nothing(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    respx_mock: respx.MockRouter,
    no_writes: Callable[[], AbstractContextManager[list[tuple[str, str]]]],
) -> None:
    """The listener, the redirect and the code exchange attempt no file write anywhere."""
    monkeypatch.setattr(secrets, "token_urlsafe", lambda _: STATE)
    token = respx_mock.post(TOKEN_URL).respond(200, json=READ_ANSWER)
    port = _free_port()
    with no_writes() as writes:
        thread, codes = _consent_in_a_thread(["--account", "work", "--port", str(port)])
        _wait_for_listener(port)
        assert _browse(f"http://127.0.0.1:{port}/favicon.ico") == 404, "another path is ignored"
        assert _browse(f"http://127.0.0.1:{port}/?state={STATE}&code=the-code&scope=x") == 200
        thread.join(timeout=10)
    assert writes == []
    assert codes == [0]
    out, err = capsys.readouterr()
    assert out == f"{REFRESH}\n", "the token alone on stdout, once"
    assert REFRESH not in err
    assert "set GOOGLE_WORK_REFRESH_TOKEN to it, and list work in GOOGLE_ACCOUNTS" in err
    assert "expires in 604799 s (7.0 days)" in err
    sent = dict(parse_qsl(token.calls.last.request.content.decode()))
    assert sent["grant_type"] == "authorization_code"
    assert sent["code"] == "the-code"
    assert sent["redirect_uri"] == f"http://127.0.0.1:{port}/"
    assert len(sent["code_verifier"]) == 43
    assert sorted(sent) == [
        "client_id",
        "client_secret",
        "code",
        "code_verifier",
        "grant_type",
        "redirect_uri",
    ]


def test_a_redirect_with_another_state_is_refused(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    respx_mock: respx.MockRouter,
) -> None:
    monkeypatch.setattr(secrets, "token_urlsafe", lambda _: STATE)
    token = respx_mock.post(TOKEN_URL).respond(200, json=READ_ANSWER)
    port = _free_port()
    thread, codes = _consent_in_a_thread(
        ["--account", "demo", "--scopes", "read", "--port", str(port)]
    )
    _wait_for_listener(port)
    assert _browse(f"http://127.0.0.1:{port}/?state=forged&code=the-code") == 400
    thread.join(timeout=10)
    assert codes == [1]
    assert capsys.readouterr().out == ""
    assert not token.calls


def _answer(status: int, payload: Mapping[str, object]) -> httpx.Response:
    return httpx.Response(status, json=payload)


def test_a_grant_without_the_asked_scope_prints_no_token(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert _consent._report(_answer(200, READ_ANSWER), "demo", "write") == 1
    out, err = capsys.readouterr()
    assert out == ""
    assert "granted gmail.readonly, not gmail.modify" in err


def test_a_refused_exchange_prints_the_error_code_only(
    capsys: pytest.CaptureFixture[str],
) -> None:
    refusal = {"error": "invalid_grant", "error_description": "Bad Request"}
    assert _consent._report(_answer(400, refusal), "demo", "read") == 1
    out, err = capsys.readouterr()
    assert out == ""
    assert "HTTP 400: error=invalid_grant description=Bad Request" in err


def test_demo_is_declared_by_its_token_alone(capsys: pytest.CaptureFixture[str]) -> None:
    assert _consent._report(_answer(200, READ_ANSWER), "demo", "read") == 0
    out, err = capsys.readouterr()
    assert out == f"{REFRESH}\n"
    assert "set GOOGLE_DEMO_REFRESH_TOKEN to it" in err
    assert "GOOGLE_ACCOUNTS" not in err


def test_an_answer_without_a_refresh_token_prints_none(
    capsys: pytest.CaptureFixture[str],
) -> None:
    answer = {key: value for key, value in READ_ANSWER.items() if key != "refresh_token"}
    assert _consent._report(_answer(200, answer), "demo", "read") == 1
    assert capsys.readouterr().out == ""
