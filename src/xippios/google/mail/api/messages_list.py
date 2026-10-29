"""``gmail.users.messages.list``: ``GET gmail/v1/users/{userId}/messages``, message ids.

Reference: ``docs/upstream/discovery.json``,
``resources.users.resources.messages.methods.list``: "Lists the messages in the user's
mailbox."; the path's ``userId`` is ``me``, which ``BASE_URL`` fixes. The query
parameters, under upstream's names: ``q`` ("Only return messages matching the specified
query. Supports the same query format as the Gmail search box."), ``labelIds`` (repeated:
"Only return messages with labels that match all of the specified label IDs."),
``maxResults`` (default 100, at most 500), ``pageToken`` and ``includeSpamTrash`` (default
``false``). The answer is ``schemas.ListMessagesResponse``, ids only. Five quota units
(``docs/upstream/pages/quota.md``).
"""

from xippios.core import AsyncClient, parse

from xippios.google.mail._client import SOURCE
from xippios.google.mail.models import ListMessagesResponse

__all__ = ["messages_list"]

ENDPOINT = "gmail.users.messages.list"
"""The operation's id in the discovery document, ``meta.endpoint`` verbatim."""


async def messages_list(
    client: AsyncClient,
    *,
    q: str | None = None,
    label_ids: list[str] | None = None,
    max_results: int | None = None,
    page_token: str | None = None,
    include_spam_trash: bool | None = None,
) -> ListMessagesResponse:
    """gmail.users.messages.list: one page of the ids of the messages that match; 5 units.

    A parameter left ``None`` is not sent, so upstream's default applies.

    Args:
        client: The account's client, ``_client.client_for(account)``.
        q: ``q``, sent verbatim, e.g. ``"from:someuser@example.com is:unread"``.
        label_ids: ``labelIds``, one query parameter per id, e.g. ``["INBOX", "UNREAD"]``.
        max_results: ``maxResults``, e.g. ``10``; upstream's default 100, at most 500.
        page_token: ``pageToken``, a previous page's ``nextPageToken``.
        include_spam_trash: ``includeSpamTrash``, sent as ``true`` or ``false``.
    """
    sent = {
        "q": q,
        "labelIds": label_ids,
        "maxResults": max_results,
        "pageToken": page_token,
        "includeSpamTrash": include_spam_trash,
    }
    params = {name: value for name, value in sent.items() if value is not None}
    raw = await client.get_json("messages", params=params or None)
    return parse(ListMessagesResponse, raw, source=SOURCE, endpoint=ENDPOINT)
