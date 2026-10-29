"""Pydantic models mirroring Gmail's payloads, one ``UpstreamModel`` per object.

Conventions §3 and ``/add-endpoint`` give the rules. Fields are named as upstream names
them in snake_case; where upstream spells a field differently (``emailAddress``),
``Field(validation_alias=...)`` accepts that spelling on input, never ``alias=``, which the
adapters would serialise, so results serialise under the snake_case names in Python, over
MCP and over HTTP alike. Types follow the schemas of ``docs/upstream/discovery.json``: an
``integer`` is an ``int``; a ``uint64`` that upstream sends as a JSON string, an id such
as ``historyId``, stays the string it is.
"""

from pydantic import Field
from xippios.core import UpstreamModel

__all__ = ["Profile"]


class Profile(UpstreamModel):
    """``schemas.Profile``: "Profile for a Gmail user." (``users.getProfile``).

    Attributes:
        email_address: ``emailAddress``, "The user's email address."
        messages_total: ``messagesTotal``, "The total number of messages in the mailbox."
        threads_total: ``threadsTotal``, "The total number of threads in the mailbox."
        history_id: ``historyId``, "The ID of the mailbox's current history record.", a
            ``uint64`` upstream sends as a string, kept as sent.
    """

    email_address: str = Field(validation_alias="emailAddress")
    messages_total: int = Field(validation_alias="messagesTotal")
    threads_total: int = Field(validation_alias="threadsTotal")
    history_id: str = Field(validation_alias="historyId")
