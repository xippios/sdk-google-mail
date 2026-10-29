"""``gmail.users.threads.get``: ``GET gmail/v1/users/{userId}/threads/{id}``, one thread.

Reference: ``docs/upstream/discovery.json``, ``resources.users.resources.threads.methods.get``:
"Gets the specified thread."; the path's ``userId`` is ``me``, which ``BASE_URL`` fixes,
and ``id`` the thread's. The query parameters: ``format`` (``full``, ``metadata`` or
``minimal``, default ``full``) and ``metadataHeaders`` (repeated: "When given and format
is METADATA, only include headers specified."). The answer is ``schemas.Thread``, its
messages in that format. Quota units (``docs/upstream/pages/quota.md``): 40, or 120 when
``format`` is ``full``, upstream's default.
"""

from typing import Literal
from urllib.parse import quote

from xippios.core import AsyncClient, parse

from xippios.google.mail._client import SOURCE
from xippios.google.mail.models import Thread

__all__ = ["ThreadFormat", "threads_get"]

ENDPOINT = "gmail.users.threads.get"
"""The operation's id in the discovery document, ``meta.endpoint`` verbatim."""

ThreadFormat = Literal["full", "metadata", "minimal"]
"""The values of ``format`` the discovery document lists for ``threads.get``."""


async def threads_get(
    client: AsyncClient,
    id: str,
    *,
    format: ThreadFormat | None = None,
    metadata_headers: list[str] | None = None,
) -> Thread:
    """gmail.users.threads.get: one thread and its messages; 40 units, 120 with ``full``.

    A parameter left ``None`` is not sent, so upstream's default applies: ``format``
    ``full``, 120 units.

    Args:
        client: The account's client, ``_client.client_for(account)``.
        id: ``id``, the thread's id, e.g. ``"1a18715327e2e304"``, a message's
            ``threadId``; sent as one path segment.
        format: ``format``: ``"full"``, ``"metadata"`` or ``"minimal"``.
        metadata_headers: ``metadataHeaders``, one query parameter per header name, e.g.
            ``["From", "Subject"]``; upstream reads it with ``format`` ``metadata`` only.
    """
    sent = {"format": format, "metadataHeaders": metadata_headers}
    params = {name: value for name, value in sent.items() if value is not None}
    raw = await client.get_json(f"threads/{quote(id, safe='')}", params=params or None)
    return parse(Thread, raw, source=SOURCE, endpoint=ENDPOINT)
