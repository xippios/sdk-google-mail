"""``gmail.users.getProfile``: ``GET gmail/v1/users/{userId}/profile``, the account's profile.

Reference: ``docs/upstream/discovery.json``, ``resources.users.methods.getProfile``: "Gets
the current user's Gmail profile."; its one parameter, the path's ``userId``, defaults to
``me``, "the authenticated user", which ``BASE_URL`` fixes (``.../users/me``). The answer is
``schemas.Profile``. One quota unit (``docs/upstream/pages/quota.md``).
"""

from xippios.core import AsyncClient, parse

from xippios.google.mail._client import SOURCE
from xippios.google.mail.models import Profile

__all__ = ["get_profile"]

ENDPOINT = "gmail.users.getProfile"
"""The operation's id in the discovery document, ``meta.endpoint`` verbatim."""


async def get_profile(client: AsyncClient) -> Profile:
    """gmail.users.getProfile: the profile of the account ``client`` signs for.

    Args:
        client: The account's client, ``_client.client_for(account)``; its bearer names
            the user, ``userId`` ``me``.
    """
    raw = await client.get_json("profile")
    return parse(Profile, raw, source=SOURCE, endpoint=ENDPOINT)
