"""``gmail.users.messages.trash``: ``POST gmail/v1/users/{userId}/messages/{id}/trash``.

Reference: ``docs/upstream/discovery.json``,
``resources.users.resources.messages.methods.trash``: "Moves the specified message to the
trash."; the path's ``userId`` is ``me``, which ``BASE_URL`` fixes, and ``id`` "The ID of
the message to Trash.". The method takes no request body. The answer is
``schemas.Message``. Twenty quota units (``docs/upstream/pages/quota.md``).
"""

from urllib.parse import quote

from xippios.core import AsyncClient, parse
from xippios.core.client import decode_json

from xippios.google.mail._client import SOURCE
from xippios.google.mail.models import Message

__all__ = ["messages_trash"]

ENDPOINT = "gmail.users.messages.trash"
"""The operation's id in the discovery document, ``meta.endpoint`` verbatim."""


async def messages_trash(client: AsyncClient, id: str) -> Message:
    """gmail.users.messages.trash: move a message to the trash; 20 units.

    Sends no body. A ``POST`` is never retried after a 5xx or a transport failure
    (ADR-0011).

    Args:
        client: The account's client, ``_client.client_for(account)``.
        id: ``id``, the message's id, e.g. ``"19a0b0c0d0e0f001"``; sent as one path
            segment.
    """
    response = await client.request("POST", f"messages/{quote(id, safe='')}/trash")
    return parse(Message, decode_json(response, source=SOURCE), source=SOURCE, endpoint=ENDPOINT)
