"""The recorder's own scrub step and its last check, offline, on made-up exchanges.

``scripts/record_fixture.py`` is no package module, so it is loaded from its path. The
values below are made up in the shapes Google's credentials take (``1//``, ``GOCSPX-``,
``ya29.``); none is a credential. The messages are made up in memory in the shape of the
discovery document's ``Message`` (``format=full``): they test the scrubber, not a fixture.
"""

import base64
import importlib.util
import json
import os
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import httpx
import pytest
import respx
from xippios.core import InvalidRequestError
from xippios.core.testing import REDACTED, Fixture, ReplayFixture, Scrub, fixture_from_response

from xippios.google.mail._client import DEMO, TENANTS

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
        ["get_message", "ok"],
        ["get_message", "ok", "message_id=a", "message_id=b"],
        ["search_messages", "ok", "q=x", "max_results=two"],
        ["search_messages", "ok", "q=x", "include_html=true"],
        ["get_thread", "ok", "thread_id=t", "start=-1"],
        ["get_message", "ok", "message_id=m", "include_html=yes"],
        ["list_labels", "ok", "account=work"],
        ["list_labels", "ok", "--garbage-bearer"],
    ],
)
def test_the_command_line_refuses_what_it_does_not_record(
    recorder: ModuleType, argv: list[str]
) -> None:
    with pytest.raises(SystemExit) as info:
        recorder.parse_args(argv)
    assert info.value.code == 2


# The demo-only rule -------------------------------------------------------------------


@pytest.mark.parametrize(
    "argv",
    [
        ["get_profile", "ok", "--account", "work"],
        ["get_profile", "ok", "--account=work"],
        ["get_profile", "ok", "account=work"],
        ["oauth2_token", "ok", "--account", "work"],
    ],
)
def test_no_account_but_demo_can_be_named(
    recorder: ModuleType, respx_mock: respx.MockRouter, argv: list[str]
) -> None:
    with pytest.raises(SystemExit) as info:
        recorder.main(argv)
    assert info.value.code == 2
    assert not respx_mock.calls, "refused before any request"


def test_the_recorder_reaches_demo_alone_whatever_is_declared(
    recorder: ModuleType,
    replay_fixture: ReplayFixture,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """``GOOGLE_ACCOUNTS`` is dropped, and every client and auth is demo's."""
    monkeypatch.setenv(TENANTS.list_var, "work")
    monkeypatch.setenv(TENANTS.var("work", "REFRESH_TOKEN"), "work-token")
    monkeypatch.setattr(recorder, "ROOT", tmp_path)
    monkeypatch.setattr(recorder, "FIXTURES", tmp_path / "fixtures")
    accounts: list[str] = []
    for name in ("client_for", "refresh_auth"):
        original = getattr(recorder, name)

        def spy(account: str, *args: Any, _original: Any = original, **kwargs: Any) -> Any:
            accounts.append(account)
            return _original(account, *args, **kwargs)

        monkeypatch.setattr(recorder, name, spy)
    token = replay_fixture("oauth2_token", "ok")
    profile = replay_fixture("get_profile", "ok")
    assert recorder.main(["get_profile", "ok"]) == 0
    assert TENANTS.list_var not in os.environ
    assert set(accounts) == {DEMO}
    assert (token.call_count, profile.call_count) == (1, 1)
    assert (tmp_path / "fixtures" / "get_profile" / "ok.json").is_file()


def test_without_demo_the_recorder_sends_nothing(
    recorder: ModuleType,
    respx_mock: respx.MockRouter,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.delenv(TENANTS.var(DEMO, "REFRESH_TOKEN"))
    monkeypatch.setenv(TENANTS.list_var, "work")
    monkeypatch.setenv(TENANTS.var("work", "REFRESH_TOKEN"), "work-token")
    monkeypatch.setattr(recorder, "FIXTURES", tmp_path / "fixtures")
    with pytest.raises(InvalidRequestError, match=r"^unknown tenant 'demo'"):
        recorder.main(["get_profile", "ok"])
    assert not respx_mock.calls
    assert not (tmp_path / "fixtures").exists()


# Message scrub and task recordings ----------------------------------------------------


def test_a_task_s_arguments_are_typed_by_its_signature(recorder: ModuleType) -> None:
    args = recorder.parse_args(
        ["list_messages", "label", "label_ids=INBOX", "label_ids=UNREAD", "max_results=2"]
    )
    assert args.task_arguments == {"label_ids": ["INBOX", "UNREAD"], "max_results": 2}
    args = recorder.parse_args(["get_message", "ok", "message_id=abc", "include_html=true"])
    assert args.task_arguments == {"message_id": "abc", "include_html": True}
    assert recorder.parse_args(["list_labels", "ok"]).task_arguments == {}


@pytest.mark.parametrize(
    ("url", "units"),
    [
        ("profile", 1),
        ("labels", 1),
        ("messages?maxResults=2", 5),
        ("messages/abc?format=metadata&metadataHeaders=From", 20),
        ("messages/abc?format=full", 60),
        ("messages/abc", 60),
        ("threads/abc?format=full", 120),
        ("threads/abc?format=minimal", 40),
        ("messages/abc/attachments/att-1", 20),
        ("drafts", None),
    ],
)
def test_the_units_of_a_call_follow_the_quota_page(
    recorder: ModuleType, url: str, units: int | None
) -> None:
    assert recorder.units_of(f"https://gmail.googleapis.com/gmail/v1/users/me/{url}") == units


def b64url(text: str, charset: str = "utf-8") -> str:
    """Base64url without padding, as the made-up bodies below are encoded."""
    return base64.urlsafe_b64encode(text.encode(charset)).decode().rstrip("=")


def unb64url(data: str) -> bytes:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


ATTACHMENT = "ANGjdJ8made-up-attachment-id_0123456789"  # pragma: allowlist secret


def made_up_message(**overrides: Any) -> dict[str, Any]:
    """A ``format=full`` message as upstream shapes it, with everything §2.7 scrubs."""
    plain = "Hi,\r\nwrite to bob@corp.example.org or <bob@corp.example.org>.\r\n"
    html = '<p>Hi, <a href="mailto:bob@corp.example.org">Bob</a></p>'
    message = {
        "id": "18f0000000000007",
        "threadId": "18f0000000000007",
        "labelIds": ["INBOX", "UNREAD"],
        "snippet": "Hi, write to bob@corp.example.org",
        "historyId": "1353",
        "internalDate": "1761746531000",
        "sizeEstimate": 5120,
        "raw": b64url("From: alice@corp.example.org"),
        "payload": {
            "partId": "",
            "mimeType": "multipart/mixed",
            "filename": "",
            "headers": [
                {"name": "Received", "value": "from mx.corp.example.org (203.0.113.7)"},
                {"name": "DKIM-Signature", "value": "v=1; a=rsa-sha256; d=corp.example.org"},
                {"name": "ARC-Seal", "value": "i=1; a=rsa-sha256; cv=none"},
                {"name": "X-Google-Smtp-Source", "value": "AGHT+made-up"},
                {"name": "MIME-Version", "value": "1.0"},
                {"name": "From", "value": "Alice Example <alice@corp.example.org>"},
                {"name": "Date", "value": "Thu, 29 Oct 2026 14:02:11 +0100"},
                {"name": "Message-Id", "value": "<CAmade-up-1@mail.gmail.com>"},
                {"name": "References", "value": "<parent-0@corp.example.org> <p-1@x.example>"},
                {"name": "In-Reply-To", "value": "<p-1@x.example>"},
                {"name": "Subject", "value": "Lunch"},
                {"name": "To", "value": "demo.inbox@gmail.com"},
                {"name": "Content-Type", "value": 'multipart/mixed; boundary="000abc"'},
            ],
            "body": {"size": 0},
            "parts": [
                {
                    "partId": "0",
                    "mimeType": "multipart/alternative",
                    "filename": "",
                    "headers": [{"name": "Content-Type", "value": "multipart/alternative"}],
                    "body": {"size": 0},
                    "parts": [
                        {
                            "partId": "0.0",
                            "mimeType": "text/plain",
                            "filename": "",
                            "headers": [
                                {"name": "Content-Type", "value": 'text/plain; charset="UTF-8"'},
                                {"name": "Content-Transfer-Encoding", "value": "quoted-printable"},
                            ],
                            "body": {"size": len(plain), "data": b64url(plain)},
                        },
                        {
                            "partId": "0.1",
                            "mimeType": "text/html",
                            "filename": "",
                            "headers": [{"name": "Content-Type", "value": "text/html"}],
                            "body": {"size": len(html), "data": b64url(html)},
                        },
                    ],
                },
                {
                    "partId": "1",
                    "mimeType": "application/pdf",
                    "filename": "menu.pdf",
                    "headers": [
                        {"name": "Content-Type", "value": 'application/pdf; name="menu.pdf"'},
                        {"name": "Content-Disposition", "value": "attachment"},
                    ],
                    "body": {"size": 2048, "attachmentId": ATTACHMENT},
                },
            ],
        },
    }
    message.update(overrides)
    return message


MESSAGE_URL = "https://gmail.googleapis.com/gmail/v1/users/me/messages/18f0000000000007"


def message_exchange(body: Any, url: str = MESSAGE_URL) -> httpx.Response:
    return httpx.Response(200, json=body, request=httpx.Request("GET", url))


def parts_of(part: dict[str, Any]) -> list[dict[str, Any]]:
    return [part, *(item for child in part.get("parts", []) for item in parts_of(child))]


def test_a_message_keeps_the_allowlisted_headers_and_numbered_ids(recorder: ModuleType) -> None:
    fixture = scrubbed(recorder, message_exchange(made_up_message()))
    payload = json.loads(fixture.response.body)["payload"]
    assert [h["name"] for h in payload["headers"]] == [
        "MIME-Version",
        "From",
        "Date",
        "Message-Id",
        "References",
        "In-Reply-To",
        "Subject",
        "To",
        "Content-Type",
    ]
    values = {h["name"]: h["value"] for h in payload["headers"]}
    assert values["Message-Id"] == "<1@example.com>"
    assert values["References"] == "<2@example.com> <3@example.com>"
    assert values["In-Reply-To"] == "<3@example.com>", "the same id, the same number"
    assert values["From"] == "Alice Example <user@example.com>"
    assert values["To"] == "user@example.com"
    plain_headers = parts_of(payload)[2]["headers"]
    assert [h["name"] for h in plain_headers] == ["Content-Type"], "every part's headers"


def test_a_message_s_bodies_are_decoded_scrubbed_and_encoded_again(recorder: ModuleType) -> None:
    original = made_up_message()
    fixture = scrubbed(recorder, message_exchange(original))
    message = json.loads(fixture.response.body)
    plain, html = parts_of(message["payload"])[2:4]
    text = unb64url(plain["body"]["data"]).decode()
    assert text == "Hi,\r\nwrite to user@example.com or <user@example.com>.\r\n"
    assert "mailto:user@example.com" in unb64url(html["body"]["data"]).decode()
    sizes = [part["body"]["size"] for part in parts_of(original["payload"])]
    assert [part["body"]["size"] for part in parts_of(message["payload"])] == sizes
    assert "=" not in plain["body"]["data"], "upstream's padding kept: none"
    assert message["snippet"] == "Hi, write to user@example.com"
    assert "raw" not in message
    assert parts_of(message["payload"])[4]["body"]["attachmentId"] == "att-1"
    assert (message["id"], message["threadId"]) == (original["id"], original["threadId"])
    assert recorder.check(recorder.to_json(fixture), fixture, SECRETS) == []


def test_bytes_in_another_charset_survive_the_body_scrub(recorder: ModuleType) -> None:
    latin = "Grüße an bob@corp.example.org"
    message = made_up_message(
        payload={
            "partId": "",
            "mimeType": "text/plain",
            "filename": "",
            "headers": [{"name": "Content-Type", "value": "text/plain; charset=iso-8859-1"}],
            "body": {"size": 29, "data": b64url(latin, "iso-8859-1") + "=="},
        }
    )
    fixture = scrubbed(recorder, message_exchange(message))
    data = json.loads(fixture.response.body)["payload"]["body"]["data"]
    assert unb64url(data) == "Grüße an user@example.com".encode("iso-8859-1")
    assert data.endswith("="), "upstream's padding kept"


def test_a_thread_numbers_one_id_once_across_its_messages(recorder: ModuleType) -> None:
    first = made_up_message()
    reply = made_up_message(id="18f0000000000008")
    reply["payload"] = {
        **reply["payload"],
        "headers": [
            {"name": "Message-ID", "value": "<CAmade-up-2@mail.gmail.com>"},
            {"name": "In-Reply-To", "value": "<CAmade-up-1@mail.gmail.com>"},
        ],
    }
    url = "https://gmail.googleapis.com/gmail/v1/users/me/threads/18f0000000000007"
    thread = {"id": first["threadId"], "historyId": "1353", "messages": [first, reply]}
    fixture = scrubbed(recorder, message_exchange(thread, url))
    messages = json.loads(fixture.response.body)["messages"]
    headers = [{h["name"]: h["value"] for h in m["payload"]["headers"]} for m in messages]
    assert headers[0]["Message-Id"] == "<1@example.com>"
    assert headers[1] == {"Message-ID": "<4@example.com>", "In-Reply-To": "<1@example.com>"}
    assert [m["payload"]["parts"][1]["body"]["attachmentId"] for m in messages[:1]] == ["att-1"]


def test_an_attachment_id_is_numbered_in_the_path_and_the_body_alike(
    recorder: ModuleType,
) -> None:
    url = f"{MESSAGE_URL}/attachments/{ATTACHMENT}"
    response = message_exchange({"attachmentId": ATTACHMENT, "size": 3, "data": b64url("abc")}, url)
    fixture = scrubbed(recorder, response)
    assert fixture.request.url == f"{MESSAGE_URL}/attachments/att-1"
    assert json.loads(fixture.response.body)["attachmentId"] == "att-1"
    assert recorder.check(recorder.to_json(fixture), fixture, SECRETS) == []


def test_an_unscrubbed_message_is_refused(recorder: ModuleType) -> None:
    raw = fixture_from_response(message_exchange(made_up_message()))
    leaks = recorder.check(recorder.to_json(raw), raw, SECRETS)
    assert leaks == [
        "an e-mail address other than user@example.com",
        "a <...@...> other than <n@example.com> and <user@example.com>",
        "a MIME header outside the allowlist (ARC-Seal, Content-Disposition, "
        "Content-Transfer-Encoding, DKIM-Signature, Received, X-Google-Smtp-Source)",
        "a raw field, a whole message",
        "an attachmentId that is not att-<n>",
    ]


def test_an_address_left_in_a_decoded_body_is_refused(recorder: ModuleType) -> None:
    """The check decodes every ``body.data``: an address there is invisible as base64url."""
    fixture = scrubbed(recorder, message_exchange(made_up_message()))
    message = json.loads(fixture.response.body)
    message["payload"]["parts"][0]["parts"][0]["body"]["data"] = b64url("to bob@x.example")
    left = fixture.model_copy(
        update={"response": fixture.response.model_copy(update={"body": json.dumps(message)})}
    )
    assert "bob@x.example" not in recorder.to_json(left)
    leaks = recorder.check(recorder.to_json(left), left, SECRETS)
    assert leaks == ["an e-mail address other than user@example.com"]


def test_an_answer_the_message_step_leaves_alone_keeps_upstream_s_text(
    recorder: ModuleType,
) -> None:
    text = '{\n  "labels": [\n    {\n      "id": "INBOX"\n    }\n  ]\n}\n'
    assert recorder.MessageScrub().body(text) == text
