"""Endpoint wrappers, layer 1: one async function per upstream operation (conventions §2).

``/add-endpoint`` adds them. Each takes the account's client (``_client.client_for``) as
its first argument: Gmail answers for the user the bearer names, ``userId`` ``me``.
"""

from xippios.google.mail.api.get_profile import get_profile

__all__ = ["get_profile"]
