"""``gmail.users.messages.send``: ``POST gmail/v1/users/{userId}/messages/send``, a message sent.

Reference: ``docs/upstream/discovery.json``, ``resources.users.resources.messages.methods.send``:
"Sends the specified message to the recipients in the ``To``, ``Cc``, and ``Bcc``
headers."; the path's ``userId`` is ``me``, which ``BASE_URL`` fixes. The request body is
``schemas.Message``: ``raw``, "The entire email message in an RFC 2822 formatted and
base64url encoded string" (required for ``gmail.users.messages.send``), and, for a reply,
``threadId``, "the ID of the thread the message belongs to", with the ``References`` and
``In-Reply-To`` headers set and the ``Subject`` headers matching. The answer is
``schemas.Message``. A hundred quota units (``docs/upstream/pages/quota.md``).

The method's media upload (``/upload/gmail/v1/...``) is not used: the message goes in
``raw``.
"""

from typing import Any

from xippios.core import AsyncClient, parse
from xippios.core.client import decode_json

from xippios.google.mail._client import SOURCE
from xippios.google.mail.models import Message

__all__ = ["messages_send"]

ENDPOINT = "gmail.users.messages.send"
"""The operation's id in the discovery document, ``meta.endpoint`` verbatim."""


async def messages_send(client: AsyncClient, *, raw: str, thread_id: str | None = None) -> Message:
    """gmail.users.messages.send: send a message to its To, Cc and Bcc; 100 units.

    Sends ``{"raw": raw, "threadId": thread_id}``, ``threadId`` left out when ``None``.
    A ``POST`` is never retried after a 5xx or a transport failure (ADR-0011): the
    message may have gone.

    Args:
        client: The account's client, ``_client.client_for(account)``.
        raw: ``raw``, the message's RFC 2822 text, base64url encoded
            (``_compose.encode_raw``).
        thread_id: ``threadId``, the thread a reply joins, e.g. ``"19a0b0c0d0e0f001"``.
    """
    body: dict[str, Any] = {"raw": raw}
    if thread_id is not None:
        body["threadId"] = thread_id
    response = await client.request("POST", "messages/send", json=body)
    return parse(Message, decode_json(response, source=SOURCE), source=SOURCE, endpoint=ENDPOINT)
