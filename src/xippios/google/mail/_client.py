"""The account registry for Gmail: one configured ``AsyncClient`` per account (conventions §6).

Gmail serves every mailbox under a grant of its own, an account's refresh token issued to
the operator's Desktop-app OAuth client (ADR-0025). So there is no single client here:
every task takes ``account``, the name of an account declared in the environment, and
``client_for(account)`` returns that account's own client, with its own renewing bearer
(``RefreshTokenAuth``, ``_auth.py``), its own token bucket, its own quota policy and no
response cache.

The registry is the package's own ``Tenants`` (``_tenants.py``, copied from sdk-cashctrl,
queue row 281), ``TENANTS`` below; the environment is the contract (conventions §7):

- ``GOOGLE_ACCOUNTS``: comma-separated names matching ``^[a-z0-9_]+$``, e.g. ``work,private``;
- per name, ``GOOGLE_<NAME>_REFRESH_TOKEN``, the name upper-cased
  (``GOOGLE_WORK_REFRESH_TOKEN``); ``TENANTS.var(name, "REFRESH_TOKEN")`` names it;
- ``GOOGLE_CLIENT_ID`` and ``GOOGLE_CLIENT_SECRET``, the OAuth client every account
  shares, read by the auth when it refreshes;
- the reserved account ``demo`` is never listed in ``GOOGLE_ACCOUNTS``: it exists when
  ``GOOGLE_DEMO_REFRESH_TOKEN`` is set. Fixtures, live tests and the fixture recorder use
  ``demo`` and nothing else.

The registry's first argument is the variable prefix every Google source shares,
``google``, not this package's slug, so its messages say "google" (ADR-0025, D-b).
``TENANTS.names()`` lists the declared accounts, ``TENANTS.require(account)`` refuses an
undeclared one with ``InvalidRequestError`` (the caller's mistake), and a malformed list
is a ``ConfigError`` naming the variable, never a value (the operator's). The client is
kept under a digest of the refresh token, the one long-lived credential, so a refresh,
which changes only the access token in memory, rebuilds no client (ADR-0025, D-b).
Nothing here spans accounts: ``TENANTS.names()`` returns names only, never values.
"""

import dataclasses
import functools
import re
from typing import Any

import httpx
from xippios.core import (
    AsyncClient,
    AuthError,
    ClientConfig,
    QuotaPolicy,
    RateLimit,
    RateLimitError,
    XippiosError,
)

from xippios.google.mail._auth import (
    CONSENT_COMMAND,
    SCOPE_SETS,
    RefreshTokenAuth,
    scope_set_for,
    short_scope,
)
from xippios.google.mail._tenants import Tenants
from xippios.google.mail._version import __version__

__all__ = [
    "API_KEY_ENV",
    "BASE_URL",
    "DAILY_LIMIT_WAIT",
    "DEMO",
    "QUOTA_REASONS",
    "QUOTA_WINDOW",
    "RATE_LIMIT",
    "RATE_LIMIT_WAIT",
    "SOURCE",
    "TENANTS",
    "account_config",
    "classify",
    "client_for",
    "config",
    "quota_spent",
    "refresh_auth",
]

SOURCE = "google-mail"
BASE_URL = "https://gmail.googleapis.com/gmail/v1/users/me"
"""The authenticated user's resources: ``gmail/v1/users/{userId}`` with ``userId`` ``me``."""
# No API key exists: an account's bearer is renewed from its refresh token
# (RefreshTokenAuth), so the template's key variable stays None.
API_KEY_ENV: str | None = None
# A self-imposed pace (ADR-0010, decision 1): upstream documents its limit in quota units,
# 6,000 a minute per user per project (docs/upstream/pages/quota.md), not in requests, so
# 60 requests a minute per account stays under it unless the calls are dear (a full
# threads.get costs 120 units); the QuotaPolicy below is the backstop (queue rows 143, 299).
RATE_LIMIT = RateLimit(per_minute=60, burst=10)
"""Per account and self-imposed, 60 a minute with bursts of 10: ``client_for`` gives each
account a client, and each client a token bucket of its own."""

QUOTA_REASONS = frozenset({"rateLimitExceeded", "userRateLimitExceeded"})
"""The reasons of upstream's HTTP 403 to a spent per-minute limit (``handle-errors.md``)."""
RATE_LIMIT_WAIT = 60.0
"""Seconds a 403 ``rateLimitExceeded`` or ``userRateLimitExceeded`` waits: the quota's
window is a minute, and upstream sends no ``Retry-After`` or quota header (F8, F19)."""
DAILY_LIMIT_WAIT = 3600.0
"""Seconds a 403 ``dailyLimitExceeded`` waits, a project's limit the owner raises."""
QUOTA_WINDOW = 60.0
"""The quota policy's window: the per-minute quota, and a headerless 429's wait."""

DEMO = "demo"
"""The reserved account, declared by ``GOOGLE_DEMO_REFRESH_TOKEN`` alone and never listed."""

TENANTS = Tenants(
    "google",
    list_var="GOOGLE_ACCOUNTS",
    fields=("REFRESH_TOKEN",),
    reserved=(DEMO,),
)
"""Gmail's accounts, declared in the environment (ADR-0025, D-b)."""

_SCOPE_PARAM = re.compile(r'(?:^|[\s,])scope="([^"]*)"')
"""The ``scope`` parameter of a ``WWW-Authenticate: Bearer`` challenge, space-separated."""


def _error(response: httpx.Response) -> dict[str, Any] | None:
    """The ``error`` object of a Google JSON error body, else ``None``."""
    try:
        payload = response.json()
    except (ValueError, httpx.ResponseNotRead):
        return None
    error = payload.get("error") if isinstance(payload, dict) else None
    return error if isinstance(error, dict) else None


def _reason(response: httpx.Response) -> str | None:
    """The first ``error.errors[].reason`` of a Google JSON error body, e.g. ``authError``."""
    error = _error(response)
    items = error.get("errors") if error is not None else None
    if not isinstance(items, list) or not items or not isinstance(items[0], dict):
        return None
    reason = items[0].get("reason")
    return reason if isinstance(reason, str) else None


def _accepted_scopes(response: httpx.Response) -> list[str]:
    """The scopes a refused method accepts, from its ``WWW-Authenticate`` challenge (F19)."""
    match = _SCOPE_PARAM.search(response.headers.get("www-authenticate", ""))
    return match.group(1).split() if match is not None else []


def quota_spent(response: httpx.Response) -> float | None:
    """The quota policy's ``spent``: ``RATE_LIMIT_WAIT`` for a 403 on ``QUOTA_REASONS``.

    Upstream answers a spent per-minute limit with HTTP 403 and the reason
    ``rateLimitExceeded`` or ``userRateLimitExceeded``, no quota header and no
    ``Retry-After`` (``handle-errors.md``; F8), so the policy reads that answer (ADR-0015);
    ``None`` for every other response.
    """
    if response.status_code == 403 and _reason(response) in QUOTA_REASONS:
        return RATE_LIMIT_WAIT
    return None


def _scope_refusal(response: httpx.Response, auth: RefreshTokenAuth | None, where: str) -> str:
    """The re-consent message for upstream's refusal of a call the grant does not cover."""
    name = auth.account if auth is not None else "<name>"
    granted = auth.granted_scopes if auth is not None else None
    granted_text = (
        ", ".join(sorted(short_scope(scope) for scope in granted))
        if granted
        else "scopes this process has not seen"
    )
    accepted = _accepted_scopes(response)
    wanted = scope_set_for(accepted)
    head = f"{where} answered HTTP 403 insufficientPermissions: account {name} was granted"
    if wanted is None:
        listed = ", ".join(short_scope(scope) for scope in accepted) or "none named"
        return (
            f"{head} {granted_text}; this call takes one of {listed}, which no scope set of "
            f"{CONSENT_COMMAND} grants"
        )
    needs = ", ".join(short_scope(scope) for scope in SCOPE_SETS[wanted])
    return (
        f"{head} {granted_text}; this tool needs {needs}; re-run {CONSENT_COMMAND} "
        f"--account {name} --scopes {wanted}"
    )


def classify(
    response: httpx.Response, *, auth: RefreshTokenAuth | None = None
) -> XippiosError | None:
    """Gmail's documented error answers as taxonomy errors, else ``None`` (conventions §6).

    The ``ClientConfig.classify`` hook: it sees every response before core's status
    mapping. Upstream's JSON error body names a ``reason`` in ``error.errors[0]``
    (``docs/upstream/pages/handle-errors.md``):

    - HTTP 403 ``rateLimitExceeded`` or ``userRateLimitExceeded``: ``RateLimitError``
      with ``retry_after`` ``RATE_LIMIT_WAIT`` (60 s), the per-minute quota's window;
      the quota policy marks the account's quota spent for as long (``quota_spent``).
    - HTTP 403 ``dailyLimitExceeded``: ``RateLimitError`` with ``retry_after``
      ``DAILY_LIMIT_WAIT`` (3,600 s), the project's limit.
    - HTTP 403 ``domainPolicy``: ``AuthError``, the domain's administrators do not let
      the app reach Gmail.
    - HTTP 403 ``insufficientPermissions``, a call the account's grant does not cover:
      ``AuthError`` naming the scopes the account was granted and the scope set to
      re-consent with, ``...: account <name> was granted gmail.readonly; this tool needs
      gmail.modify; re-run xippios-google-mail-auth --account <name> --scopes write``
      (ADR-0025, D-c). The shape is the one recorded on 2026-10-29 (F19,
      ``tests/fixtures/messages_modify/insufficient_permissions.json``), not the 401
      ``authError`` the error page folds it into: ``status`` ``PERMISSION_DENIED`` and a
      ``WWW-Authenticate: Bearer error="insufficient_scope", scope="..."`` header listing
      the scopes the method accepts; the set to re-run with is the narrowest of
      ``SCOPE_SETS`` among them (``gmail.modify``, ``write``, for a write).
    - HTTP 401 ``authError``, "Invalid Credentials": ``AuthError`` naming the account.
      ``RefreshTokenAuth`` has refreshed the access token once on the first 401 and sent
      the request again, so this is the second refusal.

    A 429 keeps core's mapping, ``RateLimitError`` with upstream's ``Retry-After`` when
    it sends one; without one the quota policy holds the account's requests back for its
    ``QUOTA_WINDOW`` (60 s), the fallback. Everything else falls through (``None``): a 401
    or 403 of another reason is core's ``AuthError``, a 404 its ``NotFoundError``. Messages
    name the method and the path, never the host or the query (conventions §5), and the
    account by name, never a token.

    The granted scopes come from the account's auth: each account's client is built on
    ``account_config(auth)``, whose ``classify`` is this function bound to that client's
    ``RefreshTokenAuth`` (``functools.partial(classify, auth=auth)``), so the scopes named
    are those of the refresh answer whose access token the refused request carried
    (``RefreshTokenAuth.granted_scopes``). Called without ``auth``, the message names
    ``<name>`` and no scopes.

    Args:
        response: A response, read in full.
        auth: The account's auth, bound by ``account_config``.
    """
    status = response.status_code
    if status not in (401, 403):
        return None
    reason = _reason(response)
    where = f"{response.request.method} {response.request.url.path}"
    named = f"account {auth.account}" if auth is not None else "the account"
    if status == 403 and reason in QUOTA_REASONS:
        return RateLimitError(
            f"{where} answered HTTP 403 {reason}: {named} spent Gmail's per-minute quota; "
            f"retry in {RATE_LIMIT_WAIT:g} s",
            retry_after=RATE_LIMIT_WAIT,
            source=SOURCE,
        )
    if status == 403 and reason == "dailyLimitExceeded":
        return RateLimitError(
            f"{where} answered HTTP 403 dailyLimitExceeded: the project's Gmail quota is spent; "
            f"retry in {DAILY_LIMIT_WAIT:g} s or raise the quota in the Cloud project",
            retry_after=DAILY_LIMIT_WAIT,
            source=SOURCE,
        )
    if status == 403 and reason == "domainPolicy":
        return AuthError(
            f"{where} answered HTTP 403 domainPolicy: the policy of {named}'s domain does "
            "not let this app reach Gmail; its administrator has to allow the app",
            status=status,
            source=SOURCE,
        )
    if status == 403 and reason == "insufficientPermissions":
        return AuthError(_scope_refusal(response, auth, where), status=status, source=SOURCE)
    if status == 401 and reason == "authError":
        name = auth.account if auth is not None else "<name>"
        return AuthError(
            f"{where} answered HTTP 401 authError: Gmail refused {named}'s access token, "
            f"also after one refresh; if the account's grant was revoked, re-run "
            f"{CONSENT_COMMAND} --account {name} and set {TENANTS.var(name, 'REFRESH_TOKEN')}",
            status=status,
            source=SOURCE,
        )
    return None


config = ClientConfig(
    source=SOURCE,
    version=__version__,
    base_url=BASE_URL,
    rate_limit=RATE_LIMIT,
    cache=None,
    classify=classify,
)
"""The configuration every account's client starts from (``account_config``).

No response cache: answers are mailbox data, fetched when asked for.
"""


def account_config(auth: RefreshTokenAuth) -> ClientConfig:
    """``config`` for the account ``auth`` signs for: its ``classify`` and its quota policy.

    ``classify`` is bound to ``auth``, so the scope refusal names that account's granted
    scopes. A ``QuotaPolicy`` of its own is installed (ADR-0010, decisions 1 and 4;
    ADR-0015): no quota header is read (upstream sends none, F8), ``spent`` reads the 403
    of a spent per-minute quota (``quota_spent``) and ``window`` is ``QUOTA_WINDOW``, so
    an account request in that minute waits out what is left of it when that is within
    the policy's ``pause_cap`` (30 s), and is otherwise refused with ``RateLimitError``
    without sending. The policy sees the
    responses of the account's Gmail requests; the token request runs inside the auth
    flow and is neither held back nor observed (``RefreshTokenAuth``).
    """
    policy = QuotaPolicy(
        limit_header=None,
        remaining_header=None,
        window=QUOTA_WINDOW,
        spent=quota_spent,
        source=SOURCE,
    )
    return policy.install(
        dataclasses.replace(config, classify=functools.partial(classify, auth=auth))
    )


def refresh_auth(account: str, *, key: str | None = None) -> RefreshTokenAuth:
    """The renewing bearer of ``account``, on ``GOOGLE_<NAME>_REFRESH_TOKEN`` (or ``key``)."""
    return RefreshTokenAuth(
        TENANTS.var(account, "REFRESH_TOKEN"), account=account, key=key, source=SOURCE
    )


def _build(account: str) -> AsyncClient:
    """A new client for ``account``: its auth, its classify, its quota policy, its bucket.

    Called by ``TENANTS.client_for`` on first use and whenever the account's refresh
    token changed; the refresh token is read on the first request.
    """
    auth = refresh_auth(account)
    return AsyncClient(account_config(auth), auth=auth)


def client_for(account: str) -> AsyncClient:
    """The configured client of ``account``, built on first use and then reused.

    Each account has its own client: its renewing bearer, its token bucket
    (``RATE_LIMIT``), its quota policy and no response cache. The refresh token is read
    from ``GOOGLE_<NAME>_REFRESH_TOKEN`` on the first request, so a declared account whose
    variable was emptied fails then, with ``ConfigError``. The registry keeps the client
    under a digest of that variable: when the environment changes the refresh token, the
    next call builds a new client and closes the one it replaces
    (``Tenants.client_for``). ``TENANTS.reset()`` drops every cached client.

    Raises:
        InvalidRequestError: ``account`` is not declared.
        ConfigError: ``GOOGLE_ACCOUNTS`` is malformed.
    """
    return TENANTS.client_for(account, _build)
