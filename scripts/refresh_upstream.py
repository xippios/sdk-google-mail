# /// script
# requires-python = ">=3.12"
# dependencies = ["markdownify==1.2.3"]
#
# [tool.uv]
# exclude-newer = "2026-10-29T00:00:00Z"
# ///
"""Refresh the upstream documentation snapshot in ``docs/upstream/`` (``/refresh-upstream``).

Gmail documents its REST API in a discovery document, which the REST reference at
``DOCS_URL`` names ("This service provides the following discovery document"), and on
developers.google.com, Google Identity's OAuth 2.0 pages and the Gmail Help Center. The
script writes:

- ``discovery.json``: the discovery document, ``SPEC_URL``, with its keys sorted: the
  discovery service serves the members of every object in a new order on each request,
  so the file is the document as fetched re-serialised with ``sort_keys=True``, once the
  fetched text is checked to be ``json.dumps(indent=2)`` of it in its served order;
- ``pages/quota.md``, ``pages/handle-errors.md`` and ``pages/auth-scopes.md``: the Gmail
  API's usage limits, its errors and its scopes, from developers.google.com;
- ``pages/search-operators.md``: the Gmail Help article on the search operators that
  ``messages.list``'s ``q`` takes;
- ``pages/oauth2.md``, ``pages/oauth2-native-app.md`` and ``pages/oauth2-web-server.md``:
  Google Identity's OAuth 2.0 overview (refresh-token expiry among it) and its guides for
  desktop apps (the loopback flow with PKCE) and web server applications (the refresh of
  an access token);
- ``FETCHED.md``: regenerated whole.

Each HTML page becomes Markdown with ``markdownify`` (``_Markdown``): a line naming the
URL it was converted from, the page's title, its article, and on developers.google.com
the content footer with its licence and its "Last updated" date; the navigation, the
feedback widgets, the Page Summary the pages mark "Generated with AI" and the icons are
dropped, and a table with merged cells or a nested table stays HTML (conventions §3). A
page the converter does not recognise (another title, an element it does not know,
another number of HTML tables than ``HTML_TABLES`` says) stops the run.

Everything is fetched before anything is written, one request at a time after a pause,
each URL retried at most once after a network error or a 5xx. Every page's body changes
on each request (a script nonce, the order of attributes in the navigation, the
discovery document's member order), so every row is ``stable=False`` and records no
size, sha256 or validators; the files are what a refresh diffs. Files the run does not
produce are removed from the subdirectories it writes to; nothing else under
``docs/upstream/`` is touched. Every run stops, before writing, when the visible text of
an HTML page, or the text of the discovery document, states a copyright, a licence or
terms of use that ``ATTRIBUTION`` does not record. No key, token or credential is needed,
sent or read. Run from the repository root::

    uv run scripts/refresh_upstream.py            # fetch, then write the snapshot
    uv run scripts/refresh_upstream.py --check    # fetch and compare; write nothing

``--check`` exits 1 when the snapshot differs from what upstream serves now, ``Fetched``
aside. HTML is converted to Markdown with ``markdownify``, the one converter a capture
script may declare: in the ``# /// script`` block above, and at the same pin in the dev
group of ``pyproject.toml``, so that ``mypy --strict`` reads its types; Beautiful Soup,
which it parses with, is reached through it and never imported here.
"""

import argparse
import hashlib
import html
import http.client
import json
import re
import textwrap
import time
import urllib.error
import urllib.request
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from importlib.metadata import version
from pathlib import Path
from typing import IO, Any
from urllib.parse import urljoin, urlsplit

from markdownify import ATX, BACKSLASH, MarkdownConverter

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = ROOT / "docs/upstream"
COMMAND = "uv run scripts/refresh_upstream.py"
SOURCE_NAME = "Gmail"
DOCS_URL = "https://developers.google.com/workspace/gmail/api/reference/rest"
"""Where the documentation lives: ``upstream_docs_url`` in ``.copier-answers.yml``."""
USER_AGENT = "xippios-google-mail refresh_upstream (+https://github.com/xippios)"
TIMEOUT = 60.0
PAUSE = 2.0
"""Seconds before every request; requests never run in parallel."""
RETRY_PAUSE = 10.0
"""Seconds before the one retry after a network error or a 5xx."""


@dataclass(frozen=True)
class Page:
    """One page the snapshot records, and the file it is kept as, if any.

    Attributes:
        url: Where the page is fetched from.
        file: Its path under ``docs/upstream/`` (``pages/guide.md``,
            ``openapi/openapi.json``), or ``None`` for a page recorded without a file.
        status: The HTTP status it must answer with: 200, or the 403 or 404 of a page
            recorded by its status.
        note: The row's note in ``FETCHED.md``: how the file was made, or why there is none.
        stable: ``False`` for a body that changes on every request (a per-request
            reference in a refusal page): its row records no size, sha256 or validators.
        excerpt: The file is this source's part of a shared document (conventions §3);
            the row keeps the whole document's size and sha256, and its note says so.
        title: The page's title as printed, its ``<h1>``; another stops the run, so a
            localised or moved page is not taken for this one.
    """

    url: str
    file: str | None = None
    status: int = 200
    note: str = ""
    stable: bool = True
    excerpt: bool = False
    title: str = ""


GMAIL_DOCS = "https://developers.google.com/workspace/gmail/api"
IDENTITY_DOCS = "https://developers.google.com/identity/protocols/oauth2"
DEVSITE = "developers.google.com"
HELP_CENTER = "support.google.com"

HTML_TABLES = {
    "pages/quota.md": 1,
    "pages/oauth2-native-app.md": 4,
    "pages/oauth2-web-server.md": 4,
}
"""How many tables each file keeps as HTML, those with merged cells or a nested table.

A nested table is counted with the table it sits in. ``reduce`` stops the run on any
other count, so the rows' notes stay true (conventions §3).
"""


def _converted(file: str) -> str:
    tables = HTML_TABLES.get(file, 0)
    note = "converted to Markdown"
    if tables:
        plural = "s" if tables > 1 else ""
        note += f"; {tables} table{plural} with merged cells or a nested table kept as HTML"
    return note


def _page(url: str, file: str, title: str) -> Page:
    return Page(url, file, note=_converted(file), stable=False, title=title)


PAGES: tuple[Page, ...] = (
    _page(f"{GMAIL_DOCS}/reference/quota", "pages/quota.md", "Usage limits"),
    _page(f"{GMAIL_DOCS}/guides/handle-errors", "pages/handle-errors.md", "Resolve errors"),
    _page(f"{GMAIL_DOCS}/auth/scopes", "pages/auth-scopes.md", "Choose Gmail API scopes"),
    _page(
        f"https://{HELP_CENTER}/mail/answer/7190",
        "pages/search-operators.md",
        "Refine searches in Gmail",
    ),
    _page(IDENTITY_DOCS, "pages/oauth2.md", "Using OAuth 2.0 to Access Google APIs"),
    _page(
        f"{IDENTITY_DOCS}/native-app", "pages/oauth2-native-app.md", "OAuth 2.0 for Desktop Apps"
    ),
    _page(
        f"{IDENTITY_DOCS}/web-server",
        "pages/oauth2-web-server.md",
        "Using OAuth 2.0 for Web Server Applications",
    ),
)
"""Every page the snapshot records, in the order of ``FETCHED.md``'s rows."""

SPEC_URL: str | None = "https://gmail.googleapis.com/$discovery/rest?version=v1"
"""The machine-readable API document the documentation names: Gmail's discovery document.

The REST reference at ``DOCS_URL`` names it ("This service provides the following
discovery document") and calls it "a machine-readable specification for describing and
consuming REST APIs"; the script keeps it as ``SPEC_FILE``, after ``PAGES``, its keys
sorted (``discovery``), and does not keep the reference's pages.
"""
SPEC_FILE = "discovery.json"
"""The document's path under ``docs/upstream/``, with the extension it is served with."""
SPEC_NOTE = (
    "SPEC_URL, the discovery document, its keys sorted: the service serves the members of "
    "every object in a new order on each request"
)
"""The discovery document's note in ``FETCHED.md``."""


@dataclass(frozen=True)
class Statement:
    """A copyright line, licence or terms of use a page prints, as printed.

    Attributes:
        text: The statement as printed, its whitespace collapsed.
        where: Where it is printed, for ``FETCHED.md``'s Attribution.
        link: For a link whose text names terms of use: its target, absolute. The check
            accepts the text only as the text of a link to this target, so the same words
            elsewhere still stop the run.
    """

    text: str
    where: str
    link: str | None = None


DEVSITE_FOOTER = (
    "Except as otherwise noted, the content of this page is licensed under the Creative "
    "Commons Attribution 4.0 License, and code samples are licensed under the Apache 2.0 "
    "License. For details, see the Google Developers Site Policies. Java is a registered "
    "trademark of Oracle and/or its affiliates."
)
"""The content footer of every developers.google.com page, as printed."""

ATTRIBUTION: tuple[Statement, ...] = (
    Statement(
        DEVSITE_FOOTER,
        "the content footer of every developers.google.com page here, its links to "
        "https://creativecommons.org/licenses/by/4.0/, "
        "https://www.apache.org/licenses/LICENSE-2.0 and "
        "https://developers.google.com/site-policies; each Markdown file made from one keeps "
        "the footer and the page's Last updated date",
    ),
    Statement(
        "Terms of Service",
        "a link in the site footer of the Gmail API pages",
        "https://developers.google.com/workspace/terms",
    ),
    Statement(
        "Terms of Service",
        "a link in the site footer of the Google Identity pages",
        "https://developers.google.com/terms",
    ),
    Statement(
        "If prompted, read and accept the API's Terms of Service.",
        "a step of enabling an API in the OAuth 2.0 guides for desktop apps and for web "
        "server applications",
    ),
    Statement("\N{COPYRIGHT SIGN}2026 Google", "the footer of the Gmail Help article"),
    Statement(
        "Terms of Service",
        "a link in the footer and in the navigation drawer of the Gmail Help article",
        "https://www.google.com/intl/en/policies/terms/",
    ),
)
"""Every copyright line, licence or terms of use the documentation states, as printed.

``FETCHED.md`` lists them under Attribution; the discovery document states none. Every run
stops when the visible text of a page states one that is not here, or names terms in a
link to a target that is not recorded.
"""

CAPTURE = (
    "The discovery document is kept with its keys sorted: the discovery service serves the "
    "members of every object in a new order on each request, so the script checks that the "
    "fetched text is the document written with `json.dumps(indent=2, ensure_ascii=False)` in "
    "its served order and writes it again with `sort_keys=True`; nothing else changes, and "
    f"arrays keep their order. The REST reference at {DOCS_URL} names the document, which it "
    "calls a machine-readable specification of the API; the script keeps the document and "
    "not the reference's pages. Each HTML page is converted to Markdown with markdownify "
    f"{version('markdownify')} (Beautiful Soup {version('beautifulsoup4')}, html.parser), "
    "headings in ATX style and underscores left unescaped, so that names read as upstream "
    "writes them, links and images made absolute. A developers.google.com page keeps its "
    "title, its `devsite-article-body` and its content footer, with the licence and the Last "
    "updated date; the Gmail Help article keeps its title and its article text (`div.cc`). "
    "Left out: the site's navigation, breadcrumbs and footers, the feedback and rating "
    "widgets, the Page Summary panel of the OAuth 2.0 pages, which they mark "
    '"Generated with AI", icons and buttons. A callout (`aside`) becomes a blockquote. A '
    "table with merged cells (`rowspan`, `colspan`) or a nested table stays HTML, with its "
    "cells' spans, links and inline markup and no other attribute, and its row's note counts "
    "them (conventions §3). Every page's body changes on each request, the HTML pages by a "
    "script nonce and the order of attributes in their navigation, so every row records no "
    "size, sha256 or validators: the files are what a refresh diffs."
)
"""``FETCHED.md``'s "How this is captured": what is converted, cut or left out, and why."""

_STATEMENT = re.compile(
    r"\N{COPYRIGHT SIGN}|copyright|licen[cs]e|terms of (?:use|service)|public domain",
    re.IGNORECASE,
)
_FETCHED = re.compile(r"\| (\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z) \|")


@dataclass(frozen=True)
class Response:
    """One GET as served: status, body and validators, and the redirects on the way."""

    url: str
    status: int
    body: bytes
    etag: str | None
    last_modified: str | None
    redirects: tuple[tuple[int, str], ...]


@dataclass(frozen=True)
class Row:
    """One row of ``FETCHED.md``: the page, its response and its file's content, if any."""

    page: Page
    response: Response
    content: bytes | None


class _RetryableError(Exception):
    """A network error or a 5xx: worth one more attempt."""


class _RecordRedirects(urllib.request.HTTPRedirectHandler):
    """Follow redirects as urllib does, remembering each status and target."""

    def __init__(self) -> None:
        super().__init__()
        self.hops: list[tuple[int, str]] = []

    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: IO[bytes],
        code: int,
        msg: str,
        headers: http.client.HTTPMessage,
        newurl: str,
    ) -> urllib.request.Request | None:
        """Record the hop, then let urllib build the follow-up request."""
        self.hops.append((code, newurl))
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _log(message: str) -> None:
    print(message, flush=True)  # noqa: T201


def _get(url: str, expect: int) -> Response:
    redirects = _RecordRedirects()
    opener = urllib.request.build_opener(redirects)
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with opener.open(request, timeout=TIMEOUT) as response:
            body: bytes = response.read()
            status: int = response.status
            headers = response.headers
    except urllib.error.HTTPError as error:
        if error.code >= 500:
            raise _RetryableError(f"HTTP {error.code} {error.reason}") from None
        if error.code != expect:
            raise SystemExit(f"GET {url}: HTTP {error.code} {error.reason}") from None
        body, status, headers = error.read(), error.code, error.headers
    except (http.client.HTTPException, OSError) as error:  # URLError is an OSError
        raise _RetryableError(str(error)) from None
    if status != expect:
        raise SystemExit(f"GET {url}: expected HTTP {expect}, got {status}")
    encoding = headers.get("Content-Encoding", "identity").strip().lower()
    if encoding != "identity":
        raise SystemExit(f"GET {url}: unexpected Content-Encoding {encoding!r}")
    return Response(
        url,
        status,
        body,
        headers.get("ETag"),
        headers.get("Last-Modified"),
        tuple(redirects.hops),
    )


def fetch(url: str, expect: int = 200) -> Response:
    """GET ``url`` after a polite pause; retry once on a network error or a 5xx."""
    time.sleep(PAUSE)
    try:
        response = _get(url, expect)
    except _RetryableError as error:
        _log(f"  {url}: {error}; retrying once in {RETRY_PAUSE:g} s")
        time.sleep(RETRY_PAUSE)
        try:
            response = _get(url, expect)
        except _RetryableError as error:
            raise SystemExit(f"GET {url}: {error} (after one retry)") from None
    digest = hashlib.sha256(response.body).hexdigest()
    hops = "".join(f", {code} to {target}" for code, target in response.redirects)
    _log(
        f"  {url}: HTTP {response.status}{hops}, {len(response.body)} bytes, sha256 {digest}, "
        f"Last-Modified {response.last_modified or '-'}, ETag {response.etag or '-'}"
    )
    return response


def changed(url: str, what: str) -> SystemExit:
    """The error that stops the run on a page the script does not recognise."""
    return SystemExit(
        f"{url} changed: {what}.\nLook at the page by hand and update "
        "scripts/refresh_upstream.py. Nothing was written."
    )


def decode(response: Response) -> str:
    """The body as UTF-8 text; a body that is not UTF-8 stops the run."""
    try:
        return response.body.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise changed(response.url, f"the body is not UTF-8 ({error})") from None


_NO_TEXT = frozenset({"script", "style", "template"})
_LINKED = frozenset((s.text, s.link) for s in ATTRIBUTION if s.link)
_STATED = tuple(s.text for s in ATTRIBUTION if not s.link)


class _VisibleText(HTMLParser):
    """The text a reader sees, outside scripts, styles and templates, but recorded links.

    A link whose text and absolute target are a recorded ``Statement`` leaves no text, so
    the same words in prose or in a link elsewhere are still searched.
    """

    def __init__(self, base: str) -> None:
        super().__init__(convert_charrefs=True)
        self.base = base
        self.parts: list[str] = []
        self._hidden = 0
        self._links: list[tuple[int, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        """Enter a script, a style or a template, or note where a link starts."""
        if tag in _NO_TEXT:
            self._hidden += 1
        elif tag == "a":
            href = dict(attrs).get("href") or ""
            self._links.append((len(self.parts), urljoin(self.base, href.strip())))

    def handle_endtag(self, tag: str) -> None:
        """Leave a script, a style or a template, or drop a recorded link's text."""
        if tag in _NO_TEXT and self._hidden:
            self._hidden -= 1
        elif tag == "a" and self._links:
            start, href = self._links.pop()
            if (" ".join("".join(self.parts[start:]).split()), href) in _LINKED:
                del self.parts[start:]
                self.parts.append(" ")

    def handle_data(self, data: str) -> None:
        """Keep visible text."""
        if not self._hidden:
            self.parts.append(data)


def visible_text(page: Page, text: str) -> str:
    """The visible text of an HTML page, entities decoded, whitespace collapsed."""
    parser = _VisibleText(page.url)
    parser.feed(text)
    parser.close()
    return " ".join("".join(parser.parts).split())


def _is_html(page: Page) -> bool:
    return page.file is not None and page.file.endswith(".md")


def check_attribution(page: Page, response: Response) -> None:
    """Stop when the page states a copyright, licence or terms that ``ATTRIBUTION`` lacks.

    The check reads the visible text of an HTML page, its header and footer included and
    its scripts, styles and templates left out: Google's pages carry licence comments of
    the JavaScript libraries they load and the interface strings of their widgets, which
    the page does not print. The recorded links' text is dropped (``_VisibleText``), then
    the recorded statements, and the rest is searched. The discovery document is read
    whole, its entities decoded.
    """
    if _is_html(page):
        body = visible_text(page, decode(response))
    else:
        body = html.unescape(response.body.decode("utf-8", "replace"))
    for statement in _STATED:
        body = body.replace(statement, "")
    match = _STATEMENT.search(body)
    if match:
        around = " ".join(body[max(match.start() - 60, 0) : match.end() + 60].split())
        raise SystemExit(
            f"GET {page.url}: the page states {match.group(0)!r} ({around!r}), a copyright, "
            "licence or terms of use that ATTRIBUTION does not record.\nRead it by hand, "
            "record it in ATTRIBUTION and update scripts/refresh_upstream.py. Nothing was "
            "written."
        )


def _no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    counts = Counter(key for key, _ in pairs)
    duplicates = sorted(key for key, count in counts.items() if count > 1)
    if duplicates:
        raise ValueError(f"duplicate keys {duplicates}")
    return dict(pairs)


def dump_json(value: Any, indent: int = 2) -> bytes:
    """``value`` as JSON in the order it holds, ``ensure_ascii=False``, with a final newline."""
    return (json.dumps(value, indent=indent, ensure_ascii=False) + "\n").encode("utf-8")


def load_json(response: Response, indent: int = 2) -> Any:
    """The JSON document of ``response``, once re-serialising it reproduces the fetched text.

    A source that keeps a JSON document cut to its part writes the part with ``dump_json``;
    this check makes sure that the whole document, written the same way, is the fetched
    text up to line endings, so the part is an excerpt of upstream's text. A duplicate key,
    which ``json.loads`` would drop silently, stops the run too.
    """
    try:
        document = json.loads(response.body, object_pairs_hook=_no_duplicates)
    except ValueError as error:
        raise SystemExit(f"GET {response.url}: not usable JSON ({error})") from None
    fetched = response.body.replace(b"\r\n", b"\n").rstrip(b"\n")
    if dump_json(document, indent).rstrip(b"\n") != fetched:
        raise SystemExit(
            f"GET {response.url}: re-serialised with json.dumps(indent={indent}, "
            "ensure_ascii=False), the document no longer reproduces the fetched text up to "
            "line endings, so a part cut from it would not be an excerpt of upstream's text."
            "\nLook at the document by hand and update scripts/refresh_upstream.py. Nothing "
            "was written."
        )
    return document


def discovery(response: Response) -> bytes:
    """The discovery document with its keys sorted, once its fetched text is checked.

    ``load_json`` stops unless the document, written with ``json.dumps(indent=2,
    ensure_ascii=False)`` in the order it was served, is the fetched text; written again
    with ``sort_keys=True``, it differs from it in the order of object members only.
    """
    document = load_json(response)
    if not isinstance(document, dict) or (
        document.get("kind"),
        document.get("name"),
        document.get("version"),
    ) != ("discovery#restDescription", "gmail", "v1"):
        raise changed(response.url, "it is not Gmail v1's discovery document")
    text = json.dumps(document, indent=2, ensure_ascii=False, sort_keys=True)
    return (text + "\n").encode("utf-8")


_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
_DROPPED = frozenset(
    {
        "button",
        "devsite-actions",
        "devsite-feedback",
        "devsite-key-takeaways-panel",
        "devsite-thumb-rating",
        "gkms-context-selector",
        "noscript",
        "script",
        "style",
        "svg",
        "template",
    }
)
"""Elements ``_Markdown`` drops with their content: widgets, the Page Summary panel (marked
"Generated with AI"), an empty context selector, and what a reader does not see."""
_UNWRAPPED = frozenset({"devsite-code"})
"""Custom elements whose content is kept: the wrapper of a code block."""
_ICONS = ("material-icons", "material-symbols", "google-symbols")
"""Class prefixes of icon glyphs, whose text is a glyph's name, not documentation."""
_TABLE_TAGS = frozenset({"table", "caption", "thead", "tbody", "tfoot", "tr", "th", "td"})
_KEPT_TAGS = _TABLE_TAGS | {
    "a",
    "b",
    "blockquote",
    "br",
    "code",
    "em",
    "i",
    "li",
    "ol",
    "p",
    "pre",
    "strong",
    "sub",
    "sup",
    "ul",
    "var",
}
"""The tags a table kept as HTML keeps; any other is unwrapped and its text kept."""
_ROWS = frozenset({"table", "thead", "tbody", "tfoot", "tr"})
"""Tags whose whitespace between children is layout, dropped in a table kept as HTML."""
_VOID = frozenset({"br"})
_TOKEN = "XIPPIOSHTMLTABLE{}X"


def _classes(element: Any) -> list[str]:
    value = element.get("class") or []
    return [value] if isinstance(value, str) else [str(name) for name in value]


def _merged(table: Any) -> bool:
    """Whether a table has a merged cell or a table nested in it, which a pipe table loses."""
    cells = table.find_all(["td", "th"])
    spans = any(cell.get(name, "1") != "1" for cell in cells for name in ("colspan", "rowspan"))
    return spans or table.find("table") is not None


def _html(node: Any, pre: bool = False) -> str:
    """A node of a table kept as HTML: kept tags with their spans or ``href``, text escaped."""
    if node.name is None:
        text = html.escape(str(node), quote=False)
        if pre:
            return text
        if not text.strip() and node.parent is not None and node.parent.name in _ROWS:
            return ""
        return re.sub(r"\s+", " ", text)
    inner = "".join(_html(child, pre or node.name == "pre") for child in node.children)
    if node.name not in _KEPT_TAGS:
        return inner
    kept = ("colspan", "rowspan") if node.name in ("th", "td") else ("href",)
    attrs = "".join(
        f' {name}="{html.escape(str(node[name]))}"' for name in kept if node.has_attr(name)
    )
    if node.name in _VOID:
        return f"<{node.name}{attrs}>"
    if not pre and node.name != "pre":
        inner = inner.strip()
    return f"<{node.name}{attrs}>{inner}</{node.name}>"


def _html_table(url: str, table: Any) -> str:
    """A table with merged cells or a nested table as HTML, one line per row of its own."""
    lines = ["<table>"]
    for part in table.find_all(["caption", "tr"]):
        if part.find_parent("table") is table:
            lines.append(_html(part))
    lines.append("</table>")
    text = "\n".join(lines)
    if any(not line.strip() for line in text.split("\n")):
        raise changed(
            url, "a table kept as HTML would hold a blank line, which ends it in Markdown"
        )
    return text


class _Markdown(MarkdownConverter):
    """markdownify for one page: its title and article, the chrome dropped (``CAPTURE``).

    ``convert_soup`` is markdownify's hook for the parsed document: this one picks the
    page's parts, prepares them and renders each; a table kept as HTML is rendered apart
    and put back in place of a token. The elements markdownify hands over are Beautiful
    Soup's, typed ``Any`` here.
    """

    def __init__(self, page: Page) -> None:
        super().__init__(
            heading_style=ATX, bullets="-", escape_underscores=False, newline_style=BACKSLASH
        )
        self.page = page
        self.tables: list[str] = []

    def convert_soup(self, soup: Any) -> str:
        """Render the page's title and parts; stop on a page the script does not know."""
        host = urlsplit(self.page.url).netloc
        if host == DEVSITE:
            title, parts = self._devsite(soup)
        elif host == HELP_CENTER:
            title, parts = self._help_article(soup)
        else:
            raise changed(self.page.url, f"no converter for {host}")
        if title != self.page.title:
            raise changed(self.page.url, f"the title is {title!r}, not {self.page.title!r}")
        blocks = [f"# {title}"]
        for part in parts:
            self._prepare(part)
            blocks.append(super().convert_soup(part).strip())
        text = "\n\n".join(blocks)
        for index, table in enumerate(self.tables):
            token = _TOKEN.format(index)
            if text.count(f"\n\n{token}\n\n") != 1:
                raise changed(self.page.url, "a table kept as HTML is not a block of its own")
            text = text.replace(token, table)
        return text

    def _one(self, soup: Any, selector: str) -> Any:
        found = soup.select(selector)
        if len(found) != 1:
            raise changed(self.page.url, f"{len(found)} elements match {selector!r}, not one")
        return found[0]

    def _devsite(self, soup: Any) -> tuple[str, list[Any]]:
        """developers.google.com: the title, the article's body and the content footer."""
        heading = self._one(soup, "article h1.devsite-page-title")
        for actions in heading.find_all("devsite-actions"):
            actions.decompose()
        title = " ".join(heading.get_text().split())
        body = self._one(soup, "article div.devsite-article-body")
        footer = self._one(soup, "devsite-content-footer")
        if " ".join(footer.get_text().split()).count(DEVSITE_FOOTER) != 1:
            raise changed(self.page.url, "the content footer does not print DEVSITE_FOOTER")
        return title, [body, footer]

    def _help_article(self, soup: Any) -> tuple[str, list[Any]]:
        """The Gmail Help Center: the article's title and its text, ``div.cc``."""
        heading = self._one(soup, "article section.article-container > h1")
        title = " ".join(heading.get_text().split())
        return title, [self._one(soup, "article div.article-content-container > div.cc")]

    def _prepare(self, root: Any) -> None:
        """Drop the chrome, make links absolute, keep merged tables as HTML (``CAPTURE``)."""
        url = self.page.url
        for element in root.find_all(True):
            if element.decomposed:
                continue
            if element.name in _DROPPED or any(
                name.startswith(_ICONS) for name in _classes(element)
            ):
                element.decompose()
        for element in root.find_all(True):
            if element.name in _UNWRAPPED:
                element.unwrap()
            elif "-" in element.name:
                raise changed(url, f"an element <{element.name}> the script does not know")
        for link in root.find_all("a", href=True):
            link["href"] = urljoin(url, str(link["href"]).strip())
        for image in root.find_all("img"):
            if image.has_attr("src"):
                image["src"] = urljoin(url, str(image["src"]).strip())
            if image.has_attr("alt"):
                image["alt"] = " ".join(str(image["alt"]).split())
        for aside in root.find_all("aside"):
            aside.name = "blockquote"
        for table in root.find_all("table"):
            if table.find_parent("table") is None and _merged(table):
                self.tables.append(_html_table(url, table))
                table.replace_with(_new_paragraph(table, _TOKEN.format(len(self.tables) - 1)))


def _new_paragraph(anchor: Any, text: str) -> Any:
    """A ``<p>`` holding ``text``, made by the document ``anchor`` belongs to."""
    document = anchor
    while document.parent is not None:
        document = document.parent
    paragraph = document.new_tag("p")
    paragraph.string = text
    return paragraph


def _tidy(markdown: str) -> str:
    """Outside fenced code: no trailing whitespace, no run of blank lines; one final newline."""
    lines: list[str] = []
    fenced = False
    for line in markdown.strip().split("\n"):
        if line.lstrip().startswith("```"):
            fenced = not fenced
        elif not fenced:
            line = line.rstrip()
            if not line and (not lines or not lines[-1]):
                continue
        lines.append(line)
    return "\n".join(lines) + "\n"


def reduce(page: Page, response: Response) -> bytes:
    """The content ``page``'s file is written with: Markdown, or the sorted discovery document.

    An HTML page goes through ``_Markdown`` after its HTML comments are removed, and the
    file starts with a line naming the URL it was converted from. The run stops with
    ``changed``, before anything is written, on a page the converter does not recognise or
    when the file would keep another number of HTML tables than ``HTML_TABLES`` says.
    """
    if page.file == SPEC_FILE:
        return discovery(response)
    converter = _Markdown(page)
    markdown = converter.convert(_COMMENT.sub("", decode(response)))
    expected = HTML_TABLES.get(page.file or "", 0)
    if len(converter.tables) != expected:
        found = len(converter.tables)
        raise changed(page.url, f"{found} tables kept as HTML, where {expected} were known")
    header = f"Converted by `scripts/refresh_upstream.py` from {page.url}; see `../FETCHED.md`."
    return _tidy(f"{header}\n\n{markdown}").encode("utf-8")


def capture(page: Page) -> Row:
    """Fetch ``page``, check its attribution and make its file's content; write nothing."""
    response = fetch(page.url, page.status)
    check_attribution(page, response)
    return Row(page, response, reduce(page, response) if page.file else None)


def _iso_http_date(value: str | None) -> str:
    if value is None:
        return "-"
    return parsedate_to_datetime(value).astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _cell(value: str) -> str:
    return value.replace("|", r"\|")


def _paragraph(*sentences: str) -> list[str]:
    text = " ".join(sentences)
    wrapped = textwrap.fill(text, width=88, break_long_words=False, break_on_hyphens=False)
    return [*wrapped.splitlines(), ""]


def _note(row: Row) -> str:
    parts = [f"redirected ({code}) to {target}" for code, target in row.response.redirects]
    if row.page.excerpt:
        parts.append("excerpt of a shared document; Bytes and sha256 are the whole document's")
    if not row.page.stable:
        parts.append("the body changes on every request, so no size, sha256 or validators")
    if row.page.note:
        parts.append(row.page.note)
    return "; ".join(parts) or "-"


def _bullet(text: str) -> list[str]:
    wrapped = textwrap.fill(
        text,
        width=88,
        initial_indent="- ",
        subsequent_indent="  ",
        break_long_words=False,
        break_on_hyphens=False,
    )
    return wrapped.splitlines()


def _attribution() -> list[str]:
    """``FETCHED.md``'s Attribution: every recorded statement, as printed, and where."""
    lines = _paragraph(
        "The pages print these statements of a copyright, a licence or terms of use,",
        "recorded as printed in `ATTRIBUTION` of `scripts/refresh_upstream.py`; the",
        "discovery document states none:",
    )
    for statement in ATTRIBUTION:
        linked = f", a link to {statement.link}" if statement.link else ""
        lines += _bullet(f"`{statement.text}`{linked}: {statement.where}.")
    return [*lines, ""]


def render_fetched(rows: Sequence[Row], fetched: str) -> str:
    """The whole of ``FETCHED.md``, ``fetched`` being the UTC time the run started."""
    lines = [
        f"# Upstream documentation snapshot \N{EM DASH} {SOURCE_NAME}",
        "",
        *_paragraph(
            "Source of truth for every endpoint wrapper (ADR-0003). The files in this",
            "directory are written by `scripts/refresh_upstream.py` (`/refresh-upstream`)",
            "from the pages in the table below and refreshed deliberately by PR, so that",
            "changes show up as diffs. This file is generated by the same script; nothing",
            "here is edited by hand.",
        ),
        "## Attribution",
        "",
        *_attribution(),
        *_paragraph(
            "The script stops when a page it reads states one it does not record. The",
            "documentation's text is reproduced here as this package's reference",
            "(ADR-0003); the publisher's objection is met by replacing it with a summary",
            "that keeps the names and semantics the wrappers rely on.",
        ),
        "## How this is captured",
        "",
        *(_paragraph(CAPTURE) if CAPTURE else []),
        *_paragraph(
            "Fetched is the UTC time the run started, in ISO 8601 with an explicit Z, the",
            "same for every row. Bytes and sha256 are those of the body as fetched: a",
            "converted page's row holds the HTML page's, an excerpt's the whole document's.",
            "Last-Modified is the HTTP header in UTC, ETag as sent. A page recorded without",
            "a file is `(no file)`, and a body that changes on every request records no",
            f"size, sha256 or validators. Requests go one at a time, {PAUSE:g} s apart,",
            f"each retried at most once, identified as `{USER_AGENT}`; no API key is sent",
            "or read.",
        ),
        "## Files and pages",
        "",
        "| File | URL | Fetched | HTTP | Bytes | sha256 | ETag | Last-Modified | Note |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for row in rows:
        response, kept = row.response, row.page.stable
        cells = [
            row.page.file or "(no file)",
            response.url,
            fetched,
            str(response.status),
            str(len(response.body)) if kept else "-",
            hashlib.sha256(response.body).hexdigest() if kept else "-",
            (response.etag or "-") if kept else "-",
            _iso_http_date(response.last_modified) if kept else "-",
            _note(row),
        ]
        lines.append("| " + " | ".join(_cell(cell) for cell in cells) + " |")
    lines += ["", f"Regenerate: `{COMMAND}`"]
    return "\n".join(lines) + "\n"


def _files(rows: Sequence[Row]) -> dict[str, bytes]:
    return {row.page.file: row.content for row in rows if row.page.file and row.content is not None}


def _stale(out: Path, files: dict[str, bytes]) -> list[str]:
    """The files under the subdirectories the run writes to that it does not produce."""
    subdirs = sorted({name.split("/", 1)[0] for name in files if "/" in name})
    found = [path for subdir in subdirs for path in sorted((out / subdir).rglob("*"))]
    names = [path.relative_to(out).as_posix() for path in found if path.is_file()]
    return [name for name in names if name not in files]


def write_snapshot(out: Path, rows: Sequence[Row], fetched_md: str) -> None:
    """Write every file as ``reduce`` made it, then ``FETCHED.md``; remove the stale files."""
    if out.exists() and any(out.iterdir()) and not (out / "FETCHED.md").is_file():
        raise SystemExit(f"{out} is neither empty nor a snapshot (no FETCHED.md); not writing")
    files = _files(rows)
    for name in _stale(out, files):
        (out / name).unlink()
        _log(f"  removed {name}: no longer produced")
    for name, content in files.items():
        (out / name).parent.mkdir(parents=True, exist_ok=True)
        (out / name).write_bytes(content)
    (out / "FETCHED.md").write_bytes(fetched_md.encode("utf-8"))


def compare(out: Path, rows: Sequence[Row]) -> list[str]:
    """The snapshot's files that differ from this run's, ``FETCHED.md``'s ``Fetched`` aside."""
    files = _files(rows)
    differ = [
        name
        for name, content in files.items()
        if not (out / name).is_file() or (out / name).read_bytes() != content
    ]
    differ += [f"{name} (no longer produced)" for name in _stale(out, files)]
    current = out / "FETCHED.md"
    old = current.read_bytes().decode("utf-8") if current.is_file() else ""
    match = _FETCHED.search(old, max(old.find("| --- |"), 0))
    if not match or render_fetched(rows, match.group(1)) != old:
        differ.append("FETCHED.md")
    return differ


def main(argv: Sequence[str] | None = None) -> int:
    """Fetch every page, then write the snapshot, or with ``--check`` compare it."""
    parser = argparse.ArgumentParser(description="Refresh docs/upstream/ from upstream.")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="snapshot directory")
    parser.add_argument(
        "--check", action="store_true", help="fetch and compare with the snapshot; write nothing"
    )
    args = parser.parse_args(argv)
    out: Path = args.out
    pages = list(PAGES)
    if SPEC_URL:
        pages.append(Page(SPEC_URL, SPEC_FILE, note=SPEC_NOTE, stable=False))
    if not pages:
        raise SystemExit(
            "Nothing to fetch: fill PAGES, and SPEC_URL where the documentation names an "
            "API document, as /refresh-upstream says."
        )
    fetched = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    _log(f"Fetching {len(pages)} pages; nothing is written before the last")
    rows = [capture(page) for page in pages]
    if args.check:
        differ = compare(out, rows)
        for name in differ:
            _log(f"  differs: {name}")
        _log(
            f"{len(differ)} of the snapshot's files differ from upstream; run without --check"
            if differ
            else "The snapshot is current: upstream serves what it records."
        )
        return 1 if differ else 0
    write_snapshot(out, rows, render_fetched(rows, fetched))
    written = sum(1 for row in rows if row.content is not None)
    _log(f"Wrote {written} files and FETCHED.md under {out} (Fetched {fetched})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
