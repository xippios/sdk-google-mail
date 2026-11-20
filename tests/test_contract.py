"""The package contract and what the adapters of xippios-core 0.4.0 answer a caller.

Offline, in process: the package's modules, both MCP servers through an in-process
``mcp.client.Client``, the router through FastAPI's ``TestClient`` and the token
endpoint's refusals through ``respx``. ``mcp.build()`` and ``mcp_write.build()`` are the
tool sets a host serves (conventions §8; sdk-core ADR-0026, amendment 2026-11-18, points c
and d), and ``_client.ENV_VARS`` the variables the package reads, for that host. Every
refusal below comes before a request to Gmail, so nothing is sent.
"""

import importlib.util
import json
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
from fastapi import FastAPI
from fastapi.testclient import TestClient
from mcp.client import Client
from mcp.types import TextContent
from xippios.core import AuthError, EnvVar, Result, ResultMeta, TenantLock, UpstreamError
from xippios.core.mcp import ToolSet, build_server, cap_result, is_loopback_only

import xippios.google.mail
from xippios.google.mail import mcp, mcp_write, tasks, tasks_write
from xippios.google.mail._auth import TOKEN_URL
from xippios.google.mail._client import DEMO, ENV_VARS, SOURCE, TENANTS
from xippios.google.mail.http import router
from xippios.google.mail.models import DecodedMessage


def test_the_package_ships_no_reference_scaffold() -> None:
    """The package packages no list (``has_reference_data`` is false), so it has no
    ``params`` or ``enums`` module and no ``reference/`` directory."""
    for module in ("params", "enums"):
        assert importlib.util.find_spec(f"xippios.google.mail.{module}") is None, module
    (package,) = xippios.google.mail.__path__
    assert not (Path(package) / "reference").exists()


# The package contract ----------------------------------------------------------------


def test_env_vars_name_the_account_list_the_refresh_tokens_and_the_client_pair() -> None:
    assert (
        EnvVar("GOOGLE_ACCOUNTS", secret=False, required=False),
        EnvVar("GOOGLE_<NAME>_REFRESH_TOKEN", secret=True, required=True),
        EnvVar("GOOGLE_CLIENT_ID", secret=True, required=True),
        EnvVar("GOOGLE_CLIENT_SECRET", secret=True, required=True),
    ) == ENV_VARS
    assert mcp.ENV_VARS is ENV_VARS, "the template's mcp.py names the client's list"
    assert mcp_write.ENV_VARS is ENV_VARS
    assert TENANTS.var("work", "REFRESH_TOKEN") == "GOOGLE_WORK_REFRESH_TOKEN", "the pattern"


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("module", "functions", "read_only"),
    [(mcp, tasks, True), (mcp_write, tasks_write, False)],
    ids=["mcp", "mcp_write"],
)
async def test_build_is_the_tool_set_the_entry_point_serves(
    module: Any, functions: Any, read_only: bool
) -> None:
    tools = module.build()
    assert isinstance(tools, ToolSet)
    assert tools.source == SOURCE
    assert tools.read_only is read_only
    assert tools.instructions == module.INSTRUCTIONS
    served = [tool.name for tool in await module.server.list_tools()]
    hosted = [tool.name for tool in await build_server(tools).list_tools()]
    assert hosted == served == [f"google_mail_{name}" for name in functions.__all__]
    assert tools.add_options is None, "no option of the package's own"
    assert tools.apply_options is None


def test_the_read_server_is_held_to_loopback_bound_or_not() -> None:
    """The read server reaches every declared mailbox and carries no authentication
    (sdk-core ADR-0025, D-a): core holds it to loopback as it holds the write server."""
    assert mcp.build().loopback_only
    assert is_loopback_only(mcp.server)
    assert is_loopback_only(build_server(mcp.build(TenantLock(TENANTS, DEMO))))
    assert is_loopback_only(mcp_write.server), "a write server, by core's rule"


# What a caller sees of core 0.4.0 -----------------------------------------------------


async def call(server: Any, name: str, arguments: dict[str, Any]) -> tuple[bool, str]:
    """A tool's answer through an in-process client: whether it is flagged, and its text."""
    async with Client(server) as client:
        result = await client.call_tool(name, arguments)
    content = result.content[0]
    assert isinstance(content, TextContent), content
    return result.is_error, content.text


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("module", "name", "arguments", "takes"),
    [
        (mcp, "google_mail_list_accounts", {"foo": 1}, "no arguments"),
        (
            mcp_write,
            "google_mail_delete_draft",
            {"account": DEMO, "draft_id": "d", "foo": 1},
            "account, draft_id, confirm",
        ),
    ],
    ids=["read", "write"],
)
async def test_a_tool_refuses_an_argument_it_does_not_declare(
    module: Any, name: str, arguments: dict[str, Any], takes: str
) -> None:
    """Refused before the task runs, where the argument was dropped and the tool ran."""
    flagged, text = await call(module.server, name, arguments)
    assert flagged
    assert text == (
        f"error: InvalidRequestError: foo: Not an argument of this tool; it takes {takes}"
    )


@pytest.mark.anyio
async def test_a_bound_server_refuses_a_call_that_still_names_the_account() -> None:
    bound = build_server(mcp.build(TenantLock(TENANTS, DEMO)))
    flagged, text = await call(
        bound, "google_mail_search_messages", {"account": DEMO, "q": "x", "max_results": 2}
    )
    assert flagged
    assert text == (
        "error: InvalidRequestError: account: Not an argument of this tool; "
        "it takes q, label_ids, max_results"
    )


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("module", "name", "arguments", "message"),
    [
        (mcp, "google_mail_get_profile", {"account": "nope"}, "unknown tenant 'nope'"),
        (
            mcp,
            "google_mail_get_thread",
            {"account": DEMO, "thread_id": "t", "start": "abc"},
            "start: Input should be a valid integer",
        ),
        (
            mcp_write,
            "google_mail_trash_message",
            {"account": "nope", "message_id": "m"},
            "unknown tenant 'nope'",
        ),
    ],
    ids=["task", "schema", "write"],
)
async def test_an_error_answer_is_flagged(
    module: Any, name: str, arguments: dict[str, Any], message: str
) -> None:
    """``isError`` is set beside the family's text, so a client tells it from data."""
    flagged, text = await call(module.server, name, arguments)
    assert flagged
    assert text.startswith(f"error: InvalidRequestError: {message}")


@pytest.mark.anyio
async def test_the_search_bound_names_the_tool_of_list_messages() -> None:
    """An error that names another task names that task's MCP tool too (conventions §8)."""
    _, text = await call(
        mcp.server, "google_mail_search_messages", {"account": DEMO, "q": "x", "max_results": 51}
    )
    assert text.endswith(
        "page through the ids with list_messages (over MCP, the tool google_mail_list_messages)"
    )


def test_an_unknown_account_names_the_source_google_mail() -> None:
    """The registry is core's, named after the package's slug, its variables keeping the
    prefix ``GOOGLE``; the error named ``google`` before."""
    with pytest.raises(Exception, match="unknown tenant 'nope'; declared tenants: demo") as info:
        tasks.get_profile("nope")
    assert getattr(info.value, "source", None) == SOURCE


@pytest.mark.parametrize(
    ("path", "detail"),
    [
        (
            "/google-mail/get_thread?account=demo&thread_id=t&start=abc",
            "start: Input should be a valid integer, unable to parse string as an integer",
        ),
        ("/google-mail/get_profile", "account: Field required"),
    ],
    ids=["refused", "missing"],
)
def test_a_route_answers_a_refused_argument_as_problem_details(path: str, detail: str) -> None:
    """Before the task runs, with no input value, where FastAPI's own 422 echoed it."""
    app = FastAPI()
    app.include_router(router)
    response = TestClient(app).get(path)
    assert response.status_code == 422
    assert response.json() == {
        "type": "urn:xippios:error:InvalidRequestError",
        "title": "InvalidRequestError",
        "status": 422,
        "detail": detail,
        "source": SOURCE,
    }
    assert "abc" not in response.text


def test_a_thread_cut_whose_first_message_alone_passes_the_cap_names_no_resume() -> None:
    """The note says so and names no offset, where it ended ``resume with start=0``, the
    start the call had just used."""
    message = DecodedMessage.model_validate(
        {
            "id": "m1",
            "thread_id": "t",
            "label_ids": [],
            "internal_date": None,
            "size_estimate": None,
            "snippet": None,
            "headers": [],
            "text": "x" * 70_000,
            "html": None,
            "attachments": [],
        }
    )
    meta = ResultMeta(
        source=SOURCE,
        endpoint="gmail.users.threads.get",
        request={"account": DEMO, "thread_id": "t", "start": 0},
        retrieved_at="2026-11-20T00:00:00Z",
    )
    capped = json.loads(
        cap_result(Result(data=[message, message], meta=meta), tasks.MCP_CAPS["get_thread"])
    )
    assert capped["data"] == []
    assert capped["note"] == (
        "showing 0 of 2 rows (cap: 200 rows or 65536 bytes); the row at offset 0 alone exceeds "
        f"the byte cap; {tasks.MCP_CAPS['get_thread'].note}"
    )


@pytest.mark.parametrize(
    ("status", "error", "message"),
    [
        (400, UpstreamError, "400 from POST /token: Bad Request"),
        (403, AuthError, "403 from POST /token: Forbidden"),
        (503, UpstreamError, "503 from POST /token: Service Unavailable"),
    ],
)
def test_a_token_answer_the_package_does_not_know_is_quoted_nowhere(
    respx_mock: respx.MockRouter, status: int, error: type[Exception], message: str
) -> None:
    """Not ``invalid_grant`` or ``invalid_client``: the status, the request and the reason
    phrase, and nothing of the answer, which a token endpoint may write from the
    credentials it was sent; the message and the body quoted it before."""
    secret = "Missing required parameter: refresh_token"
    respx_mock.post(TOKEN_URL).mock(
        return_value=httpx.Response(status, json={"error": "x", "error_description": secret})
    )
    with pytest.raises(error) as info:
        tasks.get_profile(DEMO)
    assert str(info.value) == message
    assert not getattr(info.value, "body", None)
