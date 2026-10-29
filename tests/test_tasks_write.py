"""The write tasks: previews send nothing, confirmed writes replay, the write server serves them.

The fixtures are recorded through the tasks with ``scripts/record_fixture.py`` under
demo's write grant (``gmail.modify``), in this order, so that everything written was
cleaned up again: ``create_draft/ok`` (a draft to demo itself, Subject ``xippios-live
draft``), ``delete_draft/ok`` (that draft), ``send_message/ok`` (a message to demo itself,
Subject ``xippios-live send``), ``create_draft/reply`` (a reply's preview, which reads
that message's metadata), ``trash_message/ok`` (that message) and ``modify_message/ok``
(``STARRED`` added and ``UNREAD`` removed in one call, on that message). Each replays
after ``oauth2_token/write``, the refresh under the write grant. A recorded request holds
no ``raw`` (the recorder drops it), so what a confirmed call sent is read from the route
and decoded, and compared with what the task was given.

The composer's, the previews' and the refusals' tests need no fixture: a preview sends
nothing, which ``respx`` shows by answering nothing; a message built in memory tests the
composer, not a fixture.
"""

import base64
import email
import email.policy
import importlib.util
import inspect
import json
import re
import sys
import typing
from collections.abc import Callable
from email.message import EmailMessage
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
import respx
from fastapi import APIRouter
from mcp.server.mcpserver import MCPServer
from xippios.core import InvalidRequestError, Result
from xippios.core.http import router_from_tasks
from xippios.core.mcp import build_server, tool_name
from xippios.core.mcp.server import is_write_server, register_tasks
from xippios.core.testing import Fixture, ReplayFixture, fixture_path, load_fixture, mcp_call

from xippios.google.mail import tasks, tasks_write
from xippios.google.mail._client import DEMO, SOURCE
from xippios.google.mail._compose import (
    POLICY,
    PREVIEW_LINES,
    REPLY_HEADERS,
    Reply,
    addresses,
    compose,
    encode_raw,
    preview_headers,
)
from xippios.google.mail.http import router
from xippios.google.mail.mcp import server as read_server
from xippios.google.mail.mcp_write import server as write_server
from xippios.google.mail.models import (
    ActionPreview,
    ComposePreview,
    Draft,
    DraftDeleted,
    Message,
)

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures"
BASE = "https://gmail.googleapis.com/gmail/v1/users/me"
OWN = "user@example.com"
"""Demo's address as every fixture holds it, scrubbed: the one recipient of a recording."""
MARK = "xippios-live"
BODY = "Hello from the write tests.\nA second line."

WRITE_TASKS: dict[str, Callable[..., Any]] = {
    name: getattr(tasks_write, name) for name in tasks_write.__all__
}


def recorded(endpoint: str, case: str) -> Fixture:
    return load_fixture(fixture_path(FIXTURES, endpoint, case))


def answer(endpoint: str, case: str) -> Any:
    """The recorded answer's JSON."""
    return json.loads(recorded(endpoint, case).response.body)


def sent_body(route: respx.Route) -> Any:
    """The JSON body the last call of ``route`` sent; it carries no credential."""
    return json.loads(route.calls.last.request.content)


def decoded(raw: str) -> EmailMessage:
    """``raw`` read back: base64url, its padding restored, then the RFC 2822 text."""
    data = base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4))
    message = email.message_from_bytes(data, policy=email.policy.default)
    assert isinstance(message, EmailMessage)
    return message


def text_of(message: EmailMessage) -> str:
    content = message.get_content()
    assert isinstance(content, str)
    return content.replace("\r\n", "\n")


def assert_meta(result: Result[Any], endpoint: str, request: dict[str, Any]) -> None:
    meta = result.meta
    assert (meta.source, meta.endpoint, meta.tenant) == (SOURCE, endpoint, DEMO)
    assert meta.request == {"account": DEMO, **request}
    assert (meta.published_at, meta.vintage, meta.total) == (None, None, None)


# Structure ---------------------------------------------------------------------------


@pytest.mark.parametrize("task", WRITE_TASKS.values(), ids=WRITE_TASKS.keys())
def test_every_write_task_takes_account_first_and_confirm_last(task: Callable[..., Any]) -> None:
    assert hasattr(task, "async_fn"), "write the async function and derive the sync one"
    parameters = list(inspect.signature(task).parameters.values())
    first, confirm = parameters[0], parameters[-1]
    hints = typing.get_type_hints(task)
    empty = inspect.Parameter.empty
    assert (first.name, hints["account"], first.default) == ("account", str, empty)
    assert (confirm.name, hints["confirm"], confirm.default) == ("confirm", bool, False)
    assert confirm.kind is inspect.Parameter.KEYWORD_ONLY


@pytest.mark.parametrize("task", WRITE_TASKS.values(), ids=WRITE_TASKS.keys())
def test_every_write_task_documents_its_parameters_and_the_confirm_rule(
    task: Callable[..., Any],
) -> None:
    doc = inspect.getdoc(task) or ""
    assert doc.splitlines()[0].endswith(".")
    assert re.search(r"Without\s+``confirm=true``\s+nothing", doc), "the confirm rule, one sentence"
    assert re.search(r"quota\s+units", doc), "what a call costs"
    for parameter in inspect.signature(task).parameters:
        assert re.search(rf"^\s+{parameter}:", doc, re.MULTILINE), parameter


@pytest.mark.parametrize("task", WRITE_TASKS.values(), ids=WRITE_TASKS.keys())
def test_every_write_task_returns_a_preview_or_upstream_s_answer(
    task: Callable[..., Any],
) -> None:
    """``Result[<preview> | <answer>]``: ``performed`` false tells a preview."""
    facade: Any = task
    returned = typing.get_type_hints(facade.async_fn)["return"]
    (outcome,) = returned.__pydantic_generic_metadata__["args"]
    preview, result = typing.get_args(outcome)
    assert preview in (ComposePreview, ActionPreview)
    assert result in (Draft, Message, DraftDeleted)


# The write server --------------------------------------------------------------------
# Queue row 281: assert_write_tools, _confirm_problem, _tool_names and _serves are a copy
# of sdk-cashctrl's tests/test_tasks_write.py at 6db155b9f80e20b17b1533db66134b9eb2144c46
# (main, release 0.3.0), the kit's piece (d), which returns to xippios-core in 0.4.0 with
# its two consumers (ADR-0023, decision 4; ADR-0025, D-d); this package adopts it at
# 0.2.0. sdk-cashctrl copied it from xippios-core, which removed it in 0.3.0 (ADR-0024;
# sdk-core cb8ecdf, src/xippios/core/testing/adapters.py), with _confirm_problem from its
# src/xippios/core/mcp/server.py, and extended it with the check that confirm is the
# last parameter (queue row 26 f). The family check no longer tests a write server; this
# does. Async here: core listed the tools on its private background loop.


def _confirm_problem(fn: Callable[..., Any]) -> str | None:
    """Why ``fn`` does not take ``confirm: bool = False`` last, or ``None`` when it does.

    The annotation is resolved with ``typing.get_type_hints``, so a string annotation
    and ``Annotated[bool, ...]`` count as ``bool``; ``bool | None`` does not.
    """
    parameters = inspect.signature(fn).parameters
    parameter = parameters.get("confirm")
    if parameter is None:
        return "no parameter confirm"
    annotation = typing.get_type_hints(fn).get("confirm", parameter.annotation)
    if annotation is inspect.Parameter.empty:
        return "confirm is not annotated"
    if annotation is not bool:
        return f"confirm is annotated {annotation!r}, not bool"
    if parameter.default is inspect.Parameter.empty:
        return "confirm has no default"
    if parameter.default is not False:
        return f"confirm defaults to {parameter.default!r}, not False"
    if list(parameters)[-1] != "confirm":
        return "confirm is not the last parameter"
    return None


async def _tool_names(server: MCPServer) -> set[str]:
    return {tool.name for tool in await server.list_tools()}


def _serves(route: Any, source: str, fn: Callable[..., Any]) -> bool:
    """True when ``route`` is the one ``router_from_tasks`` builds for ``fn``.

    ``router_from_tasks`` names it ``<source>_<function>`` and puts it at
    ``<prefix>/<function>``; either is enough, so a custom prefix is caught too.
    """
    named = getattr(route, "name", None) == f"{source}_{fn.__name__}"
    return named or str(getattr(route, "path", "")).endswith(f"/{fn.__name__}")


async def assert_write_tools(
    write_server: MCPServer,
    write_tasks: list[Callable[..., Any]],
    *,
    source: str,
    read_server: MCPServer | None = None,
    router: APIRouter | None = None,
) -> None:
    """Check a write server against its write tasks (conventions §8).

    Fails the test, naming the offending tool, unless all of these hold:

    - the write server was registered with ``read_only=False`` (``is_write_server``),
      which ``main``'s loopback rule for ``--http`` depends on;
    - the write server's tools are exactly ``tool_name(source, fn)`` for the write
      tasks, no more and no fewer;
    - every write task takes ``confirm: bool = False`` as its last parameter, so a call
      without it is a dry run;
    - no write tool is on ``read_server``, and no route of ``router`` serves a write
      task (matched by the route name and path ``router_from_tasks`` gives it), when
      given.

    A write server exposes write tools only: reference tools, the account list among
    them, stay on the read server.
    """
    assert is_write_server(write_server), (
        "the write server was not registered with read_only=False; the loopback rule depends on it"
    )
    expected = {tool_name(source, fn): fn for fn in write_tasks}
    listed = await _tool_names(write_server)
    unexpected = sorted(listed - expected.keys())
    assert not unexpected, f"write server tools that are not write tasks: {unexpected}"
    missing = sorted(expected.keys() - listed)
    assert not missing, f"write tasks missing from the write server: {missing}"
    for name, fn in expected.items():
        problem = _confirm_problem(fn)
        assert problem is None, f"{name}: {problem}; a write tool takes confirm: bool = False last"
    if read_server is not None:
        on_read = sorted(expected.keys() & await _tool_names(read_server))
        assert not on_read, f"write tools on the read server: {on_read}"
    if router is not None:
        for name, fn in expected.items():
            for route in router.routes:
                path = str(getattr(route, "path", ""))
                methods = " ".join(sorted(getattr(route, "methods", None) or ()))
                served = _serves(route, source, fn)
                assert not served, f"{name}: a write task has an HTTP route, {methods} {path}"


@pytest.mark.anyio
async def test_the_write_server_serves_the_write_tasks_only() -> None:
    """Conventions §8: a write server, its tools exactly ``tasks_write.__all__``, confirm last.

    The account list, a reference tool, stays on the read server, and no write task is a
    tool of the read server or a route of the HTTP router (ADR-0026: read and write
    never mix).
    """
    assert len(WRITE_TASKS) == 5
    await assert_write_tools(
        write_server,
        list(WRITE_TASKS.values()),
        source=SOURCE,
        read_server=read_server,
        router=router,
    )


@pytest.mark.anyio
async def test_the_read_server_is_no_write_server_and_keeps_its_tools() -> None:
    assert is_write_server(write_server)
    assert not is_write_server(read_server)
    expected = {tool_name(SOURCE, getattr(tasks, name)) for name in tasks.__all__}
    assert await _tool_names(read_server) == expected
    assert write_server.name == read_server.name == f"xippios-{SOURCE}", "core's default"


@pytest.mark.anyio
async def test_write_tools_take_their_task_s_parameters_and_say_they_write() -> None:
    tools = {tool.name: tool for tool in await write_server.list_tools()}
    for name, fn in WRITE_TASKS.items():
        tool = tools[tool_name(SOURCE, fn)]
        assert set(tool.input_schema.get("properties", {})) == set(
            inspect.signature(fn).parameters
        ), name
        assert tool.annotations is not None
        assert (tool.annotations.read_only_hint, tool.annotations.destructive_hint) == (
            False,
            True,
        ), name
    read_tools = await read_server.list_tools()
    assert all(tool.annotations and tool.annotations.read_only_hint for tool in read_tools)


def create_thing(name: str, confirm: bool = False) -> dict[str, str]:
    """Create a thing; without confirm, return a preview.

    Args:
        name: The thing's name, e.g. ``"Corn"``.
        confirm: ``True`` writes, ``False`` previews.
    """
    return {"name": name, "written": str(confirm)}


def delete_thing(thing_id: int, confirm: typing.Annotated[bool, "write"] = False) -> dict[str, int]:
    """Delete a thing; without confirm, return a preview.

    Args:
        thing_id: The thing's id, e.g. ``4``.
        confirm: ``True`` deletes, ``False`` previews.
    """
    return {"id": thing_id}


def list_things() -> list[str]:
    """List the things."""
    return ["a"]


WRITE: list[Callable[..., Any]] = [create_thing, delete_thing]
READ: list[Callable[..., Any]] = [list_things]


@pytest.mark.anyio
async def test_assert_write_tools_accepts_a_separate_write_server() -> None:
    await assert_write_tools(
        build_server("example", WRITE, read_only=False),
        WRITE,
        source="example",
        read_server=build_server("example", READ),
        router=router_from_tasks("example", READ),
    )


@pytest.mark.anyio
async def test_assert_write_tools_wants_a_server_registered_as_a_write_server() -> None:
    read_only = build_server("example", [])
    register_tasks(read_only, "example", WRITE)  # the write tasks, registered read-only
    with pytest.raises(
        AssertionError,
        match=(
            r"^the write server was not registered with read_only=False; "
            r"the loopback rule depends on it"
        ),
    ):
        await assert_write_tools(read_only, WRITE, source="example")


@pytest.mark.anyio
async def test_assert_write_tools_wants_exactly_the_write_tasks() -> None:
    write = build_server("example", WRITE, read_only=False)
    register_tasks(write, "example", [list_things])
    with pytest.raises(AssertionError, match=r"not write tasks: \['example_list_things'\]"):
        await assert_write_tools(write, WRITE, source="example")
    write = build_server("example", [create_thing], read_only=False)
    with pytest.raises(AssertionError, match=r"missing .*: \['example_delete_thing'\]"):
        await assert_write_tools(write, WRITE, source="example")


def no_confirm(name: str) -> None:
    """No confirm at all."""


def confirm_true(confirm: bool = True) -> None:
    """Writes unless told not to."""


def confirm_required(confirm: bool) -> None:
    """Must be told."""


def confirm_unannotated(confirm=False):  # type: ignore[no-untyped-def]
    """Not annotated."""


def confirm_optional(confirm: bool | None = False) -> None:
    """Optional."""


def confirm_zero(confirm: int = 0) -> None:
    """An int."""


def confirm_first(confirm: bool = False, name: str = "") -> None:
    """Confirm before the other parameters."""


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("task", "problem"),
    [
        (no_confirm, "no parameter confirm"),
        (confirm_true, "confirm defaults to True, not False"),
        (confirm_required, "confirm has no default"),
        (confirm_unannotated, "confirm is not annotated"),
        (confirm_optional, r"confirm is annotated bool \| None, not bool"),
        (confirm_zero, "confirm is annotated <class 'int'>, not bool"),
        (confirm_first, "confirm is not the last parameter"),
    ],
)
async def test_assert_write_tools_wants_confirm_false_last(
    task: Callable[..., Any], problem: str
) -> None:
    write = build_server("example", [create_thing], read_only=False)
    register_tasks(write, "example", [task])  # build_server(read_only=False) refuses most
    with pytest.raises(AssertionError, match=f"^example_{task.__name__}: {problem}; "):
        await assert_write_tools(write, [create_thing, task], source="example")


@pytest.mark.anyio
async def test_assert_write_tools_keeps_write_tools_off_the_read_adapters() -> None:
    write = build_server("example", WRITE, read_only=False)
    read = build_server("example", [list_things, delete_thing])
    with pytest.raises(AssertionError, match=r"on the read server: \['example_delete_thing'\]"):
        await assert_write_tools(write, WRITE, source="example", read_server=read)
    with_write = router_from_tasks("example", [list_things, create_thing])
    with pytest.raises(
        AssertionError,
        match=r"^example_create_thing: a write task has an HTTP route, GET /example/create_thing",
    ):
        await assert_write_tools(write, WRITE, source="example", router=with_write)
    prefixed = router_from_tasks("example", [delete_thing], prefix="/v1")
    with pytest.raises(AssertionError, match=r"^example_delete_thing: .* GET /v1/delete_thing"):
        await assert_write_tools(write, WRITE, source="example", router=prefixed)


@pytest.mark.anyio
async def test_assert_write_tools_names_tools_with_the_underscored_source() -> None:
    write = build_server("google-mail", WRITE, read_only=False)
    await assert_write_tools(
        write, WRITE, source="google-mail", router=router_from_tasks("google-mail", READ)
    )
    with_write = router_from_tasks("google-mail", WRITE)
    with pytest.raises(AssertionError, match=r"^google_mail_create_thing: "):
        await assert_write_tools(write, WRITE, source="google-mail", router=with_write)


# The composer, on messages built in memory -------------------------------------------


def built(**overrides: Any) -> EmailMessage:
    given: dict[str, Any] = {
        "to": addresses("to", ["Alice <alice@example.com>", "bob@example.com"], required=True),
        "subject": "Lunch on Friday",
        "body": BODY,
    } | overrides
    return compose(**given)


def test_the_message_carries_its_headers_and_no_from() -> None:
    message = built(
        cc=addresses("cc", ["carol@example.com"], required=False),
        bcc=addresses("bcc", ["Dave <dave@example.com>"], required=False),
    )
    assert message["To"] == "Alice <alice@example.com>, bob@example.com"
    assert (message["Cc"], message["Bcc"]) == ("carol@example.com", "Dave <dave@example.com>")
    assert message["Subject"] == "Lunch on Friday"
    assert message["From"] is None, "Gmail sets From from the account"
    assert (message["Date"], message["Message-ID"]) == (None, None)
    assert (message["In-Reply-To"], message["References"]) == (None, None)
    assert message.get_content_type() == "text/plain"
    assert message.get_content_charset() == "utf-8"
    assert [(h.name, h.value) for h in preview_headers(message)] == [
        ("To", "Alice <alice@example.com>, bob@example.com"),
        ("Cc", "carol@example.com"),
        ("Bcc", "Dave <dave@example.com>"),
        ("Subject", "Lunch on Friday"),
    ]


def test_a_non_ascii_subject_and_body_survive_raw_as_seven_bit_text() -> None:
    subject = f"Grüße aus Zürich — {MARK}"
    body = "Liebe Jürgen,\nbis Freitag. ✓\n"
    to = addresses("to", ["Jürgen Müller <juergen@example.com>"], required=True)
    message = compose(to=to, subject=subject, body=body)
    raw = encode_raw(message)
    assert re.fullmatch(r"[A-Za-z0-9_-]+", raw), "base64url, without padding"
    data = base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4))
    assert data.isascii(), "RFC 2822 text is US-ASCII: RFC 2047 headers, an encoded body"
    assert b"\r\n" in data
    assert b"\n" not in data.replace(b"\r\n", b""), "every line ends in CRLF"
    back = decoded(raw)
    assert back["Subject"] == subject
    assert back["To"] == "Jürgen Müller <juergen@example.com>"
    assert back["From"] is None
    assert text_of(back) == body
    assert back["Content-Transfer-Encoding"] in ("quoted-printable", "base64")
    assert POLICY.linesep == "\r\n"


def test_a_reply_takes_the_original_s_thread_ids_and_subject() -> None:
    original = Message.model_validate(
        {
            "id": "m1",
            "threadId": "t1",
            "payload": {
                "headers": [
                    {"name": "Subject", "value": "Lunch"},
                    {"name": "References", "value": "<a@example.com> <b@example.com>"},
                    {"name": "Message-Id", "value": "<c@example.com>"},
                ]
            },
        }
    )
    reply = Reply.of(original)
    assert (reply.thread_id, reply.message_id) == ("t1", "<c@example.com>"), "any case"
    message = built(subject="", reply=reply)
    assert message["Subject"] == "Re: Lunch"
    assert message["In-Reply-To"] == "<c@example.com>"
    assert message["References"] == "<a@example.com> <b@example.com> <c@example.com>"
    assert built(subject="Another", reply=reply)["Subject"] == "Another", "a subject given wins"
    again = Reply(thread_id="t1", message_id="<d@example.com>", references=None, subject="RE: x")
    assert built(subject="", reply=again)["Subject"] == "RE: x", "no second Re:"
    assert built(subject="", reply=again)["References"] == "<d@example.com>"
    bare = Reply(thread_id="t1", message_id=None, references=None, subject=None)
    without = built(subject="", reply=bare)
    assert (without["In-Reply-To"], without["References"], without["Subject"]) == (
        None,
        None,
        "Re:",
    )


@pytest.mark.parametrize(
    ("values", "message"),
    [
        ("alice@example.com", r"^to takes a list of addresses"),
        ([], r"^to takes one address at least"),
        (["alice"], r"^to: each item is one address with a domain"),
        (["alice@example.com, bob@example.com"], r"^to: each item is one address"),
        (["alice@example.com\r\nBcc: eve@example.com"], r"^to: an address holds no line break"),
        ([""], r"^to: each item is an address"),
        ([3], r"^to: each item is an address"),
    ],
)
def test_addresses_are_refused_by_name_never_by_value(values: Any, message: str) -> None:
    with pytest.raises(InvalidRequestError, match=message) as info:
        addresses("to", values, required=True)
    assert "eve@" not in info.value.message


# Previews: nothing sent --------------------------------------------------------------

PREVIEWS: dict[str, tuple[Callable[..., Any], tuple[Any, ...], dict[str, Any]]] = {
    "create_draft": (tasks_write.create_draft, (DEMO, [OWN], f"{MARK} draft", BODY), {}),
    "send_message": (tasks_write.send_message, (DEMO, [OWN], f"{MARK} send", BODY), {}),
    "delete_draft": (tasks_write.delete_draft, (DEMO, "r-1234567890123456789"), {}),
    "modify_message": (
        tasks_write.modify_message,
        (DEMO, "19a0b0c0d0e0f001"),
        {"add_label_ids": ["STARRED"], "remove_label_ids": ["UNREAD"]},
    ),
    "trash_message": (tasks_write.trash_message, (DEMO, "19a0b0c0d0e0f001"), {}),
}


@pytest.mark.parametrize("name", PREVIEWS)
@pytest.mark.parametrize("confirm", [False, "true", 1], ids=["default", "the-text", "one"])
def test_a_preview_sends_nothing(respx_mock: respx.MockRouter, name: str, confirm: Any) -> None:
    """Without ``confirm=True`` (from Python anything else previews) no request goes out."""
    task, args, kwargs = PREVIEWS[name]
    result = task(*args, **kwargs) if confirm is False else task(*args, **kwargs, confirm=confirm)
    assert result.data.performed is False
    assert isinstance(result.data, ComposePreview | ActionPreview)
    assert result.meta.endpoint == result.data.endpoint == f"gmail.users.{_operation(name)}"
    assert result.meta.tenant == DEMO
    assert not respx_mock.calls, "nothing sent, not even the token request"


def _operation(name: str) -> str:
    return {
        "create_draft": "drafts.create",
        "send_message": "messages.send",
        "delete_draft": "drafts.delete",
        "modify_message": "messages.modify",
        "trash_message": "messages.trash",
    }[name]


def test_a_message_s_preview_shows_its_headers_and_first_lines(
    respx_mock: respx.MockRouter,
) -> None:
    body = "\n".join(f"line {n}" for n in range(1, 13))
    result = tasks_write.send_message(
        DEMO,
        ["Jürgen <juergen@example.com>"],
        f"Grüße {MARK}",
        body,
        cc=["carol@example.com"],
        bcc=["dave@example.com"],
    )
    preview = result.data
    assert isinstance(preview, ComposePreview)
    assert [(h.name, h.value) for h in preview.headers] == [
        ("To", "Jürgen <juergen@example.com>"),
        ("Cc", "carol@example.com"),
        ("Bcc", "dave@example.com"),
        ("Subject", f"Grüße {MARK}"),
    ], "decoded, as the standard library renders them; no From"
    assert preview.body_lines == [f"line {n}" for n in range(1, PREVIEW_LINES + 1)]
    assert preview.body_line_count == 12
    assert preview.thread_id is None
    assert preview.action == (
        "send this message from account demo to its To, Cc and Bcc recipients; "
        "once sent it cannot be called back"
    )
    assert_meta(
        result,
        "gmail.users.messages.send",
        {
            "to": ["Jürgen <juergen@example.com>"],
            "cc": ["carol@example.com"],
            "bcc": ["dave@example.com"],
            "subject": f"Grüße {MARK}",
        },
    )
    assert not respx_mock.calls


@pytest.mark.parametrize("name", ["create_draft", "send_message"])
def test_meta_request_never_holds_the_body(respx_mock: respx.MockRouter, name: str) -> None:
    """ADR-0017; conventions §4: the message's text goes to upstream inside raw alone."""
    task, args, kwargs = PREVIEWS[name]
    result = task(*args, **kwargs)
    assert "body" not in result.meta.request
    assert "second line" not in result.meta.model_dump_json()
    assert set(result.meta.request) == {"account", "to", "subject"}


def test_an_action_preview_names_the_message_and_the_labels(respx_mock: respx.MockRouter) -> None:
    result = tasks_write.modify_message(
        DEMO, "19a0b0c0d0e0f001", add_label_ids=["STARRED"], remove_label_ids=["UNREAD"]
    )
    assert result.data == ActionPreview(
        endpoint="gmail.users.messages.modify",
        action=(
            "add the labels STARRED to and remove the labels UNREAD from the message "
            "19a0b0c0d0e0f001 of account demo"
        ),
        id="19a0b0c0d0e0f001",
        add_label_ids=["STARRED"],
        remove_label_ids=["UNREAD"],
    )
    assert_meta(
        result,
        "gmail.users.messages.modify",
        {"id": "19a0b0c0d0e0f001", "addLabelIds": ["STARRED"], "removeLabelIds": ["UNREAD"]},
    )
    only_remove = tasks_write.modify_message(DEMO, "m1", remove_label_ids=["UNREAD"])
    assert isinstance(only_remove.data, ActionPreview)
    assert only_remove.data.action == "remove the labels UNREAD from the message m1 of account demo"
    assert only_remove.meta.request == {"account": DEMO, "id": "m1", "removeLabelIds": ["UNREAD"]}
    deleted = tasks_write.delete_draft(DEMO, "r-1").data
    assert isinstance(deleted, ActionPreview)
    assert deleted.action == (
        "delete the draft r-1 of account demo immediately and permanently; "
        "it is not moved to the trash"
    )
    trashed = tasks_write.trash_message(DEMO, "m1").data
    assert isinstance(trashed, ActionPreview)
    assert trashed.action == "move the message m1 of account demo to the trash"
    assert not respx_mock.calls


@pytest.mark.anyio
async def test_a_preview_over_mcp_sends_nothing(respx_mock: respx.MockRouter) -> None:
    payload = await mcp_call(
        write_server,
        "google_mail_send_message",
        {"account": DEMO, "to": [OWN], "subject": f"{MARK} send", "body": BODY},
    )
    assert payload["data"]["performed"] is False
    assert payload["data"]["body_lines"] == BODY.splitlines()
    assert payload["meta"]["request"] == {"account": DEMO, "to": [OWN], "subject": f"{MARK} send"}
    off = await mcp_call(
        write_server,
        "google_mail_trash_message",
        {"account": DEMO, "message_id": "m1", "confirm": "false"},
    )
    assert off["data"]["performed"] is False
    assert not respx_mock.calls


# Refusals before any request ---------------------------------------------------------

W: Any = tasks_write
"""The module untyped, so a test can pass what a type checker would refuse."""


@pytest.mark.parametrize(
    ("call", "message"),
    [
        (lambda: W.send_message("work", [OWN], "s", "b"), r"^unknown tenant 'work'"),
        (lambda: W.delete_draft("work", "r-1"), r"^unknown tenant 'work'"),
        (lambda: W.send_message(DEMO, OWN, "s", "b"), r"^to takes a list"),
        (lambda: W.create_draft(DEMO, [OWN], "a\nBcc: x", "b"), r"^subject holds no"),
        (lambda: W.create_draft(DEMO, [OWN], "s", 3), r"^body is the message's"),
        (
            lambda: W.send_message(DEMO, [OWN], "s", "b", cc=["a@example.com, b@x.org"]),
            r"^cc: each item is one address",
        ),
        (
            lambda: W.send_message(DEMO, [OWN], "s", "b", in_reply_to_message_id=""),
            r"^in_reply_to_message_id is an id",
        ),
        (lambda: W.modify_message(DEMO, "m1"), r"both empty; name one label"),
        (
            lambda: W.modify_message(DEMO, "m1", add_label_ids="STARRED"),
            r"^add_label_ids takes a list of label ids",
        ),
        (
            lambda: W.modify_message(DEMO, "m1", remove_label_ids=["x"] * 101),
            r"^remove_label_ids holds 101 labels; upstream takes 100 at most",
        ),
        (lambda: W.trash_message(DEMO, " "), r"^message_id is an id"),
        (lambda: W.delete_draft(DEMO, "", confirm=True), r"^draft_id is an id"),
    ],
)
def test_a_bad_argument_is_refused_before_any_request(
    respx_mock: respx.MockRouter, call: Callable[[], Any], message: str
) -> None:
    with pytest.raises(InvalidRequestError, match=message):
        call()
    assert not respx_mock.calls


# Confirmed calls, replayed -----------------------------------------------------------


def test_create_draft_stores_the_message_as_a_draft(replay_fixture: ReplayFixture) -> None:
    replay_fixture("oauth2_token", "write")
    route = replay_fixture("create_draft", "ok")
    expected = answer("create_draft", "ok")
    subject = f"{MARK} draft"
    result = tasks_write.create_draft(DEMO, [OWN], subject, BODY, confirm=True)
    draft = result.data
    assert isinstance(draft, Draft)
    assert draft.id == expected["id"]
    assert draft.message is not None
    assert draft.message.id == expected["message"]["id"]
    assert "DRAFT" in draft.message.label_ids
    sent = sent_body(route)
    assert set(sent) == {"message"}
    assert set(sent["message"]) == {"raw"}, "no threadId for a new conversation"
    message = decoded(sent["message"]["raw"])
    assert (message["To"], message["Subject"], message["From"]) == (OWN, subject, None)
    assert text_of(message) == BODY + "\n"
    assert json.loads(recorded("create_draft", "ok").request.body or "") == {"message": {}}
    assert_meta(result, "gmail.users.drafts.create", {"to": [OWN], "subject": subject})
    assert route.call_count == 1


@pytest.mark.anyio
async def test_create_draft_over_mcp(replay_fixture: ReplayFixture) -> None:
    replay_fixture("oauth2_token", "write")
    replay_fixture("create_draft", "ok")
    payload = await mcp_call(
        write_server,
        "google_mail_create_draft",
        {"account": DEMO, "to": [OWN], "subject": f"{MARK} draft", "body": BODY, "confirm": True},
    )
    assert payload["data"]["id"] == answer("create_draft", "ok")["id"]
    assert "performed" not in payload["data"], "upstream's answer, no preview"


def test_delete_draft_deletes_the_draft(replay_fixture: ReplayFixture) -> None:
    replay_fixture("oauth2_token", "write")
    route = replay_fixture("delete_draft", "ok")
    fixture = recorded("delete_draft", "ok")
    draft_id = fixture.request.url.rsplit("/", 1)[-1]
    assert fixture.response.status in (200, 204)
    result = tasks_write.delete_draft(DEMO, draft_id, confirm=True)
    assert result.data == DraftDeleted(id=draft_id)
    assert result.data.performed is True
    assert_meta(result, "gmail.users.drafts.delete", {"id": draft_id})
    assert route.call_count == 1


@pytest.mark.anyio
async def test_delete_draft_over_mcp(replay_fixture: ReplayFixture) -> None:
    replay_fixture("oauth2_token", "write")
    replay_fixture("delete_draft", "ok")
    draft_id = recorded("delete_draft", "ok").request.url.rsplit("/", 1)[-1]
    payload = await mcp_call(
        write_server,
        "google_mail_delete_draft",
        {"account": DEMO, "draft_id": draft_id, "confirm": True},
    )
    assert payload["data"] == {"performed": True, "id": draft_id}


def test_send_message_sends_it(replay_fixture: ReplayFixture) -> None:
    replay_fixture("oauth2_token", "write")
    route = replay_fixture("send_message", "ok")
    expected = answer("send_message", "ok")
    subject = f"{MARK} send"
    result = tasks_write.send_message(DEMO, [OWN], subject, BODY, confirm=True)
    sent_message = result.data
    assert isinstance(sent_message, Message)
    assert (sent_message.id, sent_message.thread_id) == (expected["id"], expected["threadId"])
    assert sent_message.label_ids == expected["labelIds"]
    assert "SENT" in sent_message.label_ids
    sent = sent_body(route)
    assert set(sent) == {"raw"}
    message = decoded(sent["raw"])
    assert (message["To"], message["Subject"]) == (OWN, subject)
    assert json.loads(recorded("send_message", "ok").request.body or "") == {}, "no raw kept"
    assert_meta(result, "gmail.users.messages.send", {"to": [OWN], "subject": subject})


@pytest.mark.anyio
async def test_send_message_over_mcp(replay_fixture: ReplayFixture) -> None:
    replay_fixture("oauth2_token", "write")
    replay_fixture("send_message", "ok")
    payload = await mcp_call(
        write_server,
        "google_mail_send_message",
        {"account": DEMO, "to": [OWN], "subject": f"{MARK} send", "body": BODY, "confirm": True},
    )
    assert payload["data"]["id"] == answer("send_message", "ok")["id"]


def test_trash_message_moves_it_to_the_trash(replay_fixture: ReplayFixture) -> None:
    replay_fixture("oauth2_token", "write")
    route = replay_fixture("trash_message", "ok")
    expected = answer("trash_message", "ok")
    result = tasks_write.trash_message(DEMO, expected["id"], confirm=True)
    assert isinstance(result.data, Message)
    assert result.data.id == expected["id"]
    assert "TRASH" in result.data.label_ids
    assert route.calls.last.request.content == b"", "trash takes no body"
    assert_meta(result, "gmail.users.messages.trash", {"id": expected["id"]})


@pytest.mark.anyio
async def test_trash_message_over_mcp(replay_fixture: ReplayFixture) -> None:
    replay_fixture("oauth2_token", "write")
    replay_fixture("trash_message", "ok")
    message_id = answer("trash_message", "ok")["id"]
    payload = await mcp_call(
        write_server,
        "google_mail_trash_message",
        {"account": DEMO, "message_id": message_id, "confirm": True},
    )
    assert "TRASH" in payload["data"]["label_ids"]


def test_modify_message_adds_and_removes_labels(replay_fixture: ReplayFixture) -> None:
    replay_fixture("oauth2_token", "write")
    route = replay_fixture("modify_message", "ok")
    expected = answer("modify_message", "ok")
    result = tasks_write.modify_message(
        DEMO,
        expected["id"],
        add_label_ids=["STARRED"],
        remove_label_ids=["UNREAD"],
        confirm=True,
    )
    assert isinstance(result.data, Message)
    assert "STARRED" in result.data.label_ids
    assert "UNREAD" not in result.data.label_ids
    body = {"addLabelIds": ["STARRED"], "removeLabelIds": ["UNREAD"]}
    assert sent_body(route) == body
    assert json.loads(recorded("modify_message", "ok").request.body or "") == body
    assert_meta(
        result,
        "gmail.users.messages.modify",
        {"id": expected["id"], "addLabelIds": ["STARRED"], "removeLabelIds": ["UNREAD"]},
    )


@pytest.mark.anyio
async def test_modify_message_over_mcp(replay_fixture: ReplayFixture) -> None:
    replay_fixture("oauth2_token", "write")
    replay_fixture("modify_message", "ok")
    message_id = answer("modify_message", "ok")["id"]
    payload = await mcp_call(
        write_server,
        "google_mail_modify_message",
        {
            "account": DEMO,
            "message_id": message_id,
            "add_label_ids": ["STARRED"],
            "remove_label_ids": ["UNREAD"],
            "confirm": True,
        },
    )
    assert "STARRED" in payload["data"]["label_ids"]


# Replies, from the recorded metadata read --------------------------------------------


def original_headers() -> dict[str, str]:
    message = answer("create_draft", "reply")
    return {header["name"].lower(): header["value"] for header in message["payload"]["headers"]}


def test_a_reply_s_preview_reads_the_original_once(replay_fixture: ReplayFixture) -> None:
    replay_fixture("oauth2_token", "write")
    route = replay_fixture("create_draft", "reply")
    original = answer("create_draft", "reply")
    headers = original_headers()
    result = tasks_write.create_draft(DEMO, [OWN], "", BODY, in_reply_to_message_id=original["id"])
    preview = result.data
    assert isinstance(preview, ComposePreview)
    assert preview.thread_id == original["threadId"]
    shown = {h.name: h.value for h in preview.headers}
    assert shown["Subject"] == f"Re: {headers['subject']}"
    assert shown["In-Reply-To"] == headers["message-id"]
    references = " ".join(filter(None, [headers.get("references"), headers["message-id"]]))
    assert shown["References"] == references
    assert route.call_count == 1
    query = route.calls.last.request.url.params
    assert query.get_list("format") == ["metadata"]
    assert query.get_list("metadataHeaders") == REPLY_HEADERS
    assert_meta(
        result,
        "gmail.users.drafts.create",
        {
            "to": [OWN],
            "subject": "",
            "in_reply_to_message_id": original["id"],
            "threadId": original["threadId"],
        },
    )


def test_a_confirmed_reply_joins_the_thread(replay_fixture: ReplayFixture) -> None:
    """The recorded draft answers the stored reply; what counts is what was sent."""
    replay_fixture("oauth2_token", "write")
    replay_fixture("create_draft", "reply")
    created = replay_fixture("create_draft", "ok")
    original = answer("create_draft", "reply")
    headers = original_headers()
    tasks_write.create_draft(
        DEMO, [OWN], "", BODY, in_reply_to_message_id=original["id"], confirm=True
    )
    sent = sent_body(created)
    assert sent["message"]["threadId"] == original["threadId"]
    message = decoded(sent["message"]["raw"])
    assert message["In-Reply-To"] == headers["message-id"]
    assert message["Subject"] == f"Re: {headers['subject']}"


# The live tests' safety rule, offline ------------------------------------------------


@pytest.fixture(scope="module")
def live() -> ModuleType:
    """``tests/test_live.py`` as a module, for its helper; none of its tests runs here."""
    spec = importlib.util.spec_from_file_location("live_helpers", ROOT / "tests" / "test_live.py")
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("live_helpers", module)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"to": ["someone.else@example.org"]}, r"^to holds an address other than demo's own"),
        ({"to": [OWN], "cc": ["Other <other@example.org>"]}, r"^cc holds an address other"),
        ({"to": [OWN], "bcc": [f"{OWN}, x@example.org"]}, r"^bcc holds an address other"),
        ({"to": []}, r"^to names no address"),
        ({"to": [OWN], "subject": "no marker"}, rf"^the subject carries no {MARK}"),
    ],
)
def test_the_live_tests_write_to_demo_alone_with_the_marker(
    live: ModuleType, respx_mock: respx.MockRouter, kwargs: dict[str, Any], message: str
) -> None:
    given = {"subject": f"{MARK} send", **kwargs}
    with pytest.raises(ValueError, match=message) as info:
        live.self_addressed(OWN, **given)
    assert "example.org" not in str(info.value), "the refusal names no address"
    assert not respx_mock.calls, "refused before any request"
    live.self_addressed(OWN, to=[f"Demo <{OWN.upper()}>"], subject=f"{MARK} send")
