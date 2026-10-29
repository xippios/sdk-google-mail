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
    R list_labels ok
    R list_messages ok max_results=2
    R list_messages query q=newer_than:1y
    R list_messages label label_ids=INBOX
    R search_messages ok q=newer_than:1y max_results=2
    R get_message ok message_id=<a message id>
    R get_thread ok thread_id=<its thread id>

``<operation>`` names the wrapper the fixture belongs to, the directory of conventions
§10: the fixture lands at ``tests/fixtures/<operation>/<case>.json``. A read task's name
in its place (``TASKS``) records through the task itself: ``key=value`` arguments are the
task's own parameters (``q``, ``max_results``, ``message_id``; a list parameter such as
``label_ids`` repeated, a flag ``true`` or ``false``), the account is demo, and every
Gmail exchange the task makes is written under the task's name (conventions §10: a
fixture recorded through a task lives under the task's name), the first as
``<task>/<case>.json`` and the k-th, from the second on, as ``<task>/<case>_<k>.json``:
``search_messages ok`` writes the list as ``ok.json`` and the two ``format=metadata``
calls as ``ok_2.json`` and ``ok_3.json``. The task's client is the recorder's: it is put
into the registry as demo's (``TENANTS.client_for(DEMO, ...)``), so the task's
``client_for("demo")`` returns it, and its ``observe`` hook keeps each response. The
token request runs inside the auth flow, unseen by the hook; ``oauth2_token/ok.json``
replays it. ``OPERATIONS`` maps
each Gmail operation to its method and path under ``BASE_URL`` and counts its quota units
(``docs/upstream/pages/quota.md``); ``oauth2_token`` is the refresh request of
``RefreshTokenAuth`` (``token_form``), sent to ``https://oauth2.googleapis.com/token`` as
the auth sends it, which costs no Gmail quota. ``key=value`` arguments are a GET's query
parameters, named as upstream names them; ``id=`` fills the path of
``messages_modify``, which sends an empty JSON body, ``{}``, and nothing else: under a
grant without ``gmail.modify`` upstream refuses it (the scope refusal, F19), and under one
with it, an empty modify changes no label.

The account is not a parameter (``--account`` is no option and ``account=`` is refused).
The recorder removes ``GOOGLE_ACCOUNTS`` from its own
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

Before those text rewrites, ``MessageScrub`` reads a JSON answer as JSON, the
investigation's §2.7 list for messages and threads, numbering within the one fixture:

- every part's ``headers``, at any depth of the MIME tree, are cut to ``HEADER_ALLOWLIST``
  (From, To, Cc, Bcc, Date, Subject, Message-ID, In-Reply-To, References, Content-Type,
  MIME-Version, compared without regard to case), so ``Received``, ``ARC-*``,
  ``DKIM-Signature`` and ``X-Google-*`` with their hosts, addresses and signatures go;
- each ``<...>`` of a ``Message-ID``, ``In-Reply-To`` or ``References`` value becomes
  ``<n@example.com>``, the same id the same ``n`` (a reply's ``In-Reply-To`` names the
  number its parent's ``Message-ID`` got);
- every ``body.data`` is base64url decoded, its addresses made ``user@example.com``
  (bytes that are not UTF-8 kept as they came) and encoded again in upstream's padding,
  ``size`` left as served;
- every ``attachmentId``, in the body and in an ``/attachments/<id>`` path, becomes
  ``att-<n>``;
- a ``raw`` field, a whole message as text, is dropped: it is never recorded;
- every other string of the answer has its addresses made ``user@example.com``, so a
  ``snippet`` keeps none and no address beside a JSON escape (Google may write ``<`` as
  one) is cut through by the text rewrite.

Message and thread ids are kept: replay matches the path. A JSON answer the step changed
is written again as JSON (two-space indent, UTF-8), one it left alone as upstream sent
it. Last, before the one write, ``check`` searches the fixture's text, every decoded
string of a JSON body and every decoded ``body.data`` for what must never be written:
the secret values in any of those forms, Google's token shapes (``ya29.``, ``1//``,
``GOCSPX-``, ``apps.googleusercontent.com``), also percent-decoded, an address other than
``user@example.com``, a ``<...@...>`` other than ``<n@example.com>`` and
``<user@example.com>``, a token field whose value is not ``REDACTED``, a part's header
outside the allowlist, an ``attachmentId`` that is not ``att-<n>`` and a ``raw`` field.
Any of them means nothing is written and the exit status is 1; a refusal names what,
never a value. Only the status, the method, the scrubbed URL and the quota units are
printed.
"""

import argparse
import base64
import binascii
import dataclasses
import inspect
import json
import os
import re
import sys
import types
import typing
from collections.abc import Awaitable, Callable, Generator, Iterator, Sequence
from pathlib import Path
from typing import Any, TypeGuard
from urllib.parse import parse_qsl, quote, quote_plus, unquote_plus, urlencode

import httpx
from xippios.core import AsyncClient, RetryPolicy, XippiosError, sync
from xippios.core.testing import REDACTED, Fixture, Scrub, fixture_from_response, fixture_path

from xippios.google.mail import tasks
from xippios.google.mail._auth import (
    CLIENT_ID_ENV,
    CLIENT_SECRET_ENV,
    TOKEN_URL,
    RefreshTokenAuth,
)
from xippios.google.mail._client import (
    BASE_URL,
    DEMO,
    TENANTS,
    account_config,
    client_for,
    refresh_auth,
)

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

TASKS: dict[str, Callable[..., Awaitable[Any]]] = {
    "list_labels": tasks.list_labels_async,
    "list_messages": tasks.list_messages_async,
    "search_messages": tasks.search_messages_async,
    "get_message": tasks.get_message_async,
    "get_thread": tasks.get_thread_async,
}
"""The read tasks recorded through themselves, by name (fixture directory); module doc."""


def units_of(url: str) -> int | None:
    """The quota units of a Gmail GET at ``url`` (``quota.md``); ``None`` for another path.

    ``messages.get`` costs 20, 60 with ``format`` ``full`` (its default) or ``raw``;
    ``threads.get`` 40, 120 with ``format`` ``full`` (its default).
    """
    parsed = httpx.URL(url)
    base = httpx.URL(BASE_URL).path + "/"
    if not parsed.path.startswith(base):
        return None
    segments = parsed.path.removeprefix(base).split("/")
    form = parsed.params.get("format", "full")
    match segments:
        case ["profile"] | ["labels"]:
            return 1
        case ["messages"]:
            return 5
        case ["messages", _]:
            return 60 if form in ("full", "raw") else 20
        case ["threads", _]:
            return 120 if form == "full" else 40
        case ["messages", _, "attachments", _]:
            return 20
    return None


def _kind(annotation: Any) -> Any:
    """``str``, ``int``, ``bool`` or ``list`` for a task parameter's annotation."""
    if isinstance(annotation, types.UnionType):
        (annotation,) = [arg for arg in typing.get_args(annotation) if arg is not type(None)]
    return typing.get_origin(annotation) or annotation


def task_arguments(name: str, pairs: Sequence[tuple[str, str]]) -> dict[str, Any]:
    """The keyword arguments of task ``name`` from ``key=value`` pairs, typed by its signature.

    A ``list`` parameter collects its repeated pairs, a ``bool`` takes ``true`` or
    ``false``, an ``int`` digits; ``account`` is never one, the recorder's account is demo.

    Raises:
        ValueError: An unknown or repeated parameter, a value of the wrong kind or a
            required parameter missing.
    """
    fn = TASKS[name]
    hints = typing.get_type_hints(fn)
    parameters = {key: p for key, p in inspect.signature(fn).parameters.items() if key != "account"}
    values: dict[str, Any] = {}
    for key, raw in pairs:
        if key not in parameters:
            raise ValueError(f"{name} takes {', '.join(parameters) or 'no parameter'}, not {key}")
        kind = _kind(hints[key])
        if kind is list:
            values.setdefault(key, []).append(raw)
        elif key in values:
            raise ValueError(f"{key} is given twice")
        elif kind is bool:
            if raw not in ("true", "false"):
                raise ValueError(f"{key} is true or false")
            values[key] = raw == "true"
        elif kind is int:
            if not raw.isdigit():
                raise ValueError(f"{key} is a whole number")
            values[key] = int(raw)
        else:
            values[key] = raw
    missing = [
        key
        for key, parameter in parameters.items()
        if parameter.default is inspect.Parameter.empty and key not in values
    ]
    if missing:
        raise ValueError(f"{name} needs {', '.join(f'{key}=' for key in missing)}")
    return values


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

HEADER_ALLOWLIST = (
    "From",
    "To",
    "Cc",
    "Bcc",
    "Date",
    "Subject",
    "Message-ID",
    "In-Reply-To",
    "References",
    "Content-Type",
    "MIME-Version",
)
"""The MIME headers a fixture keeps, on every part, compared without regard to case."""
_ALLOWED = frozenset(name.lower() for name in HEADER_ALLOWLIST)
MESSAGE_ID_HEADERS = frozenset({"message-id", "in-reply-to", "references"})
"""The headers whose ids become ``<n@example.com>``, lower-cased."""
_ANGLED = re.compile(r"<[^<>]*>")
MESSAGE_ID_SHAPE = re.compile(r"<[^<>\s@]+@[^<>\s]+>")
"""A ``<local@domain>``, the shape of a Message-ID and of an address in angle brackets."""
_KEPT_ANGLED = re.compile(r"<(?:\d+@example\.com|user@example\.com)>")
"""The two ``<...@...>`` a fixture may hold: a numbered id and the scrubbed address."""
_ATTACHMENT_ID = re.compile(r"att-\d+")
_ATTACHMENT_PATH = re.compile(r"(?P<head>/attachments/)(?P<id>[^/?#]+)")


def _b64url_decode(data: str) -> bytes:
    """``data`` decoded as base64url, its padding restored; ``binascii.Error`` if it is not."""
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


class MessageScrub:
    """The §2.7 list for one fixture's JSON: headers, ids, bodies, attachments (module doc).

    One instance per fixture, so a Message-ID and an attachment id get the same number
    wherever they appear in it, the URL included.
    """

    def __init__(self) -> None:
        self._message_ids: dict[str, int] = {}
        self._attachment_ids: dict[str, int] = {}

    def message_id(self, value: str) -> str:
        """``<n@example.com>`` for ``value``, an id in angle brackets, numbered from 1."""
        number = self._message_ids.setdefault(value, len(self._message_ids) + 1)
        return f"<{number}@example.com>"

    def attachment_id(self, value: str) -> str:
        """``att-<n>`` for ``value``, an attachment id, numbered from 1."""
        number = self._attachment_ids.setdefault(value, len(self._attachment_ids) + 1)
        return f"att-{number}"

    def header_value(self, value: str) -> str:
        """A Message-ID, In-Reply-To or References value with each id numbered."""
        if _ANGLED.search(value):
            return _ANGLED.sub(lambda match: self.message_id(match.group(0)), value)
        return self.message_id(value.strip()) if value.strip() else value

    def data(self, data: str) -> str:
        """A ``body.data``: decoded, its addresses scrubbed, encoded in upstream's padding."""
        try:
            raw = _b64url_decode(data)
        except binascii.Error:
            return data  # not base64url: check() still reads the text as it is
        text = scrub_emails(raw.decode("utf-8", "surrogateescape"))
        encoded = base64.urlsafe_b64encode(text.encode("utf-8", "surrogateescape")).decode()
        return encoded if data.endswith("=") else encoded.rstrip("=")

    def url(self, url: str) -> str:
        """``url`` with an ``/attachments/<id>`` segment made ``att-<n>``."""
        return _ATTACHMENT_PATH.sub(
            lambda match: match["head"] + self.attachment_id(match["id"]), url
        )

    def node(self, node: Any) -> Any:
        """A decoded JSON value, scrubbed (the module docstring's list)."""
        if isinstance(node, list):
            return [self.node(item) for item in node]
        if isinstance(node, str):
            return scrub_emails(node)
        if not isinstance(node, dict):
            return node
        scrubbed: dict[str, Any] = {}
        for key, value in node.items():
            if key == "raw":
                continue  # a whole message as base64url text: never recorded
            if key == "headers" and _is_header_list(value):
                scrubbed[key] = [self.header(item) for item in value if _allowed(item)]
            elif key == "data" and isinstance(value, str):
                scrubbed[key] = self.data(value)
            elif key == "attachmentId" and isinstance(value, str):
                scrubbed[key] = self.attachment_id(value)
            else:
                scrubbed[key] = self.node(value)
        return scrubbed

    def header(self, item: dict[str, Any]) -> dict[str, Any]:
        """One kept MIME header, its ids numbered and its addresses scrubbed."""
        value = item.get("value")
        if isinstance(value, str) and str(item["name"]).lower() in MESSAGE_ID_HEADERS:
            return {**item, "value": self.header_value(value)}
        return {key: self.node(field) for key, field in item.items()}

    def body(self, body: str) -> str:
        """A JSON answer scrubbed and written again; any other text, or one unchanged, as is."""
        try:
            decoded = json.loads(body)
        except ValueError:
            return body
        scrubbed = self.node(decoded)
        if scrubbed == decoded:
            return body
        return json.dumps(scrubbed, indent=2, ensure_ascii=False) + "\n"


def _is_header_list(value: Any) -> TypeGuard[list[dict[str, Any]]]:
    """A MIME part's ``headers``: a list of ``{"name": ..., "value": ...}`` objects."""
    return isinstance(value, list) and all(
        isinstance(item, dict) and isinstance(item.get("name"), str) for item in value
    )


def _allowed(item: dict[str, Any]) -> bool:
    return str(item["name"]).lower() in _ALLOWED


def parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    """The command line: operation, case, parameters and the modes."""
    parser = argparse.ArgumentParser(
        prog="record_fixture.py",
        description="Record one exchange of the demo account as a test fixture.",
    )
    parser.add_argument(
        "operation",
        help=f"{TOKEN}, a Gmail operation ({', '.join(sorted(OPERATIONS))}) or a read task "
        f"({', '.join(sorted(TASKS))})",
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
    if args.operation != TOKEN and args.operation not in OPERATIONS | TASKS.keys():
        parser.error(
            f"operation {args.operation!r}: use {TOKEN}, one of {sorted(OPERATIONS)} or a task "
            f"of {sorted(TASKS)}"
        )
    if not _CASE.fullmatch(args.case):
        parser.error(f"case {args.case!r}: use lower-case letters, digits and underscores")
    if any("=" not in item for item in args.params):
        parser.error("parameters are key=value")
    pairs = [(key, value) for key, _, value in (item.partition("=") for item in args.params)]
    args.params = dict(pairs)
    if "account" in args.params:
        parser.error("the recorder records the demo account only; account is not a parameter")
    if args.operation in TASKS:
        if args.garbage_token or args.garbage_client or args.garbage_bearer:
            parser.error(
                "a task records under demo's own grant; the --garbage modes are not for it"
            )
        try:
            args.task_arguments = task_arguments(args.operation, pairs)
        except ValueError as refused:
            parser.error(str(refused))
        return args
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


NUMBERED_ID = re.compile(r"\d+@example\.com")
"""The address part of a scrubbed Message-ID, ``<n@example.com>``, which stays as it is."""


def _scrubbed_email(match: re.Match[str]) -> str:
    if NUMBERED_ID.fullmatch(match.group(0)):
        return match.group(0)
    return SCRUBBED_EMAIL.replace("@", match["at"]) if match["at"] else SCRUBBED_EMAIL


def scrub_emails(text: str) -> str:
    """``text`` with every e-mail address made ``user@example.com`` (``%40`` kept as such).

    A scrubbed Message-ID's ``n@example.com`` is no address and is left alone.
    """
    return EMAIL.sub(_scrubbed_email, text)


def scrub_token_fields(text: str) -> str:
    """``text`` with the value of every ``TOKEN_FIELDS`` field in raw JSON made ``REDACTED``."""
    return _TOKEN_FIELD.sub(lambda match: f'{match["field"]}"{REDACTED}"', text)


TEXT_REWRITES: tuple[Callable[[str], str], ...] = (scrub_token_fields, scrub_emails)
"""The text rewrites of every location, after the secrets and ``MessageScrub``."""


def scrub_form(body: str) -> str:
    """A form body with ``FORM_SECRETS`` made ``REDACTED`` by key, the other fields as sent."""
    pairs = parse_qsl(body, keep_blank_values=True)
    return urlencode([(key, REDACTED if key in FORM_SECRETS else value) for key, value in pairs])


def rewrite(fixture: Fixture, secrets: Sequence[str]) -> Fixture:
    """The recorder's own step on core's ``Fixture``: the module docstring's list.

    ``MessageScrub`` reads the response body as JSON first, one instance for the
    fixture's URL and body, then the secrets in their encoded forms and
    ``TEXT_REWRITES`` run over every location.
    """
    pattern = _secret_pattern(secrets)
    messages = MessageScrub()

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
            "request": request.model_copy(
                update={"url": text(messages.url(request.url)), "body": body}
            ),
            "response": response.model_copy(
                update={
                    "headers": [(name, text(value)) for name, value in response.headers],
                    "body": (
                        response.body
                        if response.body_base64
                        else text(messages.body(response.body))
                    ),
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


def _objects(node: Any) -> Iterator[dict[str, Any]]:
    """Every object of a decoded JSON value, outermost first."""
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from _objects(value)
    elif isinstance(node, list):
        for item in node:
            yield from _objects(item)


def _message_leaks(decoded: Any, url: str) -> tuple[list[str], list[str]]:
    """The §2.7 refusals of a decoded JSON answer, and the text of its decoded bodies."""
    leaks: list[str] = []
    bodies: list[str] = []
    outside: set[str] = set()
    for item in _objects(decoded):
        if "raw" in item:
            leaks.append("a raw field, a whole message")
        headers = item.get("headers")
        if _is_header_list(headers):
            outside.update(h["name"] for h in headers if not _allowed(h))
        data = item.get("data")
        if isinstance(data, str):
            try:
                bodies.append(_b64url_decode(data).decode("utf-8", "surrogateescape"))
            except binascii.Error:
                bodies.append(data)
        attachment = item.get("attachmentId")
        if isinstance(attachment, str) and not _ATTACHMENT_ID.fullmatch(attachment):
            leaks.append("an attachmentId that is not att-<n>")
    if outside:
        leaks.append(f"a MIME header outside the allowlist ({', '.join(sorted(outside))})")
    if any(not _ATTACHMENT_ID.fullmatch(m["id"]) for m in _ATTACHMENT_PATH.finditer(url)):
        leaks.append("an attachment id in the path that is not att-<n>")
    return sorted(set(leaks)), bodies


def check(text: str, fixture: Fixture, secrets: Sequence[str]) -> list[str]:
    """What the fixture would leak, by name, never by value; empty when it may be written."""
    texts = [text]
    decoded: Any = None
    message_leaks: list[str] = []
    if not fixture.response.body_base64:
        try:
            decoded = json.loads(fixture.response.body)
        except ValueError:
            decoded = None
        texts.extend(_strings(decoded))
        message_leaks, bodies = _message_leaks(decoded, fixture.request.url)
        texts.extend(bodies)
    texts.extend([unquote_plus(item) for item in texts])  # a form body's or a query's
    leaks: list[str] = []
    pattern = _secret_pattern(secrets)
    if pattern is not None and any(pattern.search(item) for item in texts):
        leaks.append("a value of the refresh token, the client pair or an answer's token")
    for shape, what in TOKEN_SHAPES.items():
        if any(shape in item for item in texts):
            leaks.append(what)
    addresses = {match.group(0) for item in texts for match in EMAIL.finditer(item)}
    addresses = {address for address in addresses if not NUMBERED_ID.fullmatch(address)}
    if addresses - {SCRUBBED_EMAIL, SCRUBBED_EMAIL.replace("@", "%40")}:
        leaks.append("an e-mail address other than user@example.com")
    angled = {m.group(0) for item in texts for m in MESSAGE_ID_SHAPE.finditer(item)}
    if any(not _KEPT_ANGLED.fullmatch(value) for value in angled):
        leaks.append("a <...@...> other than <n@example.com> and <user@example.com>")
    left = sorted(set(_token_fields_left(decoded)))
    if left:
        leaks.append(f"a value under {', '.join(left)}")
    return leaks + message_leaks


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


def recording_client(
    auth: httpx.Auth, *, keep: Callable[[httpx.Response], None] | None = None
) -> AsyncClient:
    """The client the recorder sends through: the demo account's, never retrying.

    ``client_for("demo")`` checks that the account is declared; its configuration
    (``account_config``: base URL, pace, ``classify``, quota policy) is rebuilt on the
    recorder's own auth with ``RetryPolicy(max_attempts=1)``, since core's ``record`` takes
    no ``retry=``. ``keep``, a task recording's, is called with every response from the
    wire after the quota policy's own ``observe``.
    """
    client_for(DEMO)
    signer = auth if isinstance(auth, RefreshTokenAuth) else refresh_auth(DEMO)
    config = account_config(signer)
    observe = config.observe
    if keep is not None:
        policy, kept = observe, keep

        def observe(response: httpx.Response) -> None:
            if policy is not None:
                policy(response)
            kept(response)

    config = dataclasses.replace(config, retry=RetryPolicy(max_attempts=1), observe=observe)
    return AsyncClient(config, auth=auth)


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


async def task_exchanges_async(name: str, arguments: dict[str, Any]) -> list[httpx.Response]:
    """Run task ``name`` for demo through the recorder's client; every Gmail response.

    The registry is emptied and handed the recorder's client as demo's
    (``TENANTS.client_for``), so the task's own ``client_for("demo")`` returns it; the
    recording refuses to run if it does not.

    Raises:
        RuntimeError: The task would not use the recorder's client.
        XippiosError: The task's own error; the responses before it are not written.
    """
    responses: list[httpx.Response] = []
    client = recording_client(refresh_auth(DEMO), keep=responses.append)
    TENANTS.reset()
    if TENANTS.client_for(DEMO, lambda _name: client) is not client or (
        client_for(DEMO) is not client
    ):
        raise RuntimeError("the task would not send through the recorder's client")
    await TASKS[name](DEMO, **arguments)
    return responses


task_exchanges = sync(task_exchanges_async)


def _units(url: str) -> str:
    units = units_of(url)
    return "" if units is None else f" ({units} quota unit{'s' if units != 1 else ''})"


def record_task(args: argparse.Namespace) -> int:
    """Record every Gmail exchange of a read task, each checked before any is written."""
    try:
        responses = task_exchanges(args.operation, args.task_arguments)
    except XippiosError as error:  # its message names the method and path, never a value
        print(f"nothing written: {args.operation} raised {type(error).__name__}: {error}")  # noqa: T201
        return 1
    for response in responses:
        spent = _units(str(response.request.url))
        print(f"sent: {response.request.method} {response.status_code}{spent}")  # noqa: T201
    if not responses:
        print(f"nothing written: {args.operation} sent no Gmail request")  # noqa: T201
        return 1
    files: list[tuple[Path, str, Fixture]] = []
    for index, response in enumerate(responses, start=1):
        case = args.case if index == 1 else f"{args.case}_{index}"
        path = fixture_path(FIXTURES, args.operation, case)
        secrets = [*secret_values(), *answer_tokens(response)]
        try:
            fixture = rewrite(
                fixture_from_response(response, scrub=Scrub(secrets=secrets)), secrets
            )
        except ValueError as refused:  # names what and where, never a value
            print(f"nothing written: {path.relative_to(ROOT)}: {refused}")  # noqa: T201
            return 1
        text = to_json(fixture)
        leaks = check(text, fixture, secrets)
        if leaks:
            where = path.relative_to(ROOT)
            print(f"nothing written: {where} would contain " + "; ".join(leaks))  # noqa: T201
            return 1
        files.append((path, text, fixture))
    for path, text, fixture in files:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        line = f"{fixture.response.status} {fixture.request.method} {fixture.request.url}"
        print(f"{line} -> {path.relative_to(ROOT)}")  # noqa: T201
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """Record, scrub in memory, check, write; return the exit status."""
    args = parse_args(argv)
    os.environ.pop(TENANTS.list_var, None)  # the recorder sees the demo account only
    if args.operation in TASKS:
        return record_task(args)
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
