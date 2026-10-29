"""Task functions, the public API: the adapters serve ``__all__`` (conventions §2).

``/add-endpoint`` adds them.

``account: str`` is the first, required parameter of every task: the name of a declared
account (``demo``, ``work``), whose client (``_client.client_for``) the task hands to the
endpoint wrappers. No task spans accounts. The one exception is ``list_accounts``, the
package's tenant list (``_tenants.list_task``, queue row 281): no argument, the declared
accounts' names only, so an agent can form valid calls.

``meta.endpoint`` is the operation's id in the discovery document
(``gmail.users.getProfile``), ``meta.tenant`` the account the data came from, by name
(ADR-0014, decision 5), and ``meta.request`` holds ``account`` plus the parameters sent,
under upstream's names (``labelIds``, ``maxResults``, ``format``), and a parameter the
task applies itself under its own name (``include_html``, ``start``, ``limit``); never a
body. Gmail publishes no release time or identifier for a mailbox, so ``published_at``
and ``vintage`` stay empty, and it reports no count of what a query matches, only
``resultSizeEstimate``, an estimate, so ``meta.total`` stays ``None`` (ADR-0012).

The message tasks return projections (conventions §3): ``get_message`` and
``get_thread`` the decoded view of ADR-0025 (``_decode.decode_message``), whose faithful
MIME tree stays on the wrapper's ``Message``, and ``search_messages`` a ``SearchRow`` per
message.
"""

from collections.abc import Mapping
from typing import Any

from xippios.core import InvalidRequestError, Result, ResultCap, ResultMeta, sync

from xippios.google.mail import api
from xippios.google.mail._client import SOURCE, TENANTS, client_for
from xippios.google.mail._decode import decode_message, header_value
from xippios.google.mail._tenants import list_task
from xippios.google.mail.api.get_profile import ENDPOINT as GET_PROFILE
from xippios.google.mail.api.labels_list import ENDPOINT as LABELS_LIST
from xippios.google.mail.api.messages_get import ENDPOINT as MESSAGES_GET
from xippios.google.mail.api.messages_list import ENDPOINT as MESSAGES_LIST
from xippios.google.mail.api.threads_get import ENDPOINT as THREADS_GET
from xippios.google.mail.models import (
    DecodedMessage,
    Label,
    ListMessagesResponse,
    Profile,
    SearchRow,
)

__all__ = [
    "get_message",
    "get_profile",
    "get_thread",
    "list_accounts",
    "list_labels",
    "list_messages",
    "search_messages",
]

# A task named here is Python-only: no tool, no route; the adapters skip it (ADR-0019 decision 4).
BULK_TASKS: frozenset[str] = frozenset()

MCP_CAPS: Mapping[str, ResultCap] = {
    # A message is no list: over the default 64 KB core returns the result's first 64 KB
    # as a preview after "result is <n> bytes, over the cap of 65536; " and this note,
    # which says what was cut and what the agent can do; no start resumes it (ADR-0016;
    # ADR-0025, "Over the cap a long text or html comes back as a preview plus the note").
    "get_message": ResultCap(
        note=(
            "the preview is the first 64 KB of the result, so a long message's text or html "
            "is cut; call with include_html=False for the text alone, or read the message "
            "in a mail client; there is no start to resume with"
        )
    ),
    # A thread's messages can pass the default 64 KB; the note names the two parameters
    # that page through it, and core's cut ends "; resume with start=<start + shown>"
    # from meta.request's start (conventions §8, ADR-0016 and its amendment).
    "get_thread": ResultCap(
        note=(
            "page through the thread with start and limit; a message too long for the cap "
            "reads alone with google_mail_get_message"
        )
    ),
}
"""Per-tool result caps, keyed by the task function's name (conventions §8, ADR-0016)."""

SEARCH_MAX_RESULTS = 50
"""The most messages ``search_messages`` reads: 5 + 20 x 50 = 1,005 quota units."""
# The bound is the task's, so it holds for every caller, Python, MCP and HTTP alike
# (ADR-0001: no logic in an adapter).
SEARCH_HEADERS = ["From", "Subject", "Date"]
"""The ``metadataHeaders`` ``search_messages`` asks for, the three a ``SearchRow`` shows."""


def _meta(account: str, endpoint: str, params: Mapping[str, Any]) -> ResultMeta:
    """``meta`` of a task's result: ``account`` as the tenant and first in ``request``."""
    return ResultMeta(
        source=SOURCE,
        endpoint=endpoint,
        request={"account": account, **params},
        tenant=account,
    )


LIST_ACCOUNTS_DOC = """List the Gmail accounts this server can reach, by name.

On a server that serves every declared account, every other tool takes one of these names
as ``account`` to choose the mailbox it reads, e.g. ``demo`` or ``work``; on a server
started with ``--account`` the tools act on that one account and this list shows it
alone. Only names come back, never an address, a token or another value; an empty list
means that no account is declared where the server runs.
"""

list_accounts = list_task(TENANTS, name="list_accounts", doc=LIST_ACCOUNTS_DOC, source=SOURCE)
"""The declared accounts' names (queue row 281); ``meta.tenant`` is ``None``."""


async def get_profile_async(account: str) -> Result[Profile]:
    """Get an account's Gmail profile: its address, message and thread counts, history id.

    ``email_address`` is the mailbox's address, ``messages_total`` and ``threads_total``
    count every message and thread in it, and ``history_id`` is the id of the mailbox's
    current history record. One call, one quota unit.

    Args:
        account: A declared account's name, e.g. ``demo`` or ``work``;
            ``google_mail_list_accounts`` lists them.
    """
    profile = await api.get_profile(client_for(account))
    return Result(data=profile, meta=_meta(account, GET_PROFILE, {}))


get_profile = sync(get_profile_async)


async def list_labels_async(account: str) -> Result[list[Label]]:
    """List the labels of an account's mailbox, the system labels and the user's own.

    Each label's ``id`` is what ``label_ids`` of ``google_mail_list_messages`` and
    ``google_mail_search_messages`` takes (``INBOX``, ``UNREAD``, ``Label_1``), ``name``
    its display name, ``type`` ``system`` or ``user``. One call, one quota unit.

    Args:
        account: A declared account's name, e.g. ``demo`` or ``work``;
            ``google_mail_list_accounts`` lists them.
    """
    answer = await api.labels_list(client_for(account))
    return Result(data=answer.labels, meta=_meta(account, LABELS_LIST, {}))


list_labels = sync(list_labels_async)


async def list_messages_async(
    account: str,
    *,
    q: str | None = None,
    label_ids: list[str] | None = None,
    max_results: int | None = None,
    page_token: str | None = None,
    include_spam_trash: bool = False,
) -> Result[ListMessagesResponse]:
    """List the ids of an account's messages that match a query, one page at a time.

    Ids only: each item is a message's ``id`` and ``thread_id``, for
    ``google_mail_get_message`` and ``google_mail_get_thread``;
    ``google_mail_search_messages`` shows sender, subject and date too.
    ``next_page_token`` continues the list, and ``result_size_estimate`` is upstream's
    estimate of the matches, not a count. One call, five quota units.

    Args:
        account: A declared account's name, e.g. ``demo`` or ``work``;
            ``google_mail_list_accounts`` lists them.
        q: A query in Gmail's own search syntax, sent verbatim, e.g.
            ``"from:alice@example.com newer_than:7d"``, ``"is:unread has:attachment"`` or
            ``"subject:invoice after:2026/01/01"``; omitted, every message matches.
        label_ids: Label ids every message must carry, e.g. ``["INBOX"]`` or
            ``["INBOX", "UNREAD"]``; ``google_mail_list_labels`` lists them.
        max_results: Ids per page, e.g. ``10``; upstream's default 100, at most 500.
        page_token: The ``next_page_token`` of the previous page, to continue the list.
        include_spam_trash: ``True`` lists messages in SPAM and TRASH too.
    """
    answer = await api.messages_list(
        client_for(account),
        q=q,
        label_ids=label_ids,
        max_results=max_results,
        page_token=page_token,
        include_spam_trash=include_spam_trash or None,
    )
    sent = {
        "q": q,
        "labelIds": label_ids,
        "maxResults": max_results,
        "pageToken": page_token,
        "includeSpamTrash": include_spam_trash or None,
    }
    return Result(data=answer, meta=_meta(account, MESSAGES_LIST, sent))


list_messages = sync(list_messages_async)


async def search_messages_async(
    account: str,
    q: str,
    *,
    label_ids: list[str] | None = None,
    max_results: int = 20,
) -> Result[list[SearchRow]]:
    """Search an account's messages and show each match's sender, subject, date and snippet.

    In the order upstream lists them, at most ``max_results`` messages, 50 at most: a
    search costs one list call, 5 quota units, and one call per message found, 20 units
    each, so 50 messages are 1,005 of the 6,000 units an account may spend in a minute,
    and the bound keeps one search well inside it. ``google_mail_get_message`` reads a
    message found, by ``id``; ``google_mail_list_messages`` pages through the ids alone.

    Args:
        account: A declared account's name, e.g. ``demo`` or ``work``;
            ``google_mail_list_accounts`` lists them.
        q: A query in Gmail's own search syntax, sent verbatim, e.g.
            ``"from:alice@example.com newer_than:7d"``, ``"subject:invoice"`` or
            ``"is:unread label:work"``.
        label_ids: Label ids every message must carry, e.g. ``["INBOX"]``;
            ``google_mail_list_labels`` lists them.
        max_results: How many messages to show, from 1 to 50, e.g. ``10``; default 20.

    Raises:
        InvalidRequestError: ``max_results`` is below 1 or above 50, before any request.
    """
    if not 1 <= max_results <= SEARCH_MAX_RESULTS:
        raise InvalidRequestError(
            f"max_results is {max_results}; search_messages reads 1 to "
            f"{SEARCH_MAX_RESULTS} messages, 5 + 20 quota units each, so narrow q or page "
            "through the ids with list_messages",
            source=SOURCE,
        )
    client = client_for(account)
    found = await api.messages_list(client, q=q, label_ids=label_ids, max_results=max_results)
    rows = []
    for ref in found.messages:
        message = await api.messages_get(
            client, ref.id, format="metadata", metadata_headers=SEARCH_HEADERS
        )
        headers = message.payload.headers if message.payload is not None else []
        rows.append(
            SearchRow.project(
                message,
                from_=header_value(headers, "From"),
                subject=header_value(headers, "Subject"),
                date=header_value(headers, "Date"),
            )
        )
    sent = {
        "q": q,
        "labelIds": label_ids,
        "maxResults": max_results,
        "format": "metadata",
        "metadataHeaders": SEARCH_HEADERS,
    }
    return Result(data=rows, meta=_meta(account, MESSAGES_LIST, sent))


search_messages = sync(search_messages_async)


async def get_message_async(
    account: str, message_id: str, *, include_html: bool = False
) -> Result[DecodedMessage]:
    """Read one message of an account: its headers, its text and its attachments' names.

    ``headers`` holds From, To, Cc, Bcc, Date, Subject, Message-ID, In-Reply-To and
    References as the message gives them, ``text`` the message's plain text (``None``
    when it has none), ``attachments`` each attachment's file name, type and size, not
    its content; ``html`` the HTML version, only when asked for. One call, 60 quota
    units.

    Args:
        account: A declared account's name, e.g. ``demo`` or ``work``;
            ``google_mail_list_accounts`` lists them.
        message_id: The message's ``id``, e.g. ``"19a0b0c0d0e0f001"``, from
            ``google_mail_list_messages`` or ``google_mail_search_messages``.
        include_html: ``True`` returns the HTML version in ``html`` as well, for a
            message whose plain text is missing or poor; it makes the answer longer.
    """
    message = await api.messages_get(client_for(account), message_id, format="full")
    sent = {"id": message_id, "format": "full", "include_html": include_html}
    return Result(
        data=decode_message(message, include_html=include_html),
        meta=_meta(account, MESSAGES_GET, sent),
    )


get_message = sync(get_message_async)


async def get_thread_async(
    account: str, thread_id: str, *, start: int = 0, limit: int | None = None
) -> Result[list[DecodedMessage]]:
    """Read a conversation of an account: its messages, in upstream's order, as text.

    Each message as ``google_mail_get_message`` shows it, its HTML version left out:
    headers, text and attachments' names. ``start`` and ``limit`` read a long thread in
    parts; a cut answer says where to resume. One call, 120 quota units, whatever part
    is shown.

    Args:
        account: A declared account's name, e.g. ``demo`` or ``work``;
            ``google_mail_list_accounts`` lists them.
        thread_id: The thread's id, a message's ``thread_id``, e.g.
            ``"19a0b0c0d0e0f001"``.
        start: The first message to show, counted from 0 in the thread's order; e.g.
            ``0`` or ``5``.
        limit: How many messages to show from ``start``, e.g. ``5``; omitted, every
            message from ``start`` on.

    Raises:
        InvalidRequestError: ``start`` is below 0 or ``limit`` below 1, before any
            request.
    """
    if start < 0:
        raise InvalidRequestError(f"start is {start}; it counts messages from 0", source=SOURCE)
    if limit is not None and limit < 1:
        raise InvalidRequestError(
            f"limit is {limit}; show at least 1 message, or omit it", source=SOURCE
        )
    thread = await api.threads_get(client_for(account), thread_id, format="full")
    end = None if limit is None else start + limit
    views = [decode_message(message) for message in thread.messages[start:end]]
    sent = {"id": thread_id, "format": "full", "start": start, "limit": limit}
    return Result(data=views, meta=_meta(account, THREADS_GET, sent))


get_thread = sync(get_thread_async)
