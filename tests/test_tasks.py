"""Replay tests on recorded fixtures, at least one per task (conventions §10), and ``classify``.

Every Gmail exchange replays the refresh first: ``oauth2_token/ok`` answers the token
request ``RefreshTokenAuth`` sends ahead of the call, the API fixture the call itself.
"""

import inspect
import json
import typing
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
from xippios.core import AuthError, InvalidRequestError, RateLimitError, sync
from xippios.core.mcp import tool_name
from xippios.core.testing import (
    Fixture,
    ReplayFixture,
    assert_time_between,
    fixture_path,
    load_fixture,
    mcp_call,
    response_from_fixture,
    sent_urls,
)

from xippios.google.mail import tasks
from xippios.google.mail._client import (
    DAILY_LIMIT_WAIT,
    DEMO,
    RATE_LIMIT_WAIT,
    SOURCE,
    TENANTS,
    classify,
    client_for,
    quota_spent,
    refresh_auth,
)
from xippios.google.mail.mcp import server
from xippios.google.mail.models import Profile

FIXTURES = Path(__file__).resolve().parent / "fixtures"
TENANT_LIST = "list_accounts"
"""The package's tenant list (``_tenants.list_task``, queue row 281): no ``account``."""
ACCOUNT_TASKS: dict[str, Callable[..., object]] = {
    name: getattr(tasks, name) for name in tasks.__all__ if name != TENANT_LIST
}
PROFILE_URL = "https://gmail.googleapis.com/gmail/v1/users/me/profile"


def recorded(endpoint: str, case: str) -> Fixture:
    return load_fixture(fixture_path(FIXTURES, endpoint, case))


def account_comes_first(fn: Callable[..., object]) -> bool:
    """``account: str`` is the first parameter, positional and without a default."""
    parameters = list(inspect.signature(fn).parameters.values())
    if not parameters:
        return False
    first = parameters[0]
    return (
        first.name == "account"
        and typing.get_type_hints(fn).get("account") is str
        and first.default is inspect.Parameter.empty
        and first.kind in (first.POSITIONAL_ONLY, first.POSITIONAL_OR_KEYWORD)
    )


def test_the_account_check_tells_good_from_bad() -> None:
    def good(account: str, q: str | None = None) -> None: ...
    def second(q: str, account: str) -> None: ...
    def optional(account: str = "demo") -> None: ...
    def keyword_only(*, account: str) -> None: ...
    async def get_thing_async(account: str) -> None: ...

    assert account_comes_first(good)
    assert account_comes_first(sync(get_thing_async)), "tasks are sync() facades"
    for bad in (second, optional, keyword_only):
        assert not account_comes_first(bad), bad.__name__


@pytest.mark.parametrize("task", ACCOUNT_TASKS.values(), ids=ACCOUNT_TASKS.keys())
def test_every_task_takes_account_first(task: Callable[..., object]) -> None:
    assert account_comes_first(task), "account: str is the first, required parameter"


# list_accounts -----------------------------------------------------------------------


def test_list_accounts_lists_the_declared_names_only(
    monkeypatch: pytest.MonkeyPatch, respx_mock: respx.MockRouter
) -> None:
    """The registry's names, in declared order, demo last; no value and no request."""
    assert tool_name(SOURCE, tasks.list_accounts) == "google_mail_list_accounts"
    assert list(inspect.signature(tasks.list_accounts).parameters) == []
    assert tasks.list_accounts().data == [DEMO]
    monkeypatch.setenv(TENANTS.list_var, "work,private")
    monkeypatch.setenv(TENANTS.var("work", "REFRESH_TOKEN"), "hidden-work-token")
    result = tasks.list_accounts()
    assert result.data == ["work", "private", DEMO]
    assert (result.meta.source, result.meta.endpoint) == (SOURCE, TENANT_LIST)
    assert (result.meta.tenant, result.meta.request) == (None, {})
    assert "hidden" not in result.model_dump_json()
    assert not respx_mock.calls


def test_list_accounts_has_an_agent_docstring() -> None:
    doc = inspect.getdoc(tasks.list_accounts) or ""
    assert doc.splitlines()[0] == "List the Gmail accounts this server can reach, by name."
    assert "account" in doc


@pytest.mark.anyio
async def test_list_accounts_over_mcp() -> None:
    payload = await mcp_call(server, "google_mail_list_accounts", {})
    assert payload["data"] == [DEMO]
    assert payload["meta"]["tenant"] is None


# get_profile -------------------------------------------------------------------------


def test_get_profile_returns_the_recorded_profile(replay_fixture: ReplayFixture) -> None:
    token = replay_fixture("oauth2_token", "ok")
    route = replay_fixture("get_profile", "ok")
    expected = json.loads(recorded("get_profile", "ok").response.body)
    before = datetime.now(UTC)
    result = tasks.get_profile(DEMO)
    after = datetime.now(UTC)
    assert isinstance(result.data, Profile)
    assert result.data.email_address == "user@example.com"
    assert result.data.messages_total == expected["messagesTotal"]
    assert result.data.threads_total == expected["threadsTotal"]
    assert result.data.history_id == expected["historyId"]
    meta = result.meta
    assert (meta.source, meta.endpoint) == (SOURCE, "gmail.users.getProfile")
    assert (meta.tenant, meta.request) == (DEMO, {"account": DEMO})
    assert (meta.published_at, meta.vintage, meta.total) == (None, None, None)
    assert_time_between(meta.retrieved_at, before, after)
    assert sent_urls(route) == [PROFILE_URL]
    assert token.call_count == 1


def test_get_profile_serialises_snake_case(replay_fixture: ReplayFixture) -> None:
    replay_fixture("oauth2_token", "ok")
    replay_fixture("get_profile", "ok")
    data = json.loads(tasks.get_profile(DEMO).model_dump_json())["data"]
    assert sorted(data) == ["email_address", "history_id", "messages_total", "threads_total"]


@pytest.mark.anyio
async def test_get_profile_over_mcp(replay_fixture: ReplayFixture) -> None:
    replay_fixture("oauth2_token", "ok")
    replay_fixture("get_profile", "ok")
    payload = await mcp_call(server, "google_mail_get_profile", {"account": DEMO})
    assert payload["data"]["email_address"] == "user@example.com"
    assert (payload["meta"]["tenant"], payload["meta"]["request"]) == (DEMO, {"account": DEMO})


def test_an_undeclared_account_is_refused_before_any_request(
    respx_mock: respx.MockRouter,
) -> None:
    with pytest.raises(
        InvalidRequestError, match=r"^unknown tenant 'work'; declared tenants: demo"
    ):
        tasks.get_profile("work")
    assert not respx_mock.calls


# classify ----------------------------------------------------------------------------


def test_the_second_401_names_the_account_and_the_path() -> None:
    response = response_from_fixture(recorded("get_profile", "unauthenticated"))
    error = classify(response, auth=refresh_auth(DEMO))
    assert isinstance(error, AuthError)
    assert error.status == 401
    assert error.message.startswith(
        "GET /gmail/v1/users/me/profile answered HTTP 401 authError: Gmail refused account "
        "demo's access token"
    )
    assert "gmail.googleapis.com" not in error.message


@pytest.mark.anyio
async def test_the_scope_refusal_names_the_grant_and_the_reconsent(
    replay_fixture: ReplayFixture,
) -> None:
    """F19's shape: a readonly grant refused a modify with 403 ``insufficientPermissions``.

    The recording's two calls, replayed through demo's client: the list for an id, then
    the empty modify; the message names the granted scope the refresh answer reported.
    """
    replay_fixture("oauth2_token", "ok")
    replay_fixture("messages_list", "one")
    refused = replay_fixture("messages_modify", "insufficient_permissions")
    client = client_for(DEMO)
    listed = await client.get_json("messages", params={"maxResults": 1})
    message_id = listed["messages"][0]["id"]
    with pytest.raises(AuthError) as info:
        await client.request("POST", f"messages/{message_id}/modify", json={})
    assert info.value.status == 403
    assert info.value.message == (
        f"POST /gmail/v1/users/me/messages/{message_id}/modify answered HTTP 403 "
        "insufficientPermissions: account demo was granted gmail.readonly; this tool needs "
        "gmail.modify; re-run xippios-google-mail-auth --account demo --scopes write"
    )
    assert refused.call_count == 1, "an AuthError is never retried"


def test_the_scope_refusal_without_an_auth_names_no_account() -> None:
    response = response_from_fixture(recorded("messages_modify", "insufficient_permissions"))
    error = classify(response)
    assert isinstance(error, AuthError)
    assert error.message.endswith(
        "account <name> was granted scopes this process has not seen; this tool needs "
        "gmail.modify; re-run xippios-google-mail-auth --account <name> --scopes write"
    )


def documented(status: int, reason: str, message: str) -> httpx.Response:
    """An answer shaped as ``docs/upstream/pages/handle-errors.md``'s sample for ``reason``."""
    body = {"error": {"errors": [{"reason": reason, "message": message}], "code": status}}
    return httpx.Response(status, json=body, request=httpx.Request("GET", PROFILE_URL))


@pytest.mark.parametrize(
    ("reason", "wait"),
    [
        ("rateLimitExceeded", RATE_LIMIT_WAIT),
        ("userRateLimitExceeded", RATE_LIMIT_WAIT),
        ("dailyLimitExceeded", DAILY_LIMIT_WAIT),
    ],
)
def test_a_spent_limit_is_a_rate_limit_error(reason: str, wait: float) -> None:
    error = classify(documented(403, reason, "Rate Limit Exceeded"), auth=refresh_auth(DEMO))
    assert isinstance(error, RateLimitError)
    assert error.retry_after == wait
    assert error.message.startswith(f"GET /gmail/v1/users/me/profile answered HTTP 403 {reason}")


def test_the_domain_policy_is_an_auth_error() -> None:
    sample = documented(403, "domainPolicy", "The domain administrators have disabled Gmail apps.")
    error = classify(sample, auth=refresh_auth(DEMO))
    assert isinstance(error, AuthError)
    assert error.status == 403
    assert "domainPolicy" in error.message


@pytest.mark.parametrize(
    "response",
    [
        documented(429, "rateLimitExceeded", "Too many requests"),
        documented(403, "forbidden", "Forbidden"),
        documented(401, "required", "Login Required"),
        documented(404, "notFound", "Not Found"),
        documented(500, "backendError", "Backend Error"),
        httpx.Response(200, json={}, request=httpx.Request("GET", PROFILE_URL)),
    ],
    ids=["429", "403-other", "401-other", "404", "500", "200"],
)
def test_everything_else_is_core_s_mapping(response: httpx.Response) -> None:
    assert classify(response, auth=refresh_auth(DEMO)) is None


def test_the_quota_policy_reads_the_403_of_a_spent_minute() -> None:
    assert quota_spent(documented(403, "rateLimitExceeded", "x")) == RATE_LIMIT_WAIT
    assert quota_spent(documented(403, "userRateLimitExceeded", "x")) == RATE_LIMIT_WAIT
    assert quota_spent(documented(403, "dailyLimitExceeded", "x")) is None
    assert quota_spent(response_from_fixture(recorded("get_profile", "ok"))) is None
    refusal = response_from_fixture(recorded("messages_modify", "insufficient_permissions"))
    assert quota_spent(refusal) is None


@pytest.mark.rate_limited
def test_a_spent_minute_holds_the_account_s_next_call_back(
    replay_fixture: ReplayFixture, respx_mock: respx.MockRouter
) -> None:
    """The account's QuotaPolicy: after a 403 rate limit nothing is sent for 60 s.

    The wait is over the policy's 30 s ``pause_cap``, so the next call is refused at once
    without sending (ADR-0015).
    """
    replay_fixture("oauth2_token", "ok")
    sample = documented(403, "userRateLimitExceeded", "User Rate Limit Exceeded")
    route = respx_mock.get(PROFILE_URL).respond(403, json=json.loads(sample.content))
    with pytest.raises(RateLimitError) as first:
        tasks.get_profile(DEMO)
    assert first.value.retry_after == RATE_LIMIT_WAIT
    with pytest.raises(RateLimitError, match="nothing was sent") as second:
        tasks.get_profile(DEMO)
    assert second.value.retry_after is not None
    assert route.call_count == 1


def _shapes(value: Any) -> Any:
    """For a failure message: a JSON value with every string replaced by its length."""
    if isinstance(value, dict):
        return {key: _shapes(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_shapes(item) for item in value]
    return len(value) if isinstance(value, str) else value


def test_the_recorded_refusal_is_f19_s_shape() -> None:
    """403, ``insufficientPermissions``, ``PERMISSION_DENIED``, the challenge naming the scopes."""
    fixture = recorded("messages_modify", "insufficient_permissions")
    error = json.loads(fixture.response.body)["error"]
    assert fixture.response.status == 403, _shapes(error)
    assert error["status"] == "PERMISSION_DENIED"
    assert [item["reason"] for item in error["errors"]] == ["insufficientPermissions"]
    challenge = dict(fixture.response.headers)["www-authenticate"]
    assert 'error="insufficient_scope"' in challenge
    assert "https://www.googleapis.com/auth/gmail.modify" in challenge
