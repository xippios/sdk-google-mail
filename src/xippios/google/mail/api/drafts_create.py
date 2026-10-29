"""``gmail.users.drafts.create``: ``POST gmail/v1/users/{userId}/drafts``, a new draft.

Reference: ``docs/upstream/discovery.json``, ``resources.users.resources.drafts.methods.create``:
"Creates a draft with the ``DRAFT`` label."; the path's ``userId`` is ``me``, which
``BASE_URL`` fixes. The request body is ``schemas.Draft``, whose ``message`` carries
``raw``, "The entire email message in an RFC 2822 formatted and base64url encoded string"
(``schemas.Message``, "@required gmail.users.drafts.create"), and, for a reply, the
``threadId`` of the conversation: "To add a message or draft to a thread", ``threadId`` is
"specified on the ``Message`` or ``Draft.Message`` you supply", the ``References`` and
``In-Reply-To`` headers are set and the ``Subject`` headers match. The answer is
``schemas.Draft``. Ten quota units (``docs/upstream/pages/quota.md``).

The method's media upload (``/upload/gmail/v1/...``) is not used: the message goes in
``raw``.
"""

from typing import Any

from xippios.core import AsyncClient, parse
from xippios.core.client import decode_json

from xippios.google.mail._client import SOURCE
from xippios.google.mail.models import Draft

__all__ = ["drafts_create"]

ENDPOINT = "gmail.users.drafts.create"
"""The operation's id in the discovery document, ``meta.endpoint`` verbatim."""


async def drafts_create(client: AsyncClient, *, raw: str, thread_id: str | None = None) -> Draft:
    """gmail.users.drafts.create: store a message as a draft; 10 units.

    Sends ``{"message": {"raw": raw, "threadId": thread_id}}``, ``threadId`` left out
    when ``None``. A ``POST`` is never retried after a 5xx or a transport failure
    (ADR-0011).

    Args:
        client: The account's client, ``_client.client_for(account)``.
        raw: ``message.raw``, the message's RFC 2822 text, base64url encoded
            (``_compose.encode_raw``).
        thread_id: ``message.threadId``, the thread a reply joins, e.g.
            ``"19a0b0c0d0e0f001"``.
    """
    message: dict[str, Any] = {"raw": raw}
    if thread_id is not None:
        message["threadId"] = thread_id
    response = await client.request("POST", "drafts", json={"message": message})
    return parse(Draft, decode_json(response, source=SOURCE), source=SOURCE, endpoint=ENDPOINT)
