"""``gmail.users.messages.modify``: ``POST gmail/v1/users/{userId}/messages/{id}/modify``.

Reference: ``docs/upstream/discovery.json``,
``resources.users.resources.messages.methods.modify``: "Modifies the labels and the
Classification Label values on the specified message."; the path's ``userId`` is ``me``,
which ``BASE_URL`` fixes, and ``id`` "The ID of the message to modify.". The request body
is ``schemas.ModifyMessageRequest``: ``addLabelIds`` and ``removeLabelIds``, up to 100
labels each "with each update"; its Classification Label fields, for Google Workspace
accounts, are not sent. The answer is ``schemas.Message``. Five quota units
(``docs/upstream/pages/quota.md``).
"""

from urllib.parse import quote

from xippios.core import AsyncClient, parse
from xippios.core.client import decode_json

from xippios.google.mail._client import SOURCE
from xippios.google.mail.models import Message, ModifyMessageRequest

__all__ = ["messages_modify"]

ENDPOINT = "gmail.users.messages.modify"
"""The operation's id in the discovery document, ``meta.endpoint`` verbatim."""


async def messages_modify(
    client: AsyncClient,
    id: str,
    *,
    add_label_ids: list[str] | None = None,
    remove_label_ids: list[str] | None = None,
) -> Message:
    """gmail.users.messages.modify: add and remove a message's labels; 5 units.

    Sends ``{"addLabelIds": [...], "removeLabelIds": [...]}``, a list left out when
    ``None`` or empty. A ``POST`` is never retried after a 5xx or a transport failure
    (ADR-0011).

    Args:
        client: The account's client, ``_client.client_for(account)``.
        id: ``id``, the message's id, e.g. ``"19a0b0c0d0e0f001"``; sent as one path
            segment.
        add_label_ids: ``addLabelIds``, e.g. ``["STARRED"]``.
        remove_label_ids: ``removeLabelIds``, e.g. ``["UNREAD"]``.
    """
    request = ModifyMessageRequest(
        add_label_ids=add_label_ids or [], remove_label_ids=remove_label_ids or []
    )
    body = {name: value for name, value in request.dump_upstream().items() if value}
    response = await client.request("POST", f"messages/{quote(id, safe='')}/modify", json=body)
    return parse(Message, decode_json(response, source=SOURCE), source=SOURCE, endpoint=ENDPOINT)
