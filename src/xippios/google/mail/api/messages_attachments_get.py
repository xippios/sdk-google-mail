"""``gmail.users.messages.attachments.get``: one attachment's body.

``GET gmail/v1/users/{userId}/messages/{messageId}/attachments/{id}``. Reference:
``docs/upstream/discovery.json``,
``resources.users.resources.messages.resources.attachments.methods.get``: "Gets the
specified message attachment."; the path's ``userId`` is ``me``, which ``BASE_URL``
fixes, ``messageId`` the message's id and ``id`` the attachment's, a part's
``body.attachmentId``. The answer is ``schemas.MessagePartBody``, the content in
``data``, base64url. 20 quota units (``docs/upstream/pages/quota.md``).

A wrapper without a task in 0.1.0: attachment content is served over no tool (the
briefing's scope); a decoded message lists its attachments' metadata only.
"""

from urllib.parse import quote

from xippios.core import AsyncClient, parse

from xippios.google.mail._client import SOURCE
from xippios.google.mail.models import MessagePartBody

__all__ = ["messages_attachments_get"]

ENDPOINT = "gmail.users.messages.attachments.get"
"""The operation's id in the discovery document, ``meta.endpoint`` verbatim."""


async def messages_attachments_get(
    client: AsyncClient,
    message_id: str,
    id: str,
) -> MessagePartBody:
    """gmail.users.messages.attachments.get: an attachment's body, base64url; 20 units.

    Args:
        client: The account's client, ``_client.client_for(account)``.
        message_id: ``messageId``, the id of the message holding the attachment, e.g.
            ``"1a18715327e2e304"``; one path segment.
        id: ``id``, the attachment's id, a part's ``body.attachmentId``; one path
            segment.
    """
    path = f"messages/{quote(message_id, safe='')}/attachments/{quote(id, safe='')}"
    raw = await client.get_json(path)
    return parse(MessagePartBody, raw, source=SOURCE, endpoint=ENDPOINT)
