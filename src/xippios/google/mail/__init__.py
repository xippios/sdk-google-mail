"""Public API: ``__version__`` and the task functions re-exported here (conventions §2).

Upstream: Gmail.
"""

from xippios.google.mail._version import __version__
from xippios.google.mail.tasks import get_profile, list_accounts

__all__ = ["__version__", "get_profile", "list_accounts"]
