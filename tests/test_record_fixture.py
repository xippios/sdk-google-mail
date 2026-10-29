"""The recorder's own scrub step and its last check, offline, on made-up exchanges.

``scripts/record_fixture.py`` is no package module, so it is loaded from its path. The
values below are made up in the shapes Google's credentials take (``1//``, ``GOCSPX-``,
``ya29.``); none is a credential.
"""

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

import httpx
import pytest
from xippios.core.testing import REDACTED, Fixture, Scrub, fixture_from_response

ROOT = Path(__file__).resolve().parents[1]
REFRESH = "1//made-up/refresh+token"  # pragma: allowlist secret
CLIENT_ID = "123-made-up.apps.googleusercontent.com"
CLIENT_SECRET = "GOCSPX-made-up-secret"  # pragma: allowlist secret
ACCESS = "ya29.made-up-access-token"  # pragma: allowlist secret
SECRETS = [REFRESH, CLIENT_SECRET, CLIENT_ID, ACCESS]


@pytest.fixture(scope="module")
def recorder() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "record_fixture", ROOT / "scripts" / "record_fixture.py"
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("record_fixture", module)
    spec.loader.exec_module(module)
    return module


def refresh_exchange() -> httpx.Response:
    form = {
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "grant_type": "refresh_token",
        "refresh_token": REFRESH,
    }
    request = httpx.Request("POST", "https://oauth2.googleapis.com/token", data=form)
    answer = {"access_token": ACCESS, "expires_in": 3599, "scope": "s", "token_type": "Bearer"}
    return httpx.Response(200, json=answer, request=request)


def profile_exchange() -> httpx.Response:
    request = httpx.Request("GET", "https://gmail.googleapis.com/gmail/v1/users/me/profile")
    body = {"emailAddress": "someone.else@gmail.com", "messagesTotal": 3}
    return httpx.Response(200, json=body, request=request)


def scrubbed(recorder: ModuleType, response: httpx.Response) -> Fixture:
    fixture: Fixture = recorder.rewrite(
        fixture_from_response(response, scrub=Scrub(secrets=SECRETS)), SECRETS
    )
    return fixture


def test_the_refresh_is_written_without_a_credential(recorder: ModuleType) -> None:
    fixture = scrubbed(recorder, refresh_exchange())
    assert fixture.request.body == (
        "client_id=REDACTED&client_secret=REDACTED&grant_type=refresh_token&refresh_token=REDACTED"
    )
    assert json.loads(fixture.response.body)["access_token"] == REDACTED
    text = recorder.to_json(fixture)
    assert recorder.check(text, fixture, SECRETS) == []
    for shape in recorder.TOKEN_SHAPES:
        assert shape not in text


def test_core_alone_leaves_the_encoded_refresh_token_which_the_check_refuses(
    recorder: ModuleType,
) -> None:
    """A refresh token's ``/`` is ``%2F`` in a form body: core's plain scrub misses it."""
    fixture = fixture_from_response(refresh_exchange(), scrub=Scrub(secrets=SECRETS))
    leaks = recorder.check(recorder.to_json(fixture), fixture, SECRETS)
    assert "a value of the refresh token, the client pair or an answer's token" in leaks
    assert "a refresh token's shape (1//)" in leaks


def test_every_address_becomes_the_placeholder(recorder: ModuleType) -> None:
    fixture = scrubbed(recorder, profile_exchange())
    assert json.loads(fixture.response.body)["emailAddress"] == "user@example.com"
    assert recorder.check(recorder.to_json(fixture), fixture, SECRETS) == []
    raw = fixture_from_response(profile_exchange())
    leaks = recorder.check(recorder.to_json(raw), raw, SECRETS)
    assert leaks == ["an e-mail address other than user@example.com"]


def test_a_token_field_left_in_an_answer_is_refused(recorder: ModuleType) -> None:
    request = httpx.Request("POST", "https://oauth2.googleapis.com/token")
    response = httpx.Response(200, json={"id_token": "an-unknown-value"}, request=request)
    raw = fixture_from_response(response)
    assert recorder.check(recorder.to_json(raw), raw, []) == ["a value under id_token"]
    fixture = recorder.rewrite(raw, [])
    assert recorder.check(recorder.to_json(fixture), fixture, []) == []


def test_forms_of_a_secret_cover_the_encodings(recorder: ModuleType) -> None:
    assert set(recorder.forms_of("1//a+b")) == {"1//a+b", "1%2F%2Fa%2Bb", "1\\/\\/a+b"}


@pytest.mark.parametrize(
    "argv",
    [
        ["get_profile", "ok", "--garbage-token"],
        ["oauth2_token", "ok", "--garbage-bearer"],
        ["messages_modify", "refused"],
        ["messages_modify", "refused", "id=abc", "addLabelIds=X"],
        ["threads_get", "ok"],
        ["get_profile", "Bad-Case"],
    ],
)
def test_the_command_line_refuses_what_it_does_not_record(
    recorder: ModuleType, argv: list[str]
) -> None:
    with pytest.raises(SystemExit) as info:
        recorder.parse_args(argv)
    assert info.value.code == 2
