"""Public API: ``__version__`` and the task functions re-exported here (conventions §2).

Upstream: Gmail. Every task takes ``account``, a declared account, first, except
``list_accounts``, which takes no argument and lists the declared accounts' names. The
write tasks (``create_draft``, ``delete_draft``, ``send_message``, ``modify_message`` and
``trash_message``, from ``tasks_write``) preview unless called with ``confirm=True``.
"""

from xippios.google.mail._version import __version__
from xippios.google.mail.tasks import (
    get_message,
    get_profile,
    get_thread,
    list_accounts,
    list_labels,
    list_messages,
    search_messages,
)
from xippios.google.mail.tasks_write import (
    create_draft,
    delete_draft,
    modify_message,
    send_message,
    trash_message,
)

__all__ = [
    "__version__",
    "create_draft",
    "delete_draft",
    "get_message",
    "get_profile",
    "get_thread",
    "list_accounts",
    "list_labels",
    "list_messages",
    "modify_message",
    "search_messages",
    "send_message",
    "trash_message",
]
