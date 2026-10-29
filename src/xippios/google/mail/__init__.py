"""Public API: ``__version__`` and the task functions re-exported here (conventions §2).

Upstream: Gmail.
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

__all__ = [
    "__version__",
    "get_message",
    "get_profile",
    "get_thread",
    "list_accounts",
    "list_labels",
    "list_messages",
    "search_messages",
]
