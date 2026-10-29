"""``gmail.users.drafts.delete``: ``DELETE gmail/v1/users/{userId}/drafts/{id}``, a draft gone.

Reference: ``docs/upstream/discovery.json``, ``resources.users.resources.drafts.methods.delete``:
"Immediately and permanently deletes the specified draft. Does not simply trash it."; the
path's ``userId`` is ``me``, which ``BASE_URL`` fixes, and ``id`` "The ID of the draft to
delete.", the draft's own id, not its message's. The method has no ``response``: the
answer carries no body. Ten quota units (``docs/upstream/pages/quota.md``).
"""

from urllib.parse import quote

from xippios.core import AsyncClient

__all__ = ["drafts_delete"]

ENDPOINT = "gmail.users.drafts.delete"
"""The operation's id in the discovery document, ``meta.endpoint`` verbatim."""


async def drafts_delete(client: AsyncClient, id: str) -> None:
    """gmail.users.drafts.delete: delete a draft permanently; 10 units; no answer body.

    An error answer raises through the client (``classify``, then core's mapping: a 404
    is ``NotFoundError``); a success's body, empty, is not read. A ``DELETE`` is not
    retried after a 5xx or a transport failure (ADR-0011).

    Args:
        client: The account's client, ``_client.client_for(account)``.
        id: ``id``, the draft's id, e.g. ``"r-1234567890123456789"``, a ``Draft``'s
            ``id``; sent as one path segment.
    """
    await client.request("DELETE", f"drafts/{quote(id, safe='')}")
