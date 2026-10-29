"""Replay tests of the read tasks on labels, messages and threads (conventions §10).

Every Gmail exchange replays the refresh first (``oauth2_token/ok``), then the task's own
fixtures, recorded through the tasks under demo's readonly grant on 2026-10-29. Values are
read from the fixtures, not pinned, so a re-recording breaks no assertion on content.
What was sent is compared through ``sent_query`` and ``sent_urls``, never a request kept
from ``route.calls``. The decoder's rules that the recorded mailbox does not show (an
attachment, a charset, a nested tree) are tested on messages built in memory in the
discovery document's shape: they test the decoder, not a fixture.
"""

import base64
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
from xippios.core import InvalidRequestError, ResultCap, ValidationError, sync
from xippios.core.mcp import cap_result
from xippios.core.testing import (
    Fixture,
    ReplayFixture,
    assert_projection,
    assert_time_between,
    fixture_path,
    load_fixture,
    mcp_call,
    mcp_call_text,
    recorded_query,
    sent_query,
    sent_urls,
)

from xippios.google.mail import api, tasks
from xippios.google.mail._client import DEMO, SOURCE, client_for
from xippios.google.mail._decode import HEADERS, decode_message
from xippios.google.mail.mcp import server
from xippios.google.mail.models import (
    DecodedMessage,
    Label,
    ListMessagesResponse,
    Message,
    MessagePartBody,
    SearchRow,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures"
BASE = "https://gmail.googleapis.com/gmail/v1/users/me"
KEPT = {name.lower() for name in HEADERS}


def recorded(endpoint: str, case: str) -> Fixture:
    return load_fixture(fixture_path(FIXTURES, endpoint, case))


def answer(endpoint: str, case: str) -> Any:
    """The recorded answer's JSON."""
    return json.loads(recorded(endpoint, case).response.body)


def text_of(data: str) -> str:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4)).decode("utf-8")


def part_text(message: dict[str, Any], mime_type: str) -> str:
    """The decoded body of the recorded message's first part of ``mime_type``."""
    stack = [message["payload"]]
    while stack:
        part = stack.pop(0)
        if part["mimeType"] == mime_type and not part.get("filename"):
            return text_of(part["body"]["data"])
        stack[:0] = part.get("parts", [])
    raise AssertionError(f"the recorded message has no {mime_type} part")


def assert_meta(result: Any, endpoint: str, request: dict[str, Any]) -> None:
    meta = result.meta
    assert (meta.source, meta.endpoint, meta.tenant) == (SOURCE, endpoint, DEMO)
    assert meta.request == {"account": DEMO, **request}
    assert (meta.published_at, meta.vintage, meta.total) == (None, None, None)


# list_labels -------------------------------------------------------------------------


def test_list_labels_returns_the_recorded_labels(replay_fixture: ReplayFixture) -> None:
    token = replay_fixture("oauth2_token", "ok")
    route = replay_fixture("list_labels", "ok")
    expected = answer("list_labels", "ok")["labels"]
    before = datetime.now(UTC)
    result = tasks.list_labels(DEMO)
    after = datetime.now(UTC)
    assert all(isinstance(label, Label) for label in result.data)
    assert [label.id for label in result.data] == [label["id"] for label in expected]
    assert [label.type for label in result.data] == [label["type"] for label in expected]
    inbox = next(label for label in result.data if label.id == "INBOX")
    assert inbox.messages_total is None, "labels.list sends no counts"
    assert_meta(result, "gmail.users.labels.list", {})
    assert_time_between(result.meta.retrieved_at, before, after)
    assert sent_urls(route) == [f"{BASE}/labels"]
    assert token.call_count == 1


@pytest.mark.anyio
async def test_list_labels_over_mcp(replay_fixture: ReplayFixture) -> None:
    replay_fixture("oauth2_token", "ok")
    replay_fixture("list_labels", "ok")
    payload = await mcp_call(server, "google_mail_list_labels", {"account": DEMO})
    assert len(payload["data"]) == len(answer("list_labels", "ok")["labels"])
    assert "note" not in payload, "the labels fit the default cap"
    assert {"id", "name", "type", "message_list_visibility"} <= set(payload["data"][0])


# list_messages -----------------------------------------------------------------------


def test_list_messages_returns_upstream_s_page_of_ids(replay_fixture: ReplayFixture) -> None:
    replay_fixture("oauth2_token", "ok")
    route = replay_fixture("list_messages", "ok", match_params=["maxResults"])
    expected = answer("list_messages", "ok")
    result = tasks.list_messages(DEMO, max_results=2)
    assert isinstance(result.data, ListMessagesResponse)
    assert [(m.id, m.thread_id) for m in result.data.messages] == [
        (m["id"], m["threadId"]) for m in expected["messages"]
    ]
    assert result.data.next_page_token == expected["nextPageToken"]
    assert result.data.result_size_estimate == expected["resultSizeEstimate"]
    assert result.meta.total is None, "an estimate is no count (ADR-0012)"
    assert_meta(result, "gmail.users.messages.list", {"maxResults": 2})
    assert sent_query(route) == recorded_query(recorded("list_messages", "ok"))
    assert sent_query(route) == {"maxResults": "2"}


def test_list_messages_sends_q_verbatim(replay_fixture: ReplayFixture) -> None:
    replay_fixture("oauth2_token", "ok")
    route = replay_fixture("list_messages", "query", match_params=["q"])
    result = tasks.list_messages(DEMO, q="newer_than:1y")
    assert sent_query(route) == {"q": "newer_than:1y"}
    assert sent_query(route) == recorded_query(recorded("list_messages", "query"))
    assert len(result.data.messages) == len(answer("list_messages", "query")["messages"])
    assert result.data.next_page_token is None, "the whole mailbox fits one page"
    assert result.meta.request == {"account": DEMO, "q": "newer_than:1y"}


def test_list_messages_repeats_label_ids(replay_fixture: ReplayFixture) -> None:
    replay_fixture("oauth2_token", "ok")
    route = replay_fixture("list_messages", "label", match_params=["labelIds"])
    result = tasks.list_messages(DEMO, label_ids=["INBOX"])
    assert sent_query(route) == recorded_query(recorded("list_messages", "label"))
    assert result.meta.request == {"account": DEMO, "labelIds": ["INBOX"]}
    two = replay_fixture("list_messages", "label")  # the path alone: the answer stands in
    tasks.list_messages(DEMO, label_ids=["INBOX", "UNREAD"])
    assert httpx.URL(sent_urls(two)[-1]).params.get_list("labelIds") == ["INBOX", "UNREAD"]


def test_list_messages_sends_include_spam_trash_only_when_true(
    replay_fixture: ReplayFixture,
) -> None:
    replay_fixture("oauth2_token", "ok")
    route = replay_fixture("list_messages", "ok")
    result = tasks.list_messages(DEMO, include_spam_trash=True, page_token="0123")
    assert sent_query(route) == {"includeSpamTrash": "true", "pageToken": "0123"}
    assert result.meta.request == {"account": DEMO, "includeSpamTrash": True, "pageToken": "0123"}
    tasks.list_messages(DEMO)
    assert sent_query(route) == {}


@pytest.mark.anyio
async def test_list_messages_over_mcp(replay_fixture: ReplayFixture) -> None:
    replay_fixture("oauth2_token", "ok")
    replay_fixture("list_messages", "ok")
    payload = await mcp_call(
        server, "google_mail_list_messages", {"account": DEMO, "max_results": 2}
    )
    expected = answer("list_messages", "ok")
    assert payload["data"]["result_size_estimate"] == expected["resultSizeEstimate"]
    assert [m["id"] for m in payload["data"]["messages"]] == [m["id"] for m in expected["messages"]]
    assert set(payload["data"]["messages"][0]) == {"id", "thread_id"}
    assert payload["meta"]["total"] is None


# search_messages ---------------------------------------------------------------------


def mount_search(replay_fixture: ReplayFixture) -> tuple[respx.Route, list[respx.Route]]:
    """The search's three recorded exchanges: the list, then one metadata call per id."""
    replay_fixture("oauth2_token", "ok")
    listed = replay_fixture("search_messages", "ok", match_params=["q"])
    gets = [replay_fixture("search_messages", case) for case in ("ok_2", "ok_3")]
    return listed, gets


def headers_of(message: dict[str, Any]) -> dict[str, str]:
    return {header["name"]: header["value"] for header in message["payload"]["headers"]}


def test_search_messages_shows_each_match_s_headers(replay_fixture: ReplayFixture) -> None:
    listed, gets = mount_search(replay_fixture)
    result = tasks.search_messages(DEMO, "newer_than:1y", max_results=2)
    expected = [answer("search_messages", case) for case in ("ok_2", "ok_3")]
    assert all(isinstance(row, SearchRow) for row in result.data)
    assert [row.id for row in result.data] == [
        m["id"] for m in answer("search_messages", "ok")["messages"]
    ]
    for row, message in zip(result.data, expected, strict=True):
        headers = headers_of(message)
        assert (row.from_, row.subject, row.date) == (
            headers["From"],
            headers["Subject"],
            headers["Date"],
        )
        assert (row.thread_id, row.snippet) == (message["threadId"], message["snippet"])
    assert_meta(
        result,
        "gmail.users.messages.list",
        {
            "q": "newer_than:1y",
            "maxResults": 2,
            "format": "metadata",
            "metadataHeaders": ["From", "Subject", "Date"],
        },
    )
    assert sent_query(listed) == {"q": "newer_than:1y", "maxResults": "2"}
    assert sent_query(listed) == recorded_query(recorded("search_messages", "ok"))
    for route, case in zip(gets, ("ok_2", "ok_3"), strict=True):
        assert route.call_count == 1
        sent = httpx.URL(sent_urls(route)[0]).params
        assert sent.get_list("format") == ["metadata"]
        assert sent.get_list("metadataHeaders") == ["From", "Subject", "Date"]
        assert sent_urls(route) == [recorded(case=case, endpoint="search_messages").request.url]


@pytest.mark.parametrize("max_results", [0, 51, 500])
def test_search_messages_reads_at_most_50_messages(
    respx_mock: respx.MockRouter, max_results: int
) -> None:
    with pytest.raises(InvalidRequestError, match=rf"^max_results is {max_results}; search_"):
        tasks.search_messages(DEMO, "newer_than:1y", max_results=max_results)
    assert not respx_mock.calls, "refused before any request, the token's included"


@pytest.mark.anyio
async def test_search_messages_over_mcp(replay_fixture: ReplayFixture) -> None:
    mount_search(replay_fixture)
    payload = await mcp_call(
        server,
        "google_mail_search_messages",
        {"account": DEMO, "q": "newer_than:1y", "max_results": 2},
    )
    assert [set(row) for row in payload["data"]] == [
        {"id", "thread_id", "from_", "subject", "date", "snippet"}
    ] * 2
    assert payload["data"][0]["from_"] == "Google <user@example.com>"


@pytest.mark.anyio
async def test_the_bound_holds_over_mcp(respx_mock: respx.MockRouter) -> None:
    text = await mcp_call_text(
        server, "google_mail_search_messages", {"account": DEMO, "q": "x", "max_results": 51}
    )
    assert text.startswith("error: InvalidRequestError: max_results is 51; search_messages")
    assert not respx_mock.calls


# get_message -------------------------------------------------------------------------


def test_get_message_returns_the_decoded_view(replay_fixture: ReplayFixture) -> None:
    replay_fixture("oauth2_token", "ok")
    route = replay_fixture("get_message", "ok")
    message = answer("get_message", "ok")
    result = tasks.get_message(DEMO, message["id"])
    view = result.data
    assert isinstance(view, DecodedMessage)
    assert_projection(
        view,
        Message.model_validate(message),
        headers=view.headers,
        text=view.text,
        html=None,
        attachments=[],
    )
    assert view.internal_date == message["internalDate"], "epoch milliseconds as served"
    kept = [h for h in message["payload"]["headers"] if h["name"].lower() in KEPT]
    assert [(h.name, h.value) for h in view.headers] == [(h["name"], h["value"]) for h in kept]
    names = {h["name"] for h in message["payload"]["headers"]}
    assert {"Content-Type", "MIME-Version"} <= names, "recorded, and left out of the view"
    assert {h.name for h in view.headers} == names - {"Content-Type", "MIME-Version"}
    assert view.text == part_text(message, "text/plain")
    assert view.html is None, "html only on request"
    assert view.attachments == [], "the recorded message carries none"
    assert_meta(
        result,
        "gmail.users.messages.get",
        {"id": message["id"], "format": "full", "include_html": False},
    )
    assert sent_query(route) == {"format": "full"}
    assert sent_urls(route) == [recorded("get_message", "ok").request.url]


def test_get_message_decodes_the_html_on_request(replay_fixture: ReplayFixture) -> None:
    replay_fixture("oauth2_token", "ok")
    replay_fixture("get_message", "ok")
    message = answer("get_message", "ok")
    result = tasks.get_message(DEMO, message["id"], include_html=True)
    assert result.data.html == part_text(message, "text/html")
    assert result.data.text == part_text(message, "text/plain")
    assert result.meta.request["include_html"] is True


@pytest.mark.anyio
async def test_get_message_over_mcp(replay_fixture: ReplayFixture) -> None:
    replay_fixture("oauth2_token", "ok")
    replay_fixture("get_message", "ok")
    message = answer("get_message", "ok")
    arguments = {"account": DEMO, "message_id": message["id"]}
    payload = await mcp_call(server, "google_mail_get_message", arguments)
    assert payload["data"]["text"] == part_text(message, "text/plain")
    assert payload["data"]["html"] is None
    assert sorted(payload["data"]) == sorted(DecodedMessage.model_fields)
    with_html = await mcp_call(
        server, "google_mail_get_message", {**arguments, "include_html": True}
    )
    assert with_html["data"]["html"] == part_text(message, "text/html")


# get_thread --------------------------------------------------------------------------


def test_get_thread_returns_each_message_s_view_as_text(replay_fixture: ReplayFixture) -> None:
    replay_fixture("oauth2_token", "ok")
    route = replay_fixture("get_thread", "ok")
    thread = answer("get_thread", "ok")
    result = tasks.get_thread(DEMO, thread["id"])
    assert [view.id for view in result.data] == [m["id"] for m in thread["messages"]]
    for view, message in zip(result.data, thread["messages"], strict=True):
        assert view == decode_message(Message.model_validate(message))
        assert view.text == part_text(message, "text/plain")
        assert view.html is None, "a thread's views carry text only"
    assert_meta(
        result, "gmail.users.threads.get", {"id": thread["id"], "format": "full", "start": 0}
    )
    assert sent_query(route) == {"format": "full"}
    assert sent_urls(route) == [recorded("get_thread", "ok").request.url]


def test_get_thread_shows_the_slice_start_and_limit_name(replay_fixture: ReplayFixture) -> None:
    replay_fixture("oauth2_token", "ok")
    replay_fixture("get_thread", "ok")
    thread = answer("get_thread", "ok")
    count = len(thread["messages"])
    first = tasks.get_thread(DEMO, thread["id"], start=0, limit=1)
    assert [view.id for view in first.data] == [thread["messages"][0]["id"]]
    assert (first.meta.request["start"], first.meta.request["limit"]) == (0, 1)
    past = tasks.get_thread(DEMO, thread["id"], start=count)
    assert past.data == []
    assert past.meta.request["start"] == count
    assert "limit" not in past.meta.request, "an omitted limit is not recorded"


@pytest.mark.parametrize(("start", "limit"), [(-1, None), (0, 0), (2, -3)])
def test_get_thread_refuses_a_bad_slice_before_any_request(
    respx_mock: respx.MockRouter, start: int, limit: int | None
) -> None:
    with pytest.raises(InvalidRequestError, match=r"^(start|limit) is -?\d+;"):
        tasks.get_thread(DEMO, "19a0b0c0d0e0f001", start=start, limit=limit)
    assert not respx_mock.calls


@pytest.mark.parametrize(("start", "resume"), [(0, 2), (4, 6)])
def test_a_cut_thread_says_where_to_resume(
    replay_fixture: ReplayFixture, start: int, resume: int
) -> None:
    """Core's cap reads the integer ``start`` of ``meta.request`` (conventions §8).

    The recorded thread holds one message, so the result is made three times as long in
    memory and cut at two rows; the note is the tool's own, from ``MCP_CAPS``.
    """
    replay_fixture("oauth2_token", "ok")
    replay_fixture("get_thread", "ok")
    thread = answer("get_thread", "ok")
    result = tasks.get_thread(DEMO, thread["id"])
    long = result.model_copy(
        update={
            "data": result.data * 3,
            "meta": result.meta.model_copy(
                update={"request": {**result.meta.request, "start": start}}
            ),
        }
    )
    note = tasks.MCP_CAPS["get_thread"].note
    capped = json.loads(cap_result(long, ResultCap(max_rows=2, note=note)))
    assert len(capped["data"]) == 2
    assert capped["note"] == (
        f"showing 2 of 3 rows (cap: 2 rows or 65536 bytes); {note}; resume with start={resume}"
    )


@pytest.mark.anyio
async def test_get_thread_over_mcp(replay_fixture: ReplayFixture) -> None:
    replay_fixture("oauth2_token", "ok")
    replay_fixture("get_thread", "ok")
    thread = answer("get_thread", "ok")
    payload = await mcp_call(
        server, "google_mail_get_thread", {"account": DEMO, "thread_id": thread["id"]}
    )
    assert [view["id"] for view in payload["data"]] == [m["id"] for m in thread["messages"]]
    assert payload["meta"]["request"]["start"] == 0
    assert "note" not in payload, "one message fits the cap"


def test_mcp_caps_name_get_message_and_get_thread_alone() -> None:
    assert set(tasks.MCP_CAPS) == {"get_message", "get_thread"}
    for cap in tasks.MCP_CAPS.values():
        assert (cap.max_rows, cap.max_bytes) == (ResultCap().max_rows, ResultCap().max_bytes)
    assert "start and limit" in (tasks.MCP_CAPS["get_thread"].note or "")


def test_a_long_message_comes_back_as_a_preview_and_the_note(
    replay_fixture: ReplayFixture,
) -> None:
    """ADR-0025: over the cap a long text or html is a preview plus the note (ADR-0016).

    The recorded message is short, so its text is made 40 times as long in memory; the
    cap and its note are the tool's own, from ``MCP_CAPS``.
    """
    replay_fixture("oauth2_token", "ok")
    replay_fixture("get_message", "ok")
    result = tasks.get_message(DEMO, answer("get_message", "ok")["id"])
    long = result.model_copy(
        update={"data": result.data.model_copy(update={"text": (result.data.text or "") * 40})}
    )
    compact = json.dumps(long.model_dump(mode="json"), ensure_ascii=False, separators=(",", ":"))
    size = len(compact.encode())  # as core counts it: the compact JSON's UTF-8 bytes
    assert size > ResultCap().max_bytes
    capped = json.loads(cap_result(long, tasks.MCP_CAPS["get_message"]))
    assert set(capped) == {"note", "preview"}
    assert capped["note"] == (
        f"result is {size} bytes, over the cap of 65536; the preview is the first 64 KB of "
        "the result, so a long message's text or html is cut; call with include_html=False "
        "for the text alone, or read the message in a mail client; there is no start to "
        "resume with"
    )
    assert "resume with start" not in capped["note"]
    assert capped["preview"].startswith('{"data":{"id":')
    assert len(capped["preview"].encode()) <= ResultCap().max_bytes


# the decoder, on messages built in memory --------------------------------------------


def b64url(text: str, charset: str = "utf-8") -> str:
    return base64.urlsafe_b64encode(text.encode(charset)).decode().rstrip("=")


def made_up(payload: dict[str, Any]) -> Message:
    return Message.model_validate(
        {"id": "m1", "threadId": "t1", "labelIds": ["INBOX"], "snippet": "s", "payload": payload}
    )


def leaf(part_id: str, mime_type: str, data: str | None = None, **extra: Any) -> dict[str, Any]:
    body: dict[str, Any] = {"size": 0 if data is None else len(data)}
    if data is not None:
        body["data"] = data
    return {
        "partId": part_id,
        "mimeType": mime_type,
        "filename": "",
        "headers": [],
        "body": body,
        **extra,
    }


def test_the_decoder_walks_the_tree_by_role() -> None:
    attached_text = {
        **leaf("1", "text/plain"),
        "filename": "notes.txt",
        "body": {"size": 12, "attachmentId": "att-1"},
    }
    forwarded = {
        **leaf("2", "message/rfc822"),
        "filename": "fwd.eml",
        "body": {"size": 99, "attachmentId": "att-2"},
        "parts": [leaf("2.0", "text/plain", b64url("forwarded text"))],
    }
    message = made_up(
        {
            **leaf("", "multipart/mixed"),
            "headers": [
                {"name": "Received", "value": "from a host"},
                {"name": "subject", "value": "Lunch"},
                {"name": "From", "value": "Alice <alice@example.com>"},
            ],
            "parts": [
                attached_text,
                {
                    **leaf("0", "multipart/alternative"),
                    "parts": [
                        leaf(
                            "0.0",
                            "text/plain",
                            b64url("Grüße", "iso-8859-1") + "==",
                            headers=[
                                {
                                    "name": "Content-Type",
                                    "value": 'text/plain; charset="ISO-8859-1"',
                                }
                            ],
                        ),
                        leaf("0.1", "text/html", b64url("<p>Grüße</p>")),
                    ],
                },
                forwarded,
            ],
        }
    )
    view = decode_message(message, include_html=True)
    assert [(h.name, h.value) for h in view.headers] == [
        ("subject", "Lunch"),
        ("From", "Alice <alice@example.com>"),
    ], "upstream's names and order, compared without regard to case"
    assert view.text == "Grüße", "the first text/plain that is no attachment, in its charset"
    assert view.html == "<p>Grüße</p>"
    assert [
        (a.part_id, a.filename, a.mime_type, a.size, a.attachment_id) for a in view.attachments
    ] == [
        ("1", "notes.txt", "text/plain", 12, "att-1"),
        ("2", "fwd.eml", "message/rfc822", 99, "att-2"),
    ], "metadata only; an attached message is not descended into"
    assert decode_message(message).html is None


def test_the_decoder_s_edges() -> None:
    assert decode_message(made_up(leaf("", "text/html", b64url("<b>x</b>")))).text is None
    assert decode_message(made_up(leaf("", "text/plain"))).text == "", "an empty body"
    unknown = leaf(
        "",
        "text/plain",
        b64url("café"),
        headers=[{"name": "Content-Type", "value": "text/plain; charset=x-unknown"}],
    )
    assert decode_message(made_up(unknown)).text == "café", "an unknown charset reads as UTF-8"
    broken = leaf("", "text/plain", b64url("abc", "utf-16"))
    assert "�" in (decode_message(made_up(broken)).text or ""), "bytes UTF-8 cannot read"
    with pytest.raises(ValidationError, match=r"part '': body.data is not base64url"):
        decode_message(made_up(leaf("", "text/plain", "abcde")))
    no_payload = Message.model_validate({"id": "m1"})
    assert decode_message(no_payload).headers == []
    assert decode_message(no_payload).text is None


# messages_attachments_get, the wrapper without a task --------------------------------


@pytest.mark.anyio
async def test_messages_attachments_get_sends_both_ids_as_path_segments(
    replay_fixture: ReplayFixture, respx_mock: respx.MockRouter
) -> None:
    """A made-up answer in the shape of ``schemas.MessagePartBody``: no attachment exists."""
    replay_fixture("oauth2_token", "ok")
    url = f"{BASE}/messages/19a0b0c0d0e0f001/attachments/att%2F1"
    route = respx_mock.get(url).respond(200, json={"size": 3, "data": b64url("abc")})
    body = await api.messages_attachments_get(client_for(DEMO), "19a0b0c0d0e0f001", "att/1")
    assert isinstance(body, MessagePartBody)
    assert (body.size, text_of(body.data or "")) == (3, "abc")
    assert sent_urls(route) == [url]


def test_every_read_task_has_a_sync_twin() -> None:
    for name in ("list_labels", "list_messages", "search_messages", "get_message", "get_thread"):
        twin = getattr(tasks, f"{name}_async")
        assert getattr(tasks, name).async_fn is twin
        assert sync(twin).__name__ == name
