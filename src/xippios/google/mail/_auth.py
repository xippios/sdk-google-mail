# Queue row 252: the renewing bearer, whose first consumer is sdk-faostat's LoginAuth and
# this refresh its second; core 0.4.0 takes a renewing-bearer base with the two token
# requests (ADR-0023, decision 2; ADR-0025, D-b). Until then this module is the source's.
"""The renewing bearer: an account's refresh token exchanged for an access token in memory.

ADR-0025, D-b. Every account has a long-lived refresh token, ``GOOGLE_<NAME>_REFRESH_TOKEN``,
issued to the operator's Desktop-app OAuth client when the account consented
(``xippios-google-mail-auth``). ``RefreshTokenAuth`` is the ``httpx.Auth`` of the account's
client: its key (``EnvKeyAuth.key``) is the refresh token, and its ``auth_flow`` exchanges
it at Google's token endpoint for a short-lived access token, which it sends as
``Authorization: Bearer``. The exchange is the refresh request of
``docs/upstream/pages/oauth2-web-server.md`` ("Refreshing an access token", HTTP/REST):
``POST https://oauth2.googleapis.com/token`` with the form fields ``client_id``,
``client_secret``, ``grant_type=refresh_token`` and ``refresh_token``; the secret is sent,
since Google's pages disagree on whether a Desktop client needs it (the investigation's
C2). The answer carries ``access_token``, ``expires_in``, ``scope`` and ``token_type``,
and, from a consent screen whose grants expire (an app in Testing status issues seven-day
grants), ``refresh_token_expires_in`` (F19, the probe of 2026-10-29); no new refresh token.

Nothing is written to disk: the access token, when it lapses and the granted scopes live
in the auth object's memory, one per account client (``_client.client_for``), and go with
it. The client id and secret are read from ``GOOGLE_CLIENT_ID`` and ``GOOGLE_CLIENT_SECRET``
at the moment a token request is built, by name, kept in no attribute and never logged.
Offline, core's testing plugin pins ``EnvKeyAuth.key`` to its placeholder, so the refresh
token a replayed exchange carries is the placeholder (conventions §10).

``SCOPE_SETS`` names the two grants ``xippios-google-mail-auth --scopes`` asks for:
``read``, ``gmail.readonly``, and ``write``, ``gmail.modify``, which serves every read
method too (``docs/upstream/pages/auth-scopes.md``; ADR-0025, D-c). Upstream enforces a
grant's scopes; ``_client.classify`` turns its refusal into the re-consent ``AuthError``,
naming the scopes this auth was granted.
"""

import os
import threading
import time
from collections.abc import Callable, Generator, Iterable

import httpx
import pydantic
from pydantic import BaseModel, ConfigDict, SecretStr
from xippios.core import (
    AuthError,
    ConfigError,
    UpstreamError,
    ValidationError,
    error_from_response,
)
from xippios.core.client.auth import EnvKeyAuth

__all__ = [
    "CLIENT_ID_ENV",
    "CLIENT_SECRET_ENV",
    "CONSENT_COMMAND",
    "EXPIRY_MARGIN",
    "SCOPE_SETS",
    "TOKEN_URL",
    "RefreshTokenAuth",
    "scope_set_for",
    "short_scope",
]

TOKEN_URL = "https://oauth2.googleapis.com/token"
"""Google's token endpoint: the refresh request goes here, and the consent's code exchange."""
CLIENT_ID_ENV = "GOOGLE_CLIENT_ID"
"""The variable holding the operator's Desktop-app OAuth client id, shared by every account."""
# The variable's name, not a value: detect-secrets reads the assignment as one.
CLIENT_SECRET_ENV = "GOOGLE_CLIENT_SECRET"  # pragma: allowlist secret
"""The variable holding that client's secret, sent with every token request (C2)."""
CONSENT_COMMAND = "xippios-google-mail-auth"
"""The consent tool that issues a refresh token (``_consent``), named in the messages."""
EXPIRY_MARGIN = 60.0
"""Seconds before an access token's ``expires_in`` runs out that the auth refreshes it."""
SCOPE_PREFIX = "https://www.googleapis.com/auth/"
"""The prefix of Google's scope URLs, left out where a message names a scope."""
SCOPE_SETS: dict[str, tuple[str, ...]] = {
    "read": ("https://www.googleapis.com/auth/gmail.readonly",),
    "write": ("https://www.googleapis.com/auth/gmail.modify",),
}
"""The grants ``xippios-google-mail-auth --scopes`` asks for, narrowest first (ADR-0025, D-c).

``gmail.modify`` serves every read method too (``docs/upstream/pages/auth-scopes.md``):
"Read, compose, and send emails from your Gmail account".
"""


def short_scope(scope: str) -> str:
    """``https://www.googleapis.com/auth/gmail.modify`` → ``gmail.modify``; others as given."""
    return scope.removeprefix(SCOPE_PREFIX)


def scope_set_for(accepted: Iterable[str]) -> str | None:
    """The narrowest of ``SCOPE_SETS`` whose scopes are all among ``accepted``, else ``None``.

    Args:
        accepted: The scopes a method accepts, e.g. those upstream's
            ``WWW-Authenticate: Bearer error="insufficient_scope", scope="..."`` lists.
    """
    offered = set(accepted)
    for name, scopes in SCOPE_SETS.items():
        if set(scopes) <= offered:
            return name
    return None


def _credential(name: str, source: str | None) -> str:
    """The value of the client variable ``name``; an empty one counts as unset.

    Raises:
        ConfigError: The variable is unset or empty; the message names both client
            variables, never a value.
    """
    value = os.environ.get(name, "").strip()
    if value:
        return value
    raise ConfigError(
        f"{name} is not set; export the operator's Desktop-app OAuth client as "
        f"{CLIENT_ID_ENV} and {CLIENT_SECRET_ENV}",
        source=source,
    )


class _TokenAnswer(BaseModel):
    """The token endpoint's HTTP 200, the fields kept (``oauth2-web-server.md``; F19).

    ``id_token`` and ``refresh_token``, which a refresh does not return (F19), are
    ignored if they ever come; the access token is a ``SecretStr``, whose ``repr`` is
    masked.
    """

    model_config = ConfigDict(frozen=True, extra="ignore")

    access_token: SecretStr
    expires_in: int
    scope: str
    token_type: str
    refresh_token_expires_in: int | None = None


def _oauth_error(response: httpx.Response) -> str | None:
    """The ``error`` code of the token endpoint's JSON error answer, e.g. ``invalid_grant``."""
    try:
        payload = response.json()
    except ValueError:
        return None
    code = payload.get("error") if isinstance(payload, dict) else None
    return code if isinstance(code, str) else None


class RefreshTokenAuth(EnvKeyAuth):
    """Exchange an account's refresh token for an access token, send it, renew it in time.

    The ``httpx.Auth`` of one account's client (ADR-0025, D-b; queue row 252). Its key
    is the refresh token, read from ``env_var`` (``GOOGLE_<NAME>_REFRESH_TOKEN``) on the
    first request, or given as ``key``. When it holds no access token, or the one it
    holds expires within ``EXPIRY_MARGIN`` (60 s) of its ``expires_in``, its
    ``auth_flow`` sends the refresh request (``token_request``) ahead of the request it
    signs and keeps the answer (``keep``): the access token, when it lapses (monotonic)
    and the granted ``scope`` set, under a lock, in memory only. Every request goes out
    with ``Authorization: Bearer <access token>``. A request answered with HTTP 401
    refreshes once more and goes out again, once; the answer to that one goes back to
    the client, whose ``classify`` maps a second 401 to ``AuthError``. Two requests that
    start together before the first refresh has answered may each refresh.

    The exchange runs inside the request's ``httpx`` auth flow, below core's limiter,
    hooks, cache and ``classify``: a refresh is not paced, not seen by the quota policy
    and not retried, and an error this flow raises (``AuthError``, ``ConfigError``,
    ``ValidationError`` or core's mapping of another answer) ends the call. The token
    endpoint costs no Gmail quota. ``requires_response_body`` is set, so httpx reads every
    answer of the flow in full before the flow sees it, the token answer among them: no
    endpoint this package calls streams.

    Args:
        env_var: The variable holding the refresh token, e.g.
            ``"GOOGLE_DEMO_REFRESH_TOKEN"``; ``TENANTS.var(name, "REFRESH_TOKEN")``.
        account: The account's name, e.g. ``"demo"``, which the messages name.
        key: The refresh token itself, winning over ``env_var`` (conventions §7); the
            fixture recorder's ``--garbage-token`` passes a nonsense one.
        source: Source slug for the errors, e.g. ``"google-mail"``.
        clock: Monotonic seconds, replaceable in tests.

    Attributes:
        account: As given.

    Raises:
        ConfigError: From a request: ``env_var``, ``GOOGLE_CLIENT_ID`` or
            ``GOOGLE_CLIENT_SECRET`` is unset or empty, or the token endpoint refused the
            client (HTTP 401 ``invalid_client``); the message names the variables.
        AuthError: From a request: the token endpoint answered HTTP 400 ``invalid_grant``,
            a refresh token revoked, expired or never valid; the message names the
            account and the re-run of the consent tool.
        ValidationError: From a request: the token endpoint's HTTP 200 is not the
            documented answer; nothing of it is quoted.
        XippiosError: From a request, for any other answer of the token endpoint: core's
            mapping (``RateLimitError`` for a 429, ``UpstreamError`` for a 5xx).
    """

    requires_response_body = True

    def __init__(
        self,
        env_var: str,
        *,
        account: str,
        key: str | SecretStr | None = None,
        source: str | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        super().__init__(env_var, key=key, source=source)
        self.account = account
        self._clock = clock
        self._state = threading.Lock()
        self._access_token: SecretStr | None = None
        self._lapses_at = 0.0
        self._granted: frozenset[str] | None = None
        self._refresh_token_expires_in: int | None = None

    @property
    def granted_scopes(self) -> frozenset[str] | None:
        """The scopes the last refresh answer granted (its ``scope``), ``None`` before one."""
        with self._state:
            return self._granted

    @property
    def refresh_token_expires_in(self) -> int | None:
        """The last answer's ``refresh_token_expires_in`` in seconds, if it carried one.

        Google sends it when the refresh token itself expires: a grant of a consent
        screen in Testing status lives seven days (F19). Once it has run out the next
        refresh answers ``invalid_grant``, whose message says what to re-run.
        """
        with self._state:
            return self._refresh_token_expires_in

    def reset(self) -> None:
        """Forget the access token, so that the next request refreshes it."""
        with self._state:
            self._access_token = None
            self._lapses_at = 0.0

    def _current(self) -> SecretStr | None:
        """The access token while it is outside the margin of its expiry, else ``None``."""
        with self._state:
            if self._access_token is not None and self._clock() < self._lapses_at:
                return self._access_token
            return None

    def token_form(self) -> dict[str, str]:
        """The refresh request's form fields, read at this moment; never log or keep it.

        ``client_id`` and ``client_secret`` from ``GOOGLE_CLIENT_ID`` and
        ``GOOGLE_CLIENT_SECRET``, ``grant_type`` ``refresh_token`` and ``refresh_token``,
        the key, in the order the guide lists them. The fixture recorder sends it for
        ``oauth2_token``; the flow sends it through ``token_request``.

        Raises:
            ConfigError: A client variable or the refresh token's variable is unset.
        """
        return {
            "client_id": _credential(CLIENT_ID_ENV, self.source),
            "client_secret": _credential(CLIENT_SECRET_ENV, self.source),
            "grant_type": "refresh_token",
            "refresh_token": self.key(),
        }

    def token_request(self, request: httpx.Request | None = None) -> httpx.Request:
        """The refresh request, ahead of ``request``, whose ``User-Agent`` it sends."""
        headers = {"Accept": "application/json"}
        if request is not None and "user-agent" in request.headers:
            headers["User-Agent"] = request.headers["user-agent"]
        return httpx.Request("POST", TOKEN_URL, data=self.token_form(), headers=headers)

    def keep(self, response: httpx.Response) -> None:
        """Keep the access token, when it lapses and the granted scopes from a refresh answer.

        Args:
            response: The token endpoint's answer, read in full.

        Raises:
            AuthError: HTTP 400 ``invalid_grant``: the refresh token was revoked,
                expired (unused for six months, or a seven-day grant of an app in
                Testing status) or never valid; the message names the account, the
                consent tool's re-run and the variable to set.
            ConfigError: HTTP 401 ``invalid_client``: the token endpoint refused the
                OAuth client; the message names ``GOOGLE_CLIENT_ID`` and
                ``GOOGLE_CLIENT_SECRET``.
            ValidationError: An HTTP 200 that is not the documented answer.
            XippiosError: Any other answer, by core's mapping (``error_from_response``),
                or ``UpstreamError`` for another status below 400.
        """
        status = response.status_code
        where = f"{response.request.method} {response.request.url.path}"
        if status == 200:
            try:
                answer = _TokenAnswer.model_validate_json(response.content)
            except pydantic.ValidationError:
                # Raised without pydantic's text, which would quote the token it read.
                raise ValidationError(
                    f"{where} answered HTTP 200, but not the documented token answer "
                    "(access_token, expires_in, scope, token_type); the answer is not shown",
                    raw="",
                    source=self.source,
                ) from None
            lapses_at = self._clock() + max(0.0, answer.expires_in - EXPIRY_MARGIN)
            with self._state:
                self._access_token = answer.access_token
                self._lapses_at = lapses_at
                self._granted = frozenset(answer.scope.split())
                self._refresh_token_expires_in = answer.refresh_token_expires_in
            return
        code = _oauth_error(response)
        if status == 400 and code == "invalid_grant":
            raise AuthError(
                f"account {self.account}: refresh token revoked or expired; re-run "
                f"{CONSENT_COMMAND} --account {self.account} and set {self.env_var}",
                status=status,
                source=self.source,
            )
        if status == 401 and code == "invalid_client":
            raise ConfigError(
                f"the token endpoint refused the OAuth client (HTTP 401 invalid_client from "
                f"{where}); check that {CLIENT_ID_ENV} and {CLIENT_SECRET_ENV} hold the "
                "operator's Desktop-app client id and secret",
                source=self.source,
            )
        error = error_from_response(response, source=self.source)
        if error is None:
            error = UpstreamError(
                f"{where} answered HTTP {status}, not the documented 200; nothing of it is shown",
                status=status,
                body="",
                source=self.source,
            )
        raise error

    def apply(self, request: httpx.Request) -> None:
        """Attach the access token held as ``Authorization: Bearer``; never the refresh token."""
        token = self._current()
        if token is None:  # pragma: no cover - auth_flow refreshes or raises first
            raise AuthError(
                f"account {self.account}: no access token to send", status=401, source=self.source
            )
        request.headers["Authorization"] = f"Bearer {token.get_secret_value()}"

    def auth_flow(self, request: httpx.Request) -> Generator[httpx.Request, httpx.Response, None]:
        """The httpx hook: refresh without a current token, sign, refresh once more on 401."""
        if self._current() is None:
            answer = yield self.token_request(request)
            self.keep(answer)
        self.apply(request)
        response = yield request
        if response.status_code == 401:
            answer = yield self.token_request(request)
            self.keep(answer)
            self.apply(request)
            yield request
