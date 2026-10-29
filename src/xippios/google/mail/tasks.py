"""Task functions, the public API: the adapters serve ``__all__`` (conventions §2).

``/add-endpoint`` adds them.

``account: str`` is the first, required parameter of every task: the name of a declared
account (``demo``, ``work``), whose client (``_client.client_for``) the task hands to the
endpoint wrappers. No task spans accounts. The one exception is ``list_accounts``, the
package's tenant list (``_tenants.list_task``, queue row 281): no argument, the declared
accounts' names only, so an agent can form valid calls.

``meta.endpoint`` is the operation's id in the discovery document
(``gmail.users.getProfile``), ``meta.tenant`` the account the data came from, by name
(ADR-0014, decision 5), and ``meta.request`` holds ``account`` plus the parameters sent,
under upstream's names. Gmail publishes no release time or identifier for a mailbox, so
``published_at`` and ``vintage`` stay empty.
"""

from collections.abc import Mapping
from typing import Any

from xippios.core import Result, ResultMeta, sync

from xippios.google.mail import api
from xippios.google.mail._client import SOURCE, TENANTS, client_for
from xippios.google.mail._tenants import list_task
from xippios.google.mail.api.get_profile import ENDPOINT as GET_PROFILE
from xippios.google.mail.models import Profile

__all__ = ["get_profile", "list_accounts"]

# A task named here is Python-only: no tool, no route; the adapters skip it (ADR-0019 decision 4).
BULK_TASKS: frozenset[str] = frozenset()

# MCP_CAPS, only where a tool needs a result cap of its own, keyed by the task function's
# name (conventions §8, ADR-0016):
#
# from xippios.core import ResultCap
#
# MCP_CAPS = {"list_<thing>": ResultCap(max_rows=400)}


def _meta(account: str, endpoint: str, params: Mapping[str, Any]) -> ResultMeta:
    """``meta`` of a task's result: ``account`` as the tenant and first in ``request``."""
    return ResultMeta(
        source=SOURCE,
        endpoint=endpoint,
        request={"account": account, **params},
        tenant=account,
    )


LIST_ACCOUNTS_DOC = """List the Gmail accounts this server can reach, by name.

On a server that serves every declared account, every other tool takes one of these names
as ``account`` to choose the mailbox it reads, e.g. ``demo`` or ``work``; on a server
started with ``--account`` the tools act on that one account and this list shows it
alone. Only names come back, never an address, a token or another value; an empty list
means that no account is declared where the server runs.
"""

list_accounts = list_task(TENANTS, name="list_accounts", doc=LIST_ACCOUNTS_DOC, source=SOURCE)
"""The declared accounts' names (queue row 281); ``meta.tenant`` is ``None``."""


async def get_profile_async(account: str) -> Result[Profile]:
    """Get an account's Gmail profile: its address, message and thread counts, history id.

    ``email_address`` is the mailbox's address, ``messages_total`` and ``threads_total``
    count every message and thread in it, and ``history_id`` is the id of the mailbox's
    current history record. One call, one quota unit.

    Args:
        account: A declared account's name, e.g. ``demo`` or ``work``;
            ``google_mail_list_accounts`` lists them.
    """
    profile = await api.get_profile(client_for(account))
    return Result(data=profile, meta=_meta(account, GET_PROFILE, {}))


get_profile = sync(get_profile_async)
