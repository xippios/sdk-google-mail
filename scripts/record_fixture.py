"""Record one exchange of the demo account into a fixture (``/record-fixture``).

Run from the repository root with demo's refresh token and the OAuth client pair
(``GOOGLE_DEMO_REFRESH_TOKEN``, ``GOOGLE_CLIENT_ID``, ``GOOGLE_CLIENT_SECRET``) in the
environment, which reach a process only through ``direnv exec`` from the family's root.
With ``R`` for ``direnv exec ~/github/sdk uv run scripts/record_fixture.py``::

    R oauth2_token ok
    R oauth2_token invalid_grant --garbage-token
    R oauth2_token invalid_client --garbage-client
    R get_profile ok
    R get_profile unauthenticated --garbage-bearer
    R messages_list one maxResults=1
    R messages_modify insufficient_permissions id=<a message id>

``<operation>`` names the wrapper the fixture belongs to, the directory of conventions
§10: the fixture lands at ``tests/fixtures/<operation>/<case>.json``. ``OPERATIONS`` maps
each Gmail operation to its method and path under ``BASE_URL`` and counts its quota units
(``docs/upstream/pages/quota.md``); ``oauth2_token`` is the refresh request of
``RefreshTokenAuth`` (``token_form``), sent to ``https://oauth2.googleapis.com/token`` as
the auth sends it, which costs no Gmail quota. ``key=value`` arguments are a GET's query
parameters, named as upstream names them; ``id=`` fills the path of
``messages_modify``, which sends an empty JSON body, ``{}``, and nothing else: under a
grant without ``gmail.modify`` upstream refuses it (the scope refusal, F19), and under one
with it, an empty modify changes no label.

The account is not a parameter. The recorder removes ``GOOGLE_ACCOUNTS`` from its own
environment and records through ``client_for("demo")``'s configuration only, so it cannot
reach another mailbox, in a client of its own that never retries
(``RetryPolicy(max_attempts=1)``): a 5xx is recorded as it came and spends no quota on a
retry. Three modes send a nonsense value and spend no quota:

- ``--garbage-token`` (``oauth2_token``): the refresh request with ``GARBAGE_TOKEN`` for the
  refresh token and the real client pair, upstream's ``invalid_grant``;
- ``--garbage-client`` (``oauth2_token``): the refresh request with nonsense for the client
  id, the client secret and the refresh token, so no real value is sent, upstream's
  ``invalid_client``;
- ``--garbage-bearer`` (a Gmail operation): the request with ``Authorization: Bearer``
  ``GARBAGE_BEARER``, no refresh, upstream's 401 ``authError``.

Nothing unscrubbed touches disk. The request goes through the real client with
``raise_on_error=False``, and core's ``fixture_from_response`` turns the exchange into a
fixture in memory with ``Scrub(secrets=[...])`` naming demo's refresh token, the client
secret and the client id, read by name from the environment here and never printed, and
every token the answer carries (conventions §10): credentials scrubbed, those values made
``REDACTED`` where they appear as given, and a plain verify that refuses the exchange when
one is still there as given. Then the recorder's own step (``rewrite``, ADR-0024 decision
4) runs on the ``Fixture`` core returns:

- the same values in their percent-encoded and JSON-escaped forms (a refresh token's
  ``/`` is ``%2F`` in a form body) become ``REDACTED``;
- a form request body's ``client_id``, ``client_secret``, ``refresh_token``, ``code`` and
  ``code_verifier`` become ``REDACTED`` by key, whatever they held;
- the token answer's ``access_token``, ``id_token`` and ``refresh_token`` become
  ``REDACTED`` by key;
- every e-mail address, in the URL, the response headers and both bodies, ``@`` plain or
  ``%40``, becomes ``user@example.com``, the profile's ``emailAddress`` among them.

The investigation's §2.7 list comes with the message tasks (``Message-ID``, ``In-Reply-To``
and ``References`` to ``<n@example.com>``, ``payload.headers`` cut to an allowlist, every
``data`` decoded, scrubbed and re-encoded, ``attachmentId`` to ``att-<n>``): they extend
``TEXT_REWRITES`` and ``rewrite``. Last, before the one write, ``check`` searches the
fixture's text and every decoded string of a JSON body for what must never be written:
the secret values in any of those forms, Google's token shapes (``ya29.``, ``1//``,
``GOCSPX-``, ``apps.googleusercontent.com``), an address other than ``user@example.com``,
and a token field whose value is not ``REDACTED``. Any of them means nothing is written
and the exit status is 1; a refusal names what, never a value. Only the status, the
method, the scrubbed URL and the quota units are printed.
"""

import argparse
import dataclasses
import json
import os
import re
import sys
from collections.abc import Callable, Generator, Iterator, Sequence
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, quote, quote_plus, urlencode

import httpx
from xippios.core import AsyncClient, RetryPolicy, sync
from xippios.core.testing import REDACTED, Fixture, Scrub, fixture_from_response, fixture_path

from xippios.google.mail._auth import (
    CLIENT_ID_ENV,
    CLIENT_SECRET_ENV,
    TOKEN_URL,
    RefreshTokenAuth,
)
from xippios.google.mail._client import DEMO, TENANTS, account_config, client_for, refresh_auth

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures"
TOKEN = "oauth2_token"
"""The fixture directory of the refresh request, ``POST https://oauth2.googleapis.com/token``."""
# Deliberately invalid values, no credentials: detect-secrets reads the assignments as ones.
GARBAGE_TOKEN = "not-a-refresh-token"  # pragma: allowlist secret
GARBAGE_CLIENT_ID = "not-a-client-id"
GARBAGE_CLIENT_SECRET = "not-a-client-secret"  # pragma: allowlist secret
GARBAGE_BEARER = "not-an-access-token"  # pragma: allowlist secret
_CASE = re.compile(r"[a-z0-9][a-z0-9_]*")
_ID = re.compile(r"[A-Za-z0-9_-]+")


@dataclasses.dataclass(frozen=True)
class Operation:
    """A Gmail operation the recorder may send.

    Attributes:
        method: ``GET`` or ``POST``.
        path: The path under ``BASE_URL``, ``{id}`` filled from ``id=``.
        units: Quota units a call costs (``quota.md``), printed after it.
        body: The JSON body of a POST, sent as given; ``None`` for a GET.
    """

    method: str
    path: str
    units: int
    body: dict[str, Any] | None = None

    @property
    def path_params(self) -> tuple[str, ...]:
        """The ``{name}`` parameters of ``path``, e.g. ``("id",)``."""
        return tuple(re.findall(r"\{(\w+)\}", self.path))


OPERATIONS = {
    "get_profile": Operation("GET", "profile", units=1),
    "messages_list": Operation("GET", "messages", units=5),
    "messages_modify": Operation("POST", "messages/{id}/modify", units=5, body={}),
}
"""The Gmail operations, by the wrapper (fixture directory) they belong to."""

SCRUBBED_EMAIL = "user@example.com"
EMAIL = re.compile(r"[\w.+-]+(?P<at>@|%40)[\w-]+(?:\.[\w-]+)*\.[A-Za-z]{2,}")
"""An e-mail-shaped string, ``@`` plain or percent-encoded (a query string)."""
TOKEN_FIELDS = ("access_token", "id_token", "refresh_token")
"""The token answer's fields whose value is never written."""
_TOKEN_FIELD = re.compile(
    r'(?P<field>"(?:' + "|".join(TOKEN_FIELDS) + r')"\s*:\s*)"(?:[^"\\]|\\.)*"'
)
FORM_SECRETS = ("client_id", "client_secret", "refresh_token", "code", "code_verifier")
"""A form request body's fields whose value is never written, whatever it is."""
TOKEN_SHAPES = {
    "ya29.": "an access token's shape (ya29.)",
    "1//": "a refresh token's shape (1//)",
    "GOCSPX-": "a client secret's shape (GOCSPX-)",
    "apps.googleusercontent.com": "a client id's shape (apps.googleusercontent.com)",
}
"""Google's credential shapes, refused wherever they appear."""


def parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    """The command line: operation, case, parameters and the modes."""
    parser = argparse.ArgumentParser(
        prog="record_fixture.py",
        description="Record one exchange of the demo account as a test fixture.",
    )
    parser.add_argument(
        "operation", help=f"{TOKEN} or a Gmail operation: {', '.join(sorted(OPERATIONS))}"
    )
    parser.add_argument("case", help="fixture name, e.g. ok")
    parser.add_argument(
        "params", nargs="*", metavar="key=value", help="query parameters, or id= for a path"
    )
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument(
        "--garbage-token", action="store_true", help=f"{TOKEN}: a nonsense refresh token"
    )
    modes.add_argument(
        "--garbage-client",
        action="store_true",
        help=f"{TOKEN}: a nonsense client id, secret and refresh token",
    )
    modes.add_argument(
        "--garbage-bearer",
        action="store_true",
        help="a Gmail operation: a nonsense bearer and no refresh",
    )
    args = parser.parse_intermixed_args(argv)
    if args.operation != TOKEN and args.operation not in OPERATIONS:
        parser.error(f"operation {args.operation!r}: use {TOKEN} or one of {sorted(OPERATIONS)}")
    if not _CASE.fullmatch(args.case):
        parser.error(f"case {args.case!r}: use lower-case letters, digits and underscores")
    if any("=" not in item for item in args.params):
        parser.error("parameters are key=value")
    args.params = {key: value for key, _, value in (item.partition("=") for item in args.params)}
    if args.operation == TOKEN and (args.garbage_bearer or args.params):
        parser.error(f"{TOKEN} takes no parameters and no --garbage-bearer")
    if args.operation != TOKEN and (args.garbage_token or args.garbage_client):
        parser.error("--garbage-token and --garbage-client are for oauth2_token")
    operation = OPERATIONS.get(args.operation)
    if operation is not None:
        for name in operation.path_params:
            if not _ID.fullmatch(args.params.get(name, "")):
                parser.error(f"{args.operation} needs {name}=<an id of letters and digits>")
        if operation.method == "POST" and set(args.params) - set(operation.path_params):
            parser.error(f"{args.operation} sends an empty body and takes only its path's id")
    return args


def secret_values() -> list[str]:
    """The values never written, read by name from the environment; the unset ones left out."""
    names = (TENANTS.var(DEMO, "REFRESH_TOKEN"), CLIENT_SECRET_ENV, CLIENT_ID_ENV)
    return [value for value in (os.environ.get(name, "").strip() for name in names) if value]


def answer_tokens(response: httpx.Response) -> list[str]:
    """The token values a JSON answer carries under ``TOKEN_FIELDS``, never printed."""
    try:
        payload = response.json()
    except ValueError:
        return []
    if not isinstance(payload, dict):
        return []
    return [value for name in TOKEN_FIELDS if isinstance(value := payload.get(name), str) and value]


def forms_of(value: str) -> list[str]:
    """``value`` as given, percent-encoded (both ways) and JSON-escaped: the forms looked for."""
    return sorted({value, quote(value, safe=""), quote_plus(value), value.replace("/", "\\/")})


def _secret_pattern(secrets: Sequence[str]) -> re.Pattern[str] | None:
    forms = sorted({form for value in secrets for form in forms_of(value)}, key=len, reverse=True)
    return re.compile("|".join(re.escape(form) for form in forms)) if forms else None


def scrub_emails(text: str) -> str:
    """``text`` with every e-mail address made ``user@example.com`` (``%40`` kept as such)."""
    return EMAIL.sub(
        lambda match: SCRUBBED_EMAIL.replace("@", match["at"]) if match["at"] else SCRUBBED_EMAIL,
        text,
    )


def scrub_token_fields(text: str) -> str:
    """``text`` with the value of every ``TOKEN_FIELDS`` field in raw JSON made ``REDACTED``."""
    return _TOKEN_FIELD.sub(lambda match: f'{match["field"]}"{REDACTED}"', text)


TEXT_REWRITES: tuple[Callable[[str], str], ...] = (scrub_token_fields, scrub_emails)
"""The text rewrites of every location, after the secrets; the message tasks add theirs."""


def scrub_form(body: str) -> str:
    """A form body with ``FORM_SECRETS`` made ``REDACTED`` by key, the other fields as sent."""
    pairs = parse_qsl(body, keep_blank_values=True)
    return urlencode([(key, REDACTED if key in FORM_SECRETS else value) for key, value in pairs])


def rewrite(fixture: Fixture, secrets: Sequence[str]) -> Fixture:
    """The recorder's own step on core's ``Fixture``: the module docstring's list."""
    pattern = _secret_pattern(secrets)

    def text(value: str) -> str:
        if pattern is not None:
            value = pattern.sub(REDACTED, value)
        for step in TEXT_REWRITES:
            value = step(value)
        return value

    request = fixture.request
    body = request.body
    if body is not None and not request.body_base64:
        body = text(body)
        if (request.content_type or "").startswith("application/x-www-form-urlencoded"):
            body = scrub_form(body)
    response = fixture.response
    return fixture.model_copy(
        update={
            "request": request.model_copy(update={"url": text(request.url), "body": body}),
            "response": response.model_copy(
                update={
                    "headers": [(name, text(value)) for name, value in response.headers],
                    "body": response.body if response.body_base64 else text(response.body),
                }
            ),
        }
    )


def _strings(node: Any) -> Iterator[str]:
    """Every key and string of a decoded JSON value."""
    if isinstance(node, dict):
        for key, value in node.items():
            yield str(key)
            yield from _strings(value)
    elif isinstance(node, list):
        for item in node:
            yield from _strings(item)
    elif isinstance(node, str):
        yield node


def _token_fields_left(node: Any) -> Iterator[str]:
    """The ``TOKEN_FIELDS`` of a decoded JSON value whose value is not ``REDACTED``."""
    if isinstance(node, dict):
        for key, value in node.items():
            if key in TOKEN_FIELDS and value != REDACTED:
                yield key
            yield from _token_fields_left(value)
    elif isinstance(node, list):
        for item in node:
            yield from _token_fields_left(item)


def check(text: str, fixture: Fixture, secrets: Sequence[str]) -> list[str]:
    """What the fixture would leak, by name, never by value; empty when it may be written."""
    texts = [text]
    decoded: Any = None
    if not fixture.response.body_base64:
        try:
            decoded = json.loads(fixture.response.body)
        except ValueError:
            decoded = None
        texts.extend(_strings(decoded))
    leaks: list[str] = []
    pattern = _secret_pattern(secrets)
    if pattern is not None and any(pattern.search(item) for item in texts):
        leaks.append("a value of the refresh token, the client pair or an answer's token")
    for shape, what in TOKEN_SHAPES.items():
        if any(shape in item for item in texts):
            leaks.append(what)
    addresses = {match.group(0) for item in texts for match in EMAIL.finditer(item)}
    if addresses - {SCRUBBED_EMAIL, SCRUBBED_EMAIL.replace("@", "%40")}:
        leaks.append("an e-mail address other than user@example.com")
    left = sorted(set(_token_fields_left(decoded)))
    if left:
        leaks.append(f"a value under {', '.join(left)}")
    return leaks


_CUT_MARKS = ("truncated", "original_bytes", "original_count", "sha256")
"""The response fields that mark a cut body; core's writer leaves them out of one not cut."""


def to_json(fixture: Fixture) -> str:
    """The fixture as text, laid out as core's ``write_fixture`` lays it out (no cut here)."""
    data = fixture.model_dump(mode="json")
    if not fixture.response.truncated:
        for name in _CUT_MARKS:
            data["response"].pop(name, None)
    return json.dumps(data, indent=2, sort_keys=True) + "\n"


class _Bearer(httpx.Auth):
    """``Authorization: Bearer <token>`` and nothing else: ``--garbage-bearer``'s auth."""

    def __init__(self, token: str) -> None:
        self._token = token

    def auth_flow(self, request: httpx.Request) -> Generator[httpx.Request, httpx.Response, None]:
        """Send the request once with the bearer."""
        request.headers["Authorization"] = f"Bearer {self._token}"
        yield request


def recording_client(auth: httpx.Auth) -> AsyncClient:
    """The client the recorder sends through: the demo account's, never retrying.

    ``client_for("demo")`` checks that the account is declared; its configuration
    (``account_config``: base URL, pace, ``classify``, quota policy) is rebuilt on the
    recorder's own auth with ``RetryPolicy(max_attempts=1)``, since core's ``record`` takes
    no ``retry=``.
    """
    client_for(DEMO)
    signer = auth if isinstance(auth, RefreshTokenAuth) else refresh_auth(DEMO)
    config = account_config(signer)
    return AsyncClient(dataclasses.replace(config, retry=RetryPolicy(max_attempts=1)), auth=auth)


async def exchange_async(args: argparse.Namespace) -> tuple[httpx.Response, int]:
    """The one request of the recording, an error status returned; and its quota units."""
    if args.operation == TOKEN:
        auth = refresh_auth(
            DEMO, key=GARBAGE_TOKEN if args.garbage_token or args.garbage_client else None
        )
        client = recording_client(auth)
        form = auth.token_form()
        if args.garbage_client:
            form.update(client_id=GARBAGE_CLIENT_ID, client_secret=GARBAGE_CLIENT_SECRET)
        response = await client.request(
            "POST",
            TOKEN_URL,
            data=form,
            headers={"Accept": "application/json"},
            auth=None,
            raise_on_error=False,
        )
        return response, 0
    operation = OPERATIONS[args.operation]
    signer: httpx.Auth = _Bearer(GARBAGE_BEARER) if args.garbage_bearer else refresh_auth(DEMO)
    client = recording_client(signer)
    path = operation.path.format(**{name: args.params[name] for name in operation.path_params})
    query = {key: value for key, value in args.params.items() if key not in operation.path_params}
    response = await client.request(
        operation.method,
        path,
        params=query or None,
        json=operation.body,
        raise_on_error=False,
    )
    # A request without a valid bearer names no project to charge: no quota counted.
    return response, 0 if args.garbage_bearer else operation.units


exchange = sync(exchange_async)


def main(argv: Sequence[str] | None = None) -> int:
    """Record, scrub in memory, check, write; return the exit status."""
    args = parse_args(argv)
    os.environ.pop(TENANTS.list_var, None)  # the recorder sees the demo account only
    path = fixture_path(FIXTURES, args.operation, args.case)
    response, units = exchange(args)
    secrets = [*secret_values(), *answer_tokens(response)]
    try:
        fixture = rewrite(fixture_from_response(response, scrub=Scrub(secrets=secrets)), secrets)
    except ValueError as refused:  # names what and where, never a value
        print(f"not written {path.relative_to(ROOT)}: {refused}")  # noqa: T201
        return 1
    text = to_json(fixture)
    leaks = check(text, fixture, secrets)
    spent = f" ({units} quota unit{'s' if units != 1 else ''})" if units else ""
    if leaks:
        print(f"not written {path.relative_to(ROOT)}: it would contain " + "; ".join(leaks))  # noqa: T201
        print(f"sent: {fixture.request.method} {fixture.response.status}{spent}")  # noqa: T201
        return 1
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    print(f"{fixture.response.status} {fixture.request.method} {fixture.request.url}{spent}")  # noqa: T201
    return 0


if __name__ == "__main__":
    sys.exit(main())
