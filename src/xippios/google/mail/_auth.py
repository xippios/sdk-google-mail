"""The renewing bearer: an account's refresh token exchanged for an access token in memory.

ADR-0025, D-b. Every account has a long-lived refresh token, ``GOOGLE_<NAME>_REFRESH_TOKEN``,
issued to the operator's Desktop-app OAuth client when the account consented
(``xippios-google-mail-auth``). ``RefreshTokenAuth`` is the ``httpx.Auth`` of the account's
client, a subclass of core's ``RenewingBearerAuth``: its credential
(``RenewingBearerAuth.credential``) is the refresh token, and core's flow exchanges it at
Google's token endpoint for a short-lived access token, which it sends as
``Authorization: Bearer``. The exchange is the refresh request of
``docs/upstream/pages/oauth2-web-server.md`` ("Refreshing an access token", HTTP/REST):
``POST https://oauth2.googleapis.com/token`` with the form fields ``client_id``,
``client_secret``, ``grant_type=refresh_token`` and ``refresh_token``; the secret is sent,
since Google's pages disagree on whether a Desktop client needs it (the investigation's
C2). The answer carries ``access_token``, ``expires_in``, ``scope`` and ``token_type``, no
new refresh token, and may carry ``refresh_token_expires_in``, "The remaining lifetime of
the refresh token in seconds", which the guide says is "only set when the user grants
time-based access" (``docs/upstream/pages/oauth2-web-server.md``, its table of the token
answer, about line 946). ``docs/upstream/pages/oauth2.md`` (about line 311) gives a
consent screen in Testing status refresh tokens "expiring in 7 days"; that such a grant's
answer carries the field is F19's observation, the probe of 2026-10-29 under demo's grant.

Nothing is written to disk: the access token, when it lapses and the granted scopes live
in the auth object's memory, one per account client (``_client.client_for``), and go with
it. The client id and secret are read from ``GOOGLE_CLIENT_ID`` and ``GOOGLE_CLIENT_SECRET``
at the moment a token request is built, by name, kept in no attribute and never logged.
Offline, core's testing plugin pins ``RenewingBearerAuth.credential`` to its placeholder,
so every credential a replayed exchange carries is the placeholder (conventions §10).

``SCOPE_SETS`` names the two grants ``xippios-google-mail-auth --scopes`` asks for:
``read``, ``gmail.readonly``, and ``write``, ``gmail.modify``, which serves every read
method too (``docs/upstream/pages/auth-scopes.md``; ADR-0025, D-c). Upstream enforces a
grant's scopes; ``_client.classify`` turns its refusal into the re-consent ``AuthError``,
naming the scopes this auth was granted.
"""

import threading
import time
from collections.abc import Callable, Iterable

import httpx
import pydantic
from pydantic import BaseModel, ConfigDict, SecretStr
from xippios.core import (
    AuthError,
    BearerToken,
    ConfigError,
    RenewingBearerAuth,
    ValidationError,
    XippiosError,
    request_description,
)

__all__ = [
    "CLIENT_ID_ENV",
    "CLIENT_SECRET_ENV",
    "CONSENT_COMMAND",
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


class RefreshTokenAuth(RenewingBearerAuth):
    """Exchange an account's refresh token for an access token, send it, renew it in time.

    The ``httpx.Auth`` of one account's client (ADR-0025, D-b), on core's renewing-bearer
    base. Its credential is the refresh token, read from ``env_var``
    (``GOOGLE_<NAME>_REFRESH_TOKEN``) at each refresh, or given as ``key``. Core's flow
    sends the refresh request (``token_request``) when no access token is held, or the one
    held expires within ``margin`` (60 s) of its ``expires_in``, ahead of the request it
    signs, and keeps the access token and when it lapses; ``read_token`` keeps the granted
    ``scope`` set too, under a lock, in memory only. Every request goes out with
    ``Authorization: Bearer <access token>``. A request answered with HTTP 401 refreshes
    once more and goes out again, once; the answer to that one goes back to the client,
    whose ``classify`` maps a second 401 to ``AuthError``.

    The exchange runs inside the request's ``httpx`` auth flow, below core's limiter,
    hooks, cache and ``classify``: a refresh is not paced, not seen by the quota policy
    and not retried, and an error the flow raises (``AuthError``, ``ConfigError``,
    ``ValidationError`` or core's error for another answer) ends the call. The token
    endpoint costs no Gmail quota.

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
        env_var: As given.

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
            error for its status (``RateLimitError`` for a 429, ``UpstreamError`` for a
            5xx), which quotes nothing of the answer.
    """

    def __init__(
        self,
        env_var: str,
        *,
        account: str,
        key: str | SecretStr | None = None,
        source: str | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.env_var = env_var
        super().__init__(
            source=source, clock=clock, credentials=None if key is None else {env_var: key}
        )
        self.account = account
        self._state = threading.Lock()
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

        The guide documents it for time-based access (``oauth2-web-server.md``); F19 saw
        it on demo's grant, whose consent screen is in Testing status, where refresh
        tokens expire in seven days (``oauth2.md``). Once it has run out the next refresh
        answers ``invalid_grant``, whose message says what to re-run.
        """
        with self._state:
            return self._refresh_token_expires_in

    def credential_help(self, name: str) -> str:
        """What the ``ConfigError`` of an unset ``name`` says after ``"<name> is not set; "``."""
        if name in (CLIENT_ID_ENV, CLIENT_SECRET_ENV):
            return (
                f"export the operator's Desktop-app OAuth client as {CLIENT_ID_ENV} and "
                f"{CLIENT_SECRET_ENV}"
            )
        return "export it or pass the key explicitly"

    def token_form(self) -> dict[str, str]:
        """The refresh request's form fields, read at this moment; never log or keep it.

        ``client_id`` and ``client_secret`` from ``GOOGLE_CLIENT_ID`` and
        ``GOOGLE_CLIENT_SECRET``, ``grant_type`` ``refresh_token`` and ``refresh_token``,
        the refresh token, in the order the guide lists them, each read through
        ``credential``. The fixture recorder sends it for ``oauth2_token``; the flow sends
        it through ``token_request``.

        Raises:
            ConfigError: A client variable or the refresh token's variable is unset.
        """
        return {
            "client_id": self.credential(CLIENT_ID_ENV),
            "client_secret": self.credential(CLIENT_SECRET_ENV),
            "grant_type": "refresh_token",
            "refresh_token": self.credential(self.env_var),
        }

    def token_request(self, request: httpx.Request | None = None) -> httpx.Request:
        """The refresh request, sent ahead of ``request`` with its ``User-Agent`` by core."""
        return httpx.Request(
            "POST", TOKEN_URL, data=self.token_form(), headers={"Accept": "application/json"}
        )

    def read_token(self, response: httpx.Response) -> BearerToken:
        """The access token and its lifetime from a refresh answer; the granted scopes kept.

        Args:
            response: The token endpoint's HTTP 200, read in full.

        Raises:
            ValidationError: An HTTP 200 that is not the documented answer; nothing of it
                is quoted.
        """
        try:
            answer = _TokenAnswer.model_validate_json(response.content)
        except pydantic.ValidationError:
            # Raised without pydantic's text, which would quote the token it read.
            raise ValidationError(
                f"{request_description(response)} answered HTTP 200, but not the documented "
                "token answer (access_token, expires_in, scope, token_type); the answer is not "
                "shown",
                raw="",
                source=self.source,
            ) from None
        with self._state:
            self._granted = frozenset(answer.scope.split())
            self._refresh_token_expires_in = answer.refresh_token_expires_in
        return BearerToken(answer.access_token, answer.expires_in)

    def token_refused(self, response: httpx.Response) -> XippiosError:
        """The error for a token answer other than HTTP 200, read in full.

        Returns:
            ``AuthError`` for HTTP 400 ``invalid_grant``: the refresh token was revoked,
            expired (unused for six months, or a seven-day grant of an app in Testing
            status, ``oauth2.md``) or never valid; the message names the account, the
            consent tool's re-run and the variable to set. ``ConfigError`` for HTTP 401
            ``invalid_client``: the token endpoint refused the OAuth client; the message
            names ``GOOGLE_CLIENT_ID`` and ``GOOGLE_CLIENT_SECRET``. Core's error for its
            status otherwise, which quotes nothing of the answer.
        """
        status = response.status_code
        code = _oauth_error(response)
        if status == 400 and code == "invalid_grant":
            return AuthError(
                f"account {self.account}: refresh token revoked or expired; re-run "
                f"{CONSENT_COMMAND} --account {self.account} and set {self.env_var}",
                status=status,
                source=self.source,
            )
        if status == 401 and code == "invalid_client":
            return ConfigError(
                f"the token endpoint refused the OAuth client (HTTP 401 invalid_client from "
                f"{request_description(response)}); check that {CLIENT_ID_ENV} and "
                f"{CLIENT_SECRET_ENV} hold the operator's Desktop-app client id and secret",
                source=self.source,
            )
        return super().token_refused(response)
