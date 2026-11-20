"""The package's wiring of core's --account lock and loopback rule.

Core's ``TenantLock``, ``bind_tasks`` and ``serve_tenants`` bind a server to one account,
and core's ``tests/test_tenant_lock.py`` and ``tests/mcp/test_cli.py`` hold the tests of
the lock and the command. What stays here is this package's own: its tasks bound, the
account list on a bound server, ``--account`` with ``check_account`` on both entry
points, their instructions, the read server held to loopback (``loopback_only``;
sdk-core ADR-0025, D-a) and the write server held there by core's rule. Every tool call goes
through ``xippios.core.testing.mcp_call_text``, an in-process ``mcp.Client``, so core's
argument check runs as it does for a real client.
"""

import inspect
import json
import sys
import typing
from typing import Any

import pytest
from mcp.server.mcpserver import MCPServer
from xippios.core import TenantLock, bind_tasks
from xippios.core.mcp import build_server
from xippios.core.testing import ReplayFixture, mcp_call, mcp_call_text

from xippios.google.mail import mcp, mcp_write, tasks, tasks_write
from xippios.google.mail._client import DEMO, SOURCE, TENANTS

pytestmark = pytest.mark.anyio

LIST = "google_mail_list_accounts"
SENTENCE = (
    "This server is bound to account demo; every tool acts on that account and takes no account."
)
LOCK = TenantLock(TENANTS, DEMO)


@pytest.fixture
def runs(monkeypatch: pytest.MonkeyPatch) -> list[tuple[MCPServer, str, dict[str, Any]]]:
    """Every server core's ``main`` would have run, with its transport and options."""
    served: list[tuple[MCPServer, str, dict[str, Any]]] = []

    def run(self: MCPServer, transport: str, **options: Any) -> None:
        served.append((self, transport, options))

    monkeypatch.setattr(MCPServer, "run", run)
    return served


def run_as(monkeypatch: pytest.MonkeyPatch, entry_point: str, *argv: str) -> None:
    """Set the command line as the installed entry point's script runs it.

    As the ``entry_point`` fixture of core's ``tests/mcp/test_cli.py`` sets it: core's
    ``main`` names the entry point that runs in its usage line and errors, and an
    installed script runs as ``__main__`` without a module spec, which argparse reads to
    tell it from ``python -m``.
    """
    monkeypatch.setattr(sys, "argv", [f"/opt/venv/bin/{entry_point}", *argv])
    monkeypatch.setattr(sys.modules["__main__"], "__spec__", None)


# The package's tasks, bound ----------------------------------------------------------


@pytest.mark.parametrize("name", [*tasks.__all__, *tasks_write.__all__])
def test_a_bound_task_keeps_its_name_and_drops_account(name: str) -> None:
    """Same ``__name__`` (tool names, ``MCP_CAPS`` keys), no ``account`` anywhere."""
    task = getattr(tasks, name, None) or getattr(tasks_write, name)
    bound = bind_tasks([task], LOCK)[0]
    assert bound.__name__ == task.__name__
    parameters = inspect.signature(bound).parameters
    assert "account" not in parameters
    assert list(parameters) == [p for p in inspect.signature(task).parameters if p != "account"]
    assert "account" not in typing.get_type_hints(bound)
    doc = inspect.getdoc(bound) or ""
    assert doc.splitlines()[0] == (inspect.getdoc(task) or "").splitlines()[0]
    assert "    account:" not in doc
    assert ("Args:" in doc) == bool(parameters), "an Args: block the account line emptied goes"


async def test_a_bound_sync_facade_awaits_its_async_twin(replay_fixture: ReplayFixture) -> None:
    replay_fixture("oauth2_token", "ok")
    route = replay_fixture("get_profile", "ok")
    result = await bind_tasks([tasks.get_profile], LOCK)[0]()
    assert (result.meta.tenant, result.meta.request) == (DEMO, {"account": DEMO})
    assert route.call_count == 1


# Calls on a bound server -------------------------------------------------------------


async def test_the_account_list_shows_the_bound_account_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(TENANTS.list_var, "xa,xb")
    unlocked = json.loads(await mcp_call_text(build_server(mcp.build()), LIST, {}))
    assert unlocked["data"] == ["xa", "xb", DEMO]
    only = json.loads(await mcp_call_text(build_server(mcp.build(LOCK)), LIST, {}))
    assert only["data"] == [DEMO]
    assert only["meta"]["tenant"] is None
    assert only["meta"]["endpoint"] == "list_accounts"
    assert tasks.list_accounts().data == ["xa", "xb", DEMO], "the task itself is unchanged"


async def test_the_bound_read_server_answers_for_the_bound_account_only(
    monkeypatch: pytest.MonkeyPatch, replay_fixture: ReplayFixture
) -> None:
    monkeypatch.setenv(TENANTS.list_var, "xa")
    monkeypatch.setenv(TENANTS.var("xa", "REFRESH_TOKEN"), "xa-token")
    replay_fixture("oauth2_token", "ok")
    route = replay_fixture("get_profile", "ok")
    server = build_server(mcp.build(LOCK))
    refused = await mcp_call_text(server, "google_mail_get_profile", {"account": "xa"})
    assert refused == (
        "error: InvalidRequestError: account: Not an argument of this tool; it takes no arguments"
    )
    assert route.call_count == 0, "a refused call reaches no mailbox"
    payload = await mcp_call(server, "google_mail_get_profile", {})
    assert (payload["meta"]["tenant"], payload["meta"]["request"]) == (DEMO, {"account": DEMO})
    assert route.call_count == 1


# Startup and the command line --------------------------------------------------------


def test_a_listed_account_without_its_refresh_token_fails_at_startup(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    runs: list[tuple[MCPServer, str, dict[str, Any]]],
) -> None:
    monkeypatch.setenv(TENANTS.list_var, "xa")
    run_as(monkeypatch, "xippios-google-mail-mcp-read", "--account", "xa")
    with pytest.raises(SystemExit) as info:
        mcp.run()
    assert info.value.code == 2
    err = capsys.readouterr().err
    assert (
        "xippios-google-mail-mcp-read: error: GOOGLE_XA_REFRESH_TOKEN is not set; export the "
        "refresh token xippios-google-mail-auth --account xa printed"
    ) in err
    assert runs == []


@pytest.mark.parametrize("module", [mcp, mcp_write], ids=["mcp", "mcp_write"])
def test_both_entry_points_fail_fast_on_an_undeclared_account(
    module: Any,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    runs: list[tuple[MCPServer, str, dict[str, Any]]],
) -> None:
    monkeypatch.setattr("sys.argv", ["entry-point", "--account", "nope"])
    with pytest.raises(SystemExit) as info:
        module.run()
    assert info.value.code == 2
    assert "unknown tenant 'nope'" in capsys.readouterr().err
    assert runs == []


@pytest.mark.parametrize("module", [mcp, mcp_write], ids=["mcp", "mcp_write"])
async def test_both_entry_points_bind_to_the_account(
    module: Any,
    monkeypatch: pytest.MonkeyPatch,
    runs: list[tuple[MCPServer, str, dict[str, Any]]],
) -> None:
    monkeypatch.setattr("sys.argv", ["entry-point", "--account", DEMO])
    module.run()
    ((server, transport, _),) = runs
    assert transport == "stdio"
    assert server is not module.server
    assert server.name == module.server.name == f"xippios-{SOURCE}", "core's default name"
    assert server.instructions == f"{module.BOUND_INSTRUCTIONS} {SENTENCE}"
    assert "takes account" not in (server.instructions or ""), "no word of an account argument"
    assert "takes account" in (module.server.instructions or ""), "the unbound text keeps it"
    bound = {tool.name: tool for tool in await server.list_tools()}
    unbound = {tool.name: tool for tool in await module.server.list_tools()}
    assert bound.keys() == unbound.keys(), "the same tools, by name"
    for name, tool in bound.items():
        expected = set(unbound[name].input_schema.get("properties", {})) - {"account"}
        assert set(tool.input_schema.get("properties", {})) == expected, name


# The loopback rule (sdk-core ADR-0025, D-a) ---------------------------------------------------


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
    run_as(monkeypatch, "xippios-google-mail-mcp-read", *argv)
    with pytest.raises(SystemExit) as info:
        mcp.run()
    assert info.value.code == 2
    assert (
        "xippios-google-mail-mcp-read: error: xippios-google-mail is a read server held to "
        "loopback and carries no authentication; serve it over --http on a loopback --host "
        "(127.0.0.1, localhost, ::1), or pass --allow-remote"
    ) in capsys.readouterr().err
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


@pytest.mark.parametrize(
    "argv",
    [["--http", "--host", "0.0.0.0"], ["--account", DEMO, "--http", "--host", "0.0.0.0"]],
    ids=["unlocked", "locked"],
)
def test_the_write_server_stays_on_loopback_by_core_s_rule(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    runs: list[tuple[MCPServer, str, dict[str, Any]]],
    argv: list[str],
) -> None:
    """Core's ``main`` refuses it with its own message."""
    monkeypatch.setattr("sys.argv", ["xippios-google-mail-mcp-write", *argv])
    with pytest.raises(SystemExit) as info:
        mcp_write.run()
    assert info.value.code == 2
    err = capsys.readouterr().err
    assert "xippios-google-mail is a write server and carries no authentication" in err
    assert runs == []
    monkeypatch.setattr("sys.argv", ["xippios-google-mail-mcp-write", *argv, "--allow-remote"])
    mcp_write.run()
    monkeypatch.setattr("sys.argv", ["xippios-google-mail-mcp-write", "--http", "--host", "::1"])
    mcp_write.run()
    assert [(transport, options["host"]) for _, transport, options in runs] == [
        ("streamable-http", "0.0.0.0"),
        ("streamable-http", "::1"),
    ]


def test_the_write_server_s_help_lists_account_and_allow_remote(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("sys.argv", ["xippios-google-mail-mcp-write", "--help"])
    with pytest.raises(SystemExit) as info:
        mcp_write.run()
    assert info.value.code == 0
    out = capsys.readouterr().out
    assert "--account" in out
    assert "--allow-remote" in out
