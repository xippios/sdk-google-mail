"""``gmail.users.labels.list``: ``GET gmail/v1/users/{userId}/labels``, the mailbox's labels.

Reference: ``docs/upstream/discovery.json``, ``resources.users.resources.labels.methods.list``:
"Lists all labels in the user's mailbox."; its one parameter, the path's ``userId``, is
``me``, which ``BASE_URL`` fixes. The answer is ``schemas.ListLabelsResponse``. One quota
unit (``docs/upstream/pages/quota.md``).
"""

from xippios.core import AsyncClient, parse

from xippios.google.mail._client import SOURCE
from xippios.google.mail.models import ListLabelsResponse

__all__ = ["labels_list"]

ENDPOINT = "gmail.users.labels.list"
"""The operation's id in the discovery document, ``meta.endpoint`` verbatim."""


async def labels_list(client: AsyncClient) -> ListLabelsResponse:
    """gmail.users.labels.list: every label of the mailbox ``client`` signs for; 1 unit.

    Args:
        client: The account's client, ``_client.client_for(account)``.
    """
    raw = await client.get_json("labels")
    return parse(ListLabelsResponse, raw, source=SOURCE, endpoint=ENDPOINT)
