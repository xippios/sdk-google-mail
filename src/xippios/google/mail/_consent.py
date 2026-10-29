"""``xippios-google-mail-auth``: consent one Gmail account and print its refresh token once.

Usage::

    xippios-google-mail-auth --account work --scopes read
    xippios-google-mail-auth --account demo --scopes write --port 8765

What it does, in order (the loopback flow with PKCE of
``docs/upstream/pages/oauth2-native-app.md``; ADR-0025):

1. Reads the operator's Desktop-app OAuth client from ``GOOGLE_CLIENT_ID`` and
   ``GOOGLE_CLIENT_SECRET`` (the pair every account and every Google source shares).
2. Listens on ``http://127.0.0.1:<port>/``, a random free port unless ``--port`` names
   one, for the one redirect the consent ends with.
3. Prints the authorization URL on stderr: ``response_type=code``, the scopes of
   ``--scopes`` (``read`` is ``gmail.readonly``; ``write`` is ``gmail.modify``, which serves
   every read method too), a PKCE challenge (``code_challenge_method=S256``), a random
   ``state``, ``access_type=offline`` and ``prompt=consent``, so that Google issues a
   refresh token every time.
4. Open the URL in a browser signed in to the account and consent. Under WSL2 the
   Windows browser reaches ``127.0.0.1:<port>`` through localhost forwarding. A request
   to another path (a browser's ``/favicon.ico``) is answered 404 and ignored; a redirect
   whose ``state`` differs is refused. It waits ten minutes at most.
5. Exchanges the code at ``https://oauth2.googleapis.com/token`` with the PKCE verifier
   and the client pair (``grant_type=authorization_code``).
6. Prints the refresh token once, alone on stdout, and on stderr the variable to set,
   ``GOOGLE_<NAME>_REFRESH_TOKEN``, with ``GOOGLE_ACCOUNTS`` to list the account in (but
   ``demo``, which is declared by its token alone); the operator puts it into the
   environment (``.envrc``, or the org secret for ``demo``).

Nothing is stored: no file is written, the access token of the exchange is discarded,
and the refresh token leaves the process only on stdout. A grant that lacks a scope of
the set asked for (Google's consent screen lets the user untick one) prints no token and
exits with status 1: re-run and grant every scope. An account has at most 100 refresh
tokens per OAuth client, the oldest invalidated first (``oauth2.md``), so every run uses
one: run it only to consent a new account or to replace a revoked or expired token (an
app whose consent screen is in Testing status issues seven-day grants). Exit status 0 on
success, 1 when the consent or the exchange failed, 2 for a usage or environment error.
A consent is the operator's step, done by hand; no test or recorder runs this tool live.
"""

import argparse
import base64
import hashlib
import http.server
import os
import re
import secrets
import sys
import time
import urllib.parse
from collections.abc import Sequence
from typing import Any

import httpx

from xippios.google.mail._auth import (
    CLIENT_ID_ENV,
    CLIENT_SECRET_ENV,
    CONSENT_COMMAND,
    SCOPE_SETS,
    TOKEN_URL,
    short_scope,
)
from xippios.google.mail._client import DEMO, TENANTS, config

__all__ = ["AUTH_URL", "WAIT", "authorization_url", "main", "pkce_pair", "run"]

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
"""Google's authorization endpoint (``oauth2-native-app.md``, step 2)."""
WAIT = 600.0
"""Seconds the listener waits for the redirect before giving up."""
_NAME = re.compile(r"[a-z0-9_]+")


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def pkce_pair() -> tuple[str, str]:
    """A PKCE code verifier and its S256 challenge (RFC 7636): 43 characters each."""
    verifier = _b64url(secrets.token_bytes(32))
    return verifier, _b64url(hashlib.sha256(verifier.encode("ascii")).digest())


def authorization_url(
    client_id: str, redirect_uri: str, scopes: Sequence[str], challenge: str, state: str
) -> str:
    """The URL the operator opens: the consent for ``scopes``, a refresh token included."""
    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": " ".join(scopes),
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "state": state,
        "access_type": "offline",
        "prompt": "consent",
    }
    return f"{AUTH_URL}?{urllib.parse.urlencode(params)}"


class _Listener(http.server.HTTPServer):
    """The loopback listener: keeps the first redirect to ``/`` that carries ``state``."""

    expected_state: str = ""
    result: dict[str, str] | None = None


class _Redirect(http.server.BaseHTTPRequestHandler):
    """Answers the redirect; any other path is a 404 and changes nothing."""

    server: _Listener

    def do_GET(self) -> None:  # the name http.server calls
        """Keep the redirect's query when its ``state`` matches; answer the browser."""
        url = urllib.parse.urlsplit(self.path)
        if url.path != "/":
            self._answer(404, "not found")
            return
        query = dict(urllib.parse.parse_qsl(url.query, keep_blank_values=True))
        if not secrets.compare_digest(query.get("state", ""), self.server.expected_state):
            self.server.result = {"error": "state_mismatch"}
            self._answer(400, "the state does not match; close this tab and run the tool again")
            return
        self.server.result = query
        if "code" in query:
            self._answer(200, "consent received; you can close this tab")
        else:
            self._answer(400, "no consent was given; close this tab")

    def _answer(self, status: int, text: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(text.encode("utf-8"))

    def log_message(self, format: str, *args: Any) -> None:
        """Log nothing: the request line carries the authorization code."""


def _say(text: str) -> None:
    print(text, file=sys.stderr)  # noqa: T201 - the tool's messages go to stderr


def _credential(name: str) -> str | None:
    value = os.environ.get(name, "").strip()
    return value or None


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=CONSENT_COMMAND,
        description=(
            "Consent one Gmail account through a loopback redirect with PKCE and print its "
            "refresh token once, to stdout; nothing is stored."
        ),
    )
    parser.add_argument(
        "--account",
        required=True,
        help="the account's name, lower-case letters, digits and underscores, e.g. work or demo",
    )
    parser.add_argument(
        "--scopes",
        required=True,
        choices=sorted(SCOPE_SETS),
        help="read (gmail.readonly) or write (gmail.modify, which serves every read too)",
    )
    parser.add_argument(
        "--port", type=int, default=0, help="loopback port for the redirect; 0, a free one"
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Consent, exchange, print; return the exit status (see the module docstring)."""
    parser = _parser()
    args = parser.parse_args(argv)
    if not _NAME.fullmatch(args.account):
        parser.error("--account: use lower-case letters, digits and underscores, e.g. work")
    client_id = _credential(CLIENT_ID_ENV)
    client_secret = _credential(CLIENT_SECRET_ENV)
    if client_id is None or client_secret is None:
        _say(
            f"{CONSENT_COMMAND}: error: export the operator's Desktop-app OAuth client as "
            f"{CLIENT_ID_ENV} and {CLIENT_SECRET_ENV}"
        )
        return 2
    scopes = SCOPE_SETS[args.scopes]
    verifier, challenge = pkce_pair()
    try:
        listener = _Listener(("127.0.0.1", args.port), _Redirect)
    except OSError as exc:
        _say(f"{CONSENT_COMMAND}: error: cannot listen on 127.0.0.1:{args.port} ({exc.strerror})")
        return 2
    listener.expected_state = secrets.token_urlsafe(16)
    listener.timeout = 1.0
    redirect_uri = f"http://127.0.0.1:{listener.server_port}/"
    try:
        _say(f"account {args.account}, scopes {args.scopes}: {' '.join(scopes)}")
        _say(f"listening on {redirect_uri} for {WAIT / 60:g} minutes")
        _say("open this URL in a browser signed in to the account and consent:")
        _say(authorization_url(client_id, redirect_uri, scopes, challenge, listener.expected_state))
        deadline = time.monotonic() + WAIT
        while listener.result is None and time.monotonic() < deadline:
            listener.handle_request()
    finally:
        listener.server_close()
    result = listener.result
    if result is None:
        _say("no redirect arrived in time; run the tool again")
        return 1
    if "code" not in result:
        _say(f"no consent: the redirect carried error={result.get('error', 'none')}")
        return 1
    response = httpx.post(
        TOKEN_URL,
        data={
            "code": result["code"],
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
            "code_verifier": verifier,
        },
        headers={"User-Agent": config.user_agent, "Accept": "application/json"},
        timeout=config.timeouts.to_httpx(),
    )
    return _report(response, args.account, args.scopes)


def _report(response: httpx.Response, account: str, scope_set: str) -> int:
    """Print the refresh token of a good exchange, or why there is none; the exit status."""
    try:
        answer = response.json()
    except ValueError:
        answer = None
    if not isinstance(answer, dict):
        answer = {}
    if response.status_code != 200:
        _say(
            f"the token endpoint answered HTTP {response.status_code}: "
            f"error={answer.get('error')} description={answer.get('error_description')}"
        )
        return 1
    granted = set(str(answer.get("scope", "")).split())
    missing = [scope for scope in SCOPE_SETS[scope_set] if scope not in granted]
    if missing:
        _say(
            f"the account granted {', '.join(sorted(map(short_scope, granted))) or 'no scope'}, "
            f"not {', '.join(map(short_scope, missing))}; no token is printed: run the tool "
            "again and grant every scope the consent screen asks for"
        )
        return 1
    refresh_token = answer.get("refresh_token")
    if not isinstance(refresh_token, str) or not refresh_token:
        _say("the answer carried no refresh_token, although access_type=offline was sent")
        return 1
    variable = TENANTS.var(account, "REFRESH_TOKEN")
    where = "" if account == DEMO else f", and list {account} in {TENANTS.list_var}"
    _say(f"the refresh token of account {account} follows on stdout: set {variable} to it{where}")
    lifetime = answer.get("refresh_token_expires_in")
    if isinstance(lifetime, int):
        _say(f"it expires in {lifetime} s ({lifetime / 86400:.1f} days): consent again by then")
    print(refresh_token)  # noqa: T201 - the one place the token leaves the process
    return 0


def run() -> None:
    """The ``xippios-google-mail-auth`` entry point."""
    sys.exit(main())
