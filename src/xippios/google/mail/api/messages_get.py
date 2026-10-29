"""``gmail.users.messages.get``: ``GET gmail/v1/users/{userId}/messages/{id}``, one message.

Reference: ``docs/upstream/discovery.json``, ``resources.users.resources.messages.methods.get``:
"Gets the specified message."; the path's ``userId`` is ``me``, which ``BASE_URL`` fixes,
and ``id`` the message's. The query parameters: ``format`` (``minimal``, ``full``,
``raw`` or ``metadata``, default ``full``) and ``metadataHeaders`` (repeated: "When given
and format is ``METADATA``, only include headers specified."). The answer is
``schemas.Message``. Quota units (``docs/upstream/pages/quota.md``): 20, or 60 when
``format`` is ``full``, upstream's default, or ``raw``.

``raw`` is not offered: 0.1.0 reads no message as its RFC 2822 text (the briefing's
scope), so ``format`` takes ``minimal``, ``metadata`` or ``full``.
"""

from typing import Literal
from urllib.parse import quote

from xippios.core import AsyncClient, parse

from xippios.google.mail._client import SOURCE
from xippios.google.mail.models import Message

__all__ = ["MessageFormat", "messages_get"]

ENDPOINT = "gmail.users.messages.get"
"""The operation's id in the discovery document, ``meta.endpoint`` verbatim."""

MessageFormat = Literal["minimal", "full", "metadata"]
"""The values of ``format`` this wrapper sends, upstream's but ``raw``."""


async def messages_get(
    client: AsyncClient,
    id: str,
    *,
    format: MessageFormat | None = None,
    metadata_headers: list[str] | None = None,
) -> Message:
    """gmail.users.messages.get: one message; 20 units, 60 with ``format`` ``full``.

    A parameter left ``None`` is not sent, so upstream's default applies: ``format``
    ``full``, 60 units.

    Args:
        client: The account's client, ``_client.client_for(account)``.
        id: ``id``, the message's id, e.g. ``"1a18715327e2e304"``, from
            ``messages.list``; sent as one path segment.
        format: ``format``: ``"minimal"``, ``"metadata"`` or ``"full"``.
        metadata_headers: ``metadataHeaders``, one query parameter per header name, e.g.
            ``["From", "Subject", "Date"]``; upstream reads it with ``format``
            ``metadata`` only.
    """
    sent = {"format": format, "metadataHeaders": metadata_headers}
    params = {name: value for name, value in sent.items() if value is not None}
    raw = await client.get_json(f"messages/{quote(id, safe='')}", params=params or None)
    return parse(Message, raw, source=SOURCE, endpoint=ENDPOINT)
