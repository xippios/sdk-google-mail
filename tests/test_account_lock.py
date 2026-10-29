"""The --account lock and the loopback rule: ``_account_lock``, wired up by ``_cli``.

From sdk-cashctrl's ``tests/test_org_lock.py`` at ``6db155b`` (queue row 281), with
``account`` for ``org``: no middleware, but a wrapper that binds ``account`` on every tool
when the server is built, so no tool takes it. Every tool call goes through
``xippios.core.testing.mcp_call_text``, an in-process ``mcp.Client``, so core's argument
check runs as it does for a real client. sdk-cashctrl's write-server tests stay there:
this package has no write server yet. Added: the read server's loopback rule
(ADR-0025, D-a) and the unset refresh token at startup.
"""

import dataclasses
import inspect
import json
import logging
import typing
from typing import Any

import httpx
import pytest
import respx
from mcp import Client
from mcp.server.mcpserver import MCPServer
from xippios.core.mcp import build_server
from xippios.core.testing import ReplayFixture, mcp_call, mcp_call_text

from xippios.google.mail import mcp, tasks
from xippios.google.mail._account_lock import (
    AccountLock,
    bind,
    bind_tasks,
    instructions,
    lock_sentence,
)
from xippios.google.mail._cli import REMOTE_REFUSAL, serve
from xippios.google.mail._client import DEMO, SOURCE, TENANTS

pytestmark = pytest.mark.anyio

TOOL = "google_mail_echo_account"
LIST = "google_mail_list_accounts"
REQUEST_LOGGERS = ("httpx", "httpcore")
SENTENCE = (
    "This server is bound to account demo; every tool acts on that account and takes no account."
)
LOCK = AccountLock("account", DEMO)


class EchoServers:
    """Builds servers with one tool that records the account it was called for."""

    def __init__(self, instructions: str | None = None) -> None:
        self.calls: list[str] = []
        self.locks: list[AccountLock | None] = []
        self.instructions = instructions

    def build(self, lock: AccountLock | None = None) -> MCPServer:
        calls = self.calls

        async def echo_account(account: str, note: str = "") -> dict[str, str]:
            """Return the account the tool was called for.

            Args:
                account: Account name, e.g. ``demo``.
                note: Any text, e.g. ``"hi"``.
            """
            calls.append(account)
            return {"account": account, "note": note}

        self.locks.append(lock)
        return build_server(
            SOURCE,
            bind_tasks([echo_account, tasks.list_accounts], lock),
            instructions=instructions(self.instructions, lock),
        )


@pytest.fixture
def runs(monkeypatch: pytest.MonkeyPatch) -> list[tuple[MCPServer, str, dict[str, Any]]]:
    """Every server core's ``main`` would have run, with its transport and options."""
    served: list[tuple[MCPServer, str, dict[str, Any]]] = []

    def run(self: MCPServer, transport: str, **options: Any) -> None:
        served.append((self, transport, options))

    monkeypatch.setattr(MCPServer, "run", run)
    return served


def locked(echo: EchoServers) -> MCPServer:
    return echo.build(LOCK)


# The lock and the wrapper ------------------------------------------------------------


def test_a_lock_is_a_frozen_pair() -> None:
    assert (LOCK.param, LOCK.value) == ("account", DEMO)
    assert AccountLock(param="account", value=DEMO) == LOCK
    with pytest.raises(dataclasses.FrozenInstanceError):
        LOCK.value = "other"  # type: ignore[misc]


@pytest.mark.parametrize(
    ("param", "value"), [("", "work"), ("an account", "work"), ("account", "")]
)
def test_a_lock_needs_a_parameter_name_and_a_tenant(param: str, value: str) -> None:
    with pytest.raises(ValueError, match=r"param must be|value must name"):
        AccountLock(param, value)


@pytest.mark.parametrize("name", tasks.__all__)
def test_a_bound_task_keeps_its_name_and_drops_account(name: str) -> None:
    """Same ``__name__`` (tool names, ``MCP_CAPS`` keys), no ``account`` anywhere."""
    task = getattr(tasks, name)
    bound = bind(task, LOCK)
    assert bound.__name__ == task.__name__
    parameters = inspect.signature(bound).parameters
    assert "account" not in parameters
    assert list(parameters) == [p for p in inspect.signature(task).parameters if p != "account"]
    assert "account" not in typing.get_type_hints(bound)
    doc = inspect.getdoc(bound) or ""
    assert doc.splitlines()[0] == (inspect.getdoc(task) or "").splitlines()[0]
    assert "    account:" not in doc
    assert ("Args:" in doc) == bool(parameters), "an Args: block the account line emptied goes"


def test_the_docstring_loses_the_account_entry_and_its_continuation() -> None:
    def task(account: str, q: str | None = None) -> None:
        """Find things.

        Args:
            account: A declared account name,
                e.g. ``demo``.
            q: Gmail's search syntax, e.g. ``"from:me"``.

        Returns:
            Nothing.
        """

    assert inspect.getdoc(bind(task, LOCK)) == (
        'Find things.\n\nArgs:\n    q: Gmail\'s search syntax, e.g. ``"from:me"``.\n\n'
        "Returns:\n    Nothing."
    )


def test_a_task_without_account_is_left_as_it_is() -> None:
    def ping() -> dict[str, bool]:
        """Answer without an account."""
        return {"pong": True}

    assert bind(ping, LOCK) is ping
    assert bind_tasks([ping], None) == [ping]


async def test_a_bound_sync_facade_awaits_its_async_twin(replay_fixture: ReplayFixture) -> None:
    replay_fixture("oauth2_token", "ok")
    route = replay_fixture("get_profile", "ok")
    result = await bind(tasks.get_profile, LOCK)()
    assert (result.meta.tenant, result.meta.request) == (DEMO, {"account": DEMO})
    assert route.call_count == 1


# Calls on a bound server -------------------------------------------------------------


async def test_the_bound_account_is_passed() -> None:
    echo = EchoServers()
    text = await mcp_call_text(locked(echo), TOOL, {"note": "hi"})
    assert json.loads(text) == {"account": DEMO, "note": "hi"}
    assert echo.calls == [DEMO]


async def test_no_tool_of_a_bound_server_takes_account() -> None:
    async with Client(locked(EchoServers())) as client:
        tools = (await client.list_tools()).tools
    assert [tool.name for tool in tools] == [TOOL, LIST]
    for tool in tools:
        assert "account" not in tool.input_schema.get("properties", {}), tool.name
        assert "account:" not in (tool.description or ""), tool.name


@pytest.mark.parametrize("other", [DEMO, "other", "DEMO", "", None])
async def test_a_call_that_still_passes_account_is_answered_for_the_bound_account(
    other: str | None,
) -> None:
    """As sdk-cashctrl observed under core 0.3.0: the argument check drops an unknown argument."""
    echo = EchoServers()
    text = await mcp_call_text(locked(echo), TOOL, {"account": other, "note": "x"})
    assert json.loads(text) == {"account": DEMO, "note": "x"}
    assert echo.calls == [DEMO]


async def test_a_refused_argument_is_answered_by_core_before_the_tool_runs() -> None:
    echo = EchoServers()
    text = await mcp_call_text(locked(echo), TOOL, {"note": 7})
    assert text == "error: InvalidRequestError: note: Input should be a valid string"
    assert echo.calls == []


async def test_the_account_list_shows_the_bound_account_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(TENANTS.list_var, "xa,xb")
    echo = EchoServers()
    unlocked = json.loads(await mcp_call_text(echo.build(), LIST, {}))
    assert unlocked["data"] == ["xa", "xb", DEMO]
    only = json.loads(await mcp_call_text(locked(echo), LIST, {}))
    assert only["data"] == [DEMO]
    assert only["meta"]["tenant"] is None
    assert only["meta"]["endpoint"] == "list_accounts"
    assert tasks.list_accounts().data == ["xa", "xb", DEMO], "the task itself is unchanged"


async def test_a_lock_on_an_undeclared_account_lists_nothing() -> None:
    server = EchoServers().build(AccountLock("account", "gone"))
    assert (await mcp_call(server, LIST, {}))["data"] == []


async def test_the_bound_read_server_answers_for_the_bound_account_only(
    monkeypatch: pytest.MonkeyPatch, replay_fixture: ReplayFixture
) -> None:
    monkeypatch.setenv(TENANTS.list_var, "xa")
    monkeypatch.setenv(TENANTS.var("xa", "REFRESH_TOKEN"), "xa-token")
    replay_fixture("oauth2_token", "ok")
    route = replay_fixture("get_profile", "ok")
    payload = await mcp_call(mcp.build(LOCK), "google_mail_get_profile", {"account": "xa"})
    assert (payload["meta"]["tenant"], payload["meta"]["request"]) == (DEMO, {"account": DEMO})
    assert route.call_count == 1


# Startup and the command line --------------------------------------------------------


async def test_serve_binds_the_server_then_hands_over_to_core(
    runs: list[tuple[MCPServer, str, dict[str, Any]]],
) -> None:
    echo = EchoServers()
    unlocked = echo.build()
    serve(
        unlocked, echo.build, ["--host", "127.0.0.1", "--account=demo", "--http", "--port", "9001"]
    )
    assert echo.locks == [None, LOCK]
    ((server, transport, options),) = runs
    assert server is not unlocked, "the bound server is built anew"
    assert (transport, options) == ("streamable-http", {"host": "127.0.0.1", "port": 9001})
    assert json.loads(await mcp_call_text(server, TOOL, {}))["account"] == DEMO
    assert echo.calls == [DEMO]


def test_serve_without_account_serves_unlocked(
    runs: list[tuple[MCPServer, str, dict[str, Any]]],
) -> None:
    echo = EchoServers()
    unlocked = echo.build()
    serve(unlocked, echo.build, [])
    assert runs == [(unlocked, "stdio", {})]
    assert echo.locks == [None]


def test_an_undeclared_account_fails_fast(
    runs: list[tuple[MCPServer, str, dict[str, Any]]], capsys: pytest.CaptureFixture[str]
) -> None:
    echo = EchoServers()
    with pytest.raises(SystemExit) as info:
        serve(echo.build(), echo.build, ["--account", "nope", "--http"])
    assert info.value.code == 2
    err = capsys.readouterr().err
    assert "xippios-google-mail: error: unknown tenant 'nope'; declared tenants: demo" in err
    assert "placeholder" not in err
    assert echo.locks == [None], "nothing is built for an undeclared account"
    assert runs == []


def test_a_listed_account_without_its_refresh_token_fails_at_startup(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    runs: list[tuple[MCPServer, str, dict[str, Any]]],
) -> None:
    monkeypatch.setenv(TENANTS.list_var, "xa")
    echo = EchoServers()
    with pytest.raises(SystemExit) as info:
        serve(echo.build(), echo.build, ["--account", "xa"])
    assert info.value.code == 2
    err = capsys.readouterr().err
    assert (
        "xippios-google-mail: error: GOOGLE_XA_REFRESH_TOKEN is not set; export the refresh "
        "token xippios-google-mail-auth --account xa printed"
    ) in err
    assert echo.locks == [None]
    assert runs == []


def test_help_lists_account_and_core_options(capsys: pytest.CaptureFixture[str]) -> None:
    echo = EchoServers()
    with pytest.raises(SystemExit) as info:
        serve(echo.build(), echo.build, ["--help"])
    assert info.value.code == 0
    out = capsys.readouterr().out
    for option in ("--account", "--http", "--host", "--port", "--allow-remote"):
        assert option in out


def test_unknown_options_still_fail() -> None:
    echo = EchoServers()
    with pytest.raises(SystemExit) as info:
        serve(echo.build(), echo.build, ["--account", "demo", "--nope"])
    assert info.value.code == 2


def test_the_entry_point_fails_fast_on_an_undeclared_account(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    runs: list[tuple[MCPServer, str, dict[str, Any]]],
) -> None:
    monkeypatch.setattr("sys.argv", ["xippios-google-mail-mcp-read", "--account", "nope"])
    with pytest.raises(SystemExit) as info:
        mcp.run()
    assert info.value.code == 2
    assert "unknown tenant 'nope'" in capsys.readouterr().err
    assert runs == []


async def test_the_entry_point_binds_to_the_account(
    monkeypatch: pytest.MonkeyPatch,
    runs: list[tuple[MCPServer, str, dict[str, Any]]],
) -> None:
    monkeypatch.setattr("sys.argv", ["xippios-google-mail-mcp-read", "--account", DEMO])
    mcp.run()
    ((server, transport, _),) = runs
    assert transport == "stdio"
    assert server is not mcp.server
    assert server.name == mcp.server.name
    assert server.instructions == f"{mcp.INSTRUCTIONS} {SENTENCE}"
    bound = {tool.name: tool for tool in await server.list_tools()}
    unbound = {tool.name: tool for tool in await mcp.server.list_tools()}
    assert bound.keys() == unbound.keys(), "the same tools, by name"
    for name, tool in bound.items():
        expected = set(unbound[name].input_schema.get("properties", {})) - {"account"}
        assert set(tool.input_schema.get("properties", {})) == expected, name


async def test_a_bound_server_names_the_account_in_its_instructions(
    runs: list[tuple[MCPServer, str, dict[str, Any]]],
) -> None:
    echo = EchoServers(instructions="Gmail via xippios.")
    serve(echo.build(), echo.build, ["--account", DEMO])
    ((server, _, _),) = runs
    async with Client(server) as client:
        assert client.instructions == f"Gmail via xippios. {SENTENCE}"
    assert lock_sentence(LOCK) == SENTENCE
    assert instructions(None, LOCK) == SENTENCE


async def test_an_unlocked_server_keeps_its_instructions(
    runs: list[tuple[MCPServer, str, dict[str, Any]]],
) -> None:
    echo = EchoServers(instructions="Gmail via xippios.")
    serve(echo.build(), echo.build, [])
    ((server, _, _),) = runs
    async with Client(server) as client:
        assert client.instructions == "Gmail via xippios."
    assert json.loads(await mcp_call_text(server, TOOL, {"account": "other"}))["account"] == "other"


# The loopback rule (ADR-0025, D-a) ---------------------------------------------------


@pytest.mark.parametrize(
    "argv",
    [["--http", "--host", "0.0.0.0"], ["--account", DEMO, "--http", "--host", "192.0.2.1"]],
    ids=["unlocked", "locked"],
)
def test_the_read_server_stays_on_loopback_over_http(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    runs: list[tuple[MCPServer, str, dict[str, Any]]],
    argv: list[str],
) -> None:
    """It reaches every declared mailbox and carries no authentication: exit 2, nothing served."""
    monkeypatch.setattr("sys.argv", ["xippios-google-mail-mcp-read", *argv])
    with pytest.raises(SystemExit) as info:
        mcp.run()
    assert info.value.code == 2
    assert f"xippios-google-mail: error: {REMOTE_REFUSAL}" in capsys.readouterr().err
    assert runs == []


def test_allow_remote_serves_it_beyond_loopback(
    monkeypatch: pytest.MonkeyPatch, runs: list[tuple[MCPServer, str, dict[str, Any]]]
) -> None:
    monkeypatch.setattr(
        "sys.argv",
        ["xippios-google-mail-mcp-read", "--http", "--host", "0.0.0.0", "--allow-remote"],
    )
    mcp.run()
    assert [(transport, options["host"]) for _, transport, options in runs] == [
        ("streamable-http", "0.0.0.0")
    ]


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "::1"])
def test_every_loopback_host_is_served(
    monkeypatch: pytest.MonkeyPatch, runs: list[tuple[MCPServer, str, dict[str, Any]]], host: str
) -> None:
    monkeypatch.setattr("sys.argv", ["xippios-google-mail-mcp-read", "--http", "--host", host])
    mcp.run()
    assert [(transport, options["host"]) for _, transport, options in runs] == [
        ("streamable-http", host)
    ]


def test_stdio_is_never_refused(
    monkeypatch: pytest.MonkeyPatch, runs: list[tuple[MCPServer, str, dict[str, Any]]]
) -> None:
    """``--host`` without ``--http`` binds nothing: the rule is about HTTP."""
    monkeypatch.setattr("sys.argv", ["xippios-google-mail-mcp-read", "--host", "0.0.0.0"])
    mcp.run()
    assert [transport for _, transport, _ in runs] == ["stdio"]


def test_the_loopback_rule_comes_before_the_account(
    capsys: pytest.CaptureFixture[str], runs: list[tuple[MCPServer, str, dict[str, Any]]]
) -> None:
    echo = EchoServers()
    with pytest.raises(SystemExit):
        serve(echo.build(), echo.build, ["--account", "nope", "--http", "--host", "0.0.0.0"])
    assert REMOTE_REFUSAL in capsys.readouterr().err
    assert echo.locks == [None]
    assert runs == []


@pytest.mark.parametrize("argv", [[], ["--account", DEMO]], ids=["unlocked", "locked"])
def test_serve_keeps_request_urls_out_of_the_log(
    caplog: pytest.LogCaptureFixture,
    respx_mock: respx.MockRouter,
    runs: list[tuple[MCPServer, str, dict[str, Any]]],
    argv: list[str],
) -> None:
    """Core's ``main`` quiets httpx and httpcore before it serves; the plugin restores them."""
    echo = EchoServers()
    server = echo.build()
    for name in REQUEST_LOGGERS:
        logging.getLogger(name).setLevel(logging.NOTSET)
    serve(server, echo.build, argv)
    assert [logging.getLogger(name).level for name in REQUEST_LOGGERS] == [logging.WARNING] * 2
    respx_mock.get("https://gmail.googleapis.com/gmail/v1/users/me/xq-path").respond(200)
    with caplog.at_level(logging.INFO):
        httpx.get("https://gmail.googleapis.com/gmail/v1/users/me/xq-path")
    assert "xq-path" not in caplog.text
    assert not [record for record in caplog.records if record.name in REQUEST_LOGGERS]
