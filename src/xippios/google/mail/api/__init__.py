"""Endpoint wrappers, layer 1: one async function per upstream operation (conventions §2).

``/add-endpoint`` adds them. Each takes the account's client (``_client.client_for``) as
its first argument: Gmail answers for the user the bearer names, ``userId`` ``me``. A
module and its function carry the operation's id in the discovery document minus the
prefix every method shares, ``gmail.users.``, in snake case (conventions §2):
``gmail.users.messages.attachments.get`` is ``messages_attachments_get``.
"""

from xippios.google.mail.api.get_profile import get_profile
from xippios.google.mail.api.labels_list import labels_list
from xippios.google.mail.api.messages_attachments_get import messages_attachments_get
from xippios.google.mail.api.messages_get import messages_get
from xippios.google.mail.api.messages_list import messages_list
from xippios.google.mail.api.threads_get import threads_get

__all__ = [
    "get_profile",
    "labels_list",
    "messages_attachments_get",
    "messages_get",
    "messages_list",
    "threads_get",
]
