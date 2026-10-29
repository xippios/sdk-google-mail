"""Pydantic models mirroring Gmail's payloads, one ``UpstreamModel`` per object.

Conventions §3 and ``/add-endpoint`` give the rules. Fields are named as upstream names
them in snake_case; where upstream spells a field differently (``emailAddress``),
``Field(validation_alias=...)`` accepts that spelling on input, never ``alias=``, which the
adapters would serialise, so results serialise under the snake_case names in Python, over
MCP and over HTTP alike. Types follow the schemas of ``docs/upstream/discovery.json``: an
``integer`` is an ``int``; a ``uint64`` id that upstream sends as a JSON string,
``historyId``, stays the string it is; ``internalDate``, a timestamp upstream sends as
a string of epoch milliseconds, is parsed to an aware UTC ``datetime``
(``EpochMillis``), since conventions §3 types dates and times. A documented value set is
a ``StrEnum`` written here (``LabelType``), since no packaged list renders it into
``enums.py``. A field the schema has and an answer may leave out is ``None`` by default,
a list ``[]``.

Two kinds of model live here. The read models of the endpoint wrappers are faithful
(ADR-0003): ``Label``, ``ListLabelsResponse``, ``MessageRef``, ``ListMessagesResponse``,
``MessagePartHeader``, ``MessagePartBody``, ``MessagePart``, ``Message`` and ``Thread``,
the MIME tree of a message whole under ``Message.payload``. The models the tasks return
beside them are projections: ``DecodedMessage`` with its ``Attachment`` items, the
decoded view of ADR-0025 that ``_decode.decode_message`` builds from a ``Message`` by the
parts' MIME roles (conventions §3), and ``SearchRow``, a message's three metadata headers
beside its ids and snippet.
"""

import re
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Annotated

from pydantic import AwareDatetime, BeforeValidator, Field
from xippios.core import UpstreamModel

__all__ = [
    "Attachment",
    "DecodedMessage",
    "EpochMillis",
    "Label",
    "LabelColor",
    "LabelListVisibility",
    "LabelType",
    "ListLabelsResponse",
    "ListMessagesResponse",
    "Message",
    "MessageListVisibility",
    "MessagePart",
    "MessagePartBody",
    "MessagePartHeader",
    "MessageRef",
    "Profile",
    "SearchRow",
    "Thread",
]


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


_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


_INTEGER = re.compile(r"-?[0-9]+")
"""An integer as text: ASCII digits, an optional leading minus and nothing else."""


def _from_epoch_millis(value: object) -> datetime:
    """``"1793267674000"`` → ``2026-10-29 09:54:34+00:00``; an aware ``datetime`` as it is.

    The milliseconds are added to the epoch as an integer, so none is lost to a float.
    Every other shape is refused with a ``ValueError`` that names the shape, never the
    value, which pydantic and core's ``parse`` turn into a ``ValidationError``: text that
    is not an integer (``"+123"``, ``"1793267674.5"``, ISO text), a ``float``, a ``bool``,
    a ``datetime`` without a zone, and a count of milliseconds outside the years 1 to
    9999 (Python's ``OverflowError``, which would otherwise escape pydantic bare).
    """
    if isinstance(value, datetime):
        if value.utcoffset() is None:
            raise ValueError("a datetime without a zone; an aware datetime is taken as it is")
        return value
    if isinstance(value, str) and _INTEGER.fullmatch(value):
        millis = int(value)
    elif isinstance(value, int) and not isinstance(value, bool):
        millis = value
    else:
        shape = "text that is not an integer" if isinstance(value, str) else type(value).__name__
        raise ValueError(f"epoch milliseconds are an integer, as text or an int, not {shape}")
    try:
        return _EPOCH + timedelta(milliseconds=millis)
    except OverflowError:
        raise ValueError("epoch milliseconds outside the years 1 to 9999") from None


EpochMillis = Annotated[AwareDatetime, BeforeValidator(_from_epoch_millis)]
"""A time upstream sends as a string of milliseconds since the epoch, as an aware UTC
``datetime`` (``internalDate``, format ``int64``). It takes an integer as text (an
optional leading minus, then ASCII digits) or as an ``int``, and an aware ``datetime``,
which it keeps as it is; anything else, naive datetimes and other numeric text included,
is refused as a ``ValidationError``."""


class LabelType(StrEnum):
    """``Label.type``: "The owner type for the label." (``schemas.Label``)."""

    SYSTEM = "system"
    """Labels created by Gmail."""
    USER = "user"
    """Custom labels created by the user or application."""


class LabelListVisibility(StrEnum):
    """``Label.labelListVisibility``: the label's visibility in the web interface's label list."""

    LABEL_SHOW = "labelShow"
    """Show the label in the label list."""
    LABEL_SHOW_IF_UNREAD = "labelShowIfUnread"
    """Show the label if there are any unread messages with that label."""
    LABEL_HIDE = "labelHide"
    """Do not show the label in the label list."""


class MessageListVisibility(StrEnum):
    """``Label.messageListVisibility``: the visibility of messages with this label."""

    SHOW = "show"
    """Show the label in the message list."""
    HIDE = "hide"
    """Do not show the label in the message list."""


class LabelColor(UpstreamModel):
    """``schemas.LabelColor``: a user label's colours, each a hex string ``#RRGGBB``.

    Attributes:
        text_color: ``textColor``, "The text color of the label, represented as hex string."
        background_color: ``backgroundColor``, "The background color represented as hex
            string #RRGGBB (ex #000000)."
    """

    text_color: str | None = Field(default=None, validation_alias="textColor")
    background_color: str | None = Field(default=None, validation_alias="backgroundColor")


class Label(UpstreamModel):
    """``schemas.Label``, a label of the mailbox, the answer's items of ``users.labels.list``.

    "Labels are used to categorize messages and threads within the user's mailbox."
    ``labels.list`` sends "only ... an ``id``, ``name``, ``messageListVisibility``,
    ``labelListVisibility``, and ``type``" of each label (``ListLabelsResponse``); the
    counts and the colour are ``labels.get``'s and stay ``None`` here.

    Attributes:
        id: "The immutable ID of the label.", e.g. ``INBOX`` or ``Label_1``; what
            ``labelIds`` takes.
        name: "The display name of the label."
        type: "The owner type for the label.": ``system`` or ``user``.
        message_list_visibility: ``messageListVisibility``: ``show`` or ``hide``.
        label_list_visibility: ``labelListVisibility``: ``labelShow``,
            ``labelShowIfUnread`` or ``labelHide``.
        messages_total: ``messagesTotal``, "The total number of messages with the label."
        messages_unread: ``messagesUnread``, "The number of unread messages with the label."
        threads_total: ``threadsTotal``, "The total number of threads with the label."
        threads_unread: ``threadsUnread``, "The number of unread threads with the label."
        color: "The color to assign to the label.", user labels only.
    """

    id: str
    name: str
    type: LabelType | None = None
    message_list_visibility: MessageListVisibility | None = Field(
        default=None, validation_alias="messageListVisibility"
    )
    label_list_visibility: LabelListVisibility | None = Field(
        default=None, validation_alias="labelListVisibility"
    )
    messages_total: int | None = Field(default=None, validation_alias="messagesTotal")
    messages_unread: int | None = Field(default=None, validation_alias="messagesUnread")
    threads_total: int | None = Field(default=None, validation_alias="threadsTotal")
    threads_unread: int | None = Field(default=None, validation_alias="threadsUnread")
    color: LabelColor | None = None


class ListLabelsResponse(UpstreamModel):
    """``schemas.ListLabelsResponse``, the answer of ``users.labels.list``.

    Attributes:
        labels: "List of labels."
    """

    labels: list[Label] = Field(default_factory=list)


class MessageRef(UpstreamModel):
    """A message of ``ListMessagesResponse.messages``: "only an ``id`` and a ``threadId``".

    The list's items are ``schemas.Message`` resources with those two fields sent;
    ``messages.get`` fetches the rest (``Message``).

    Attributes:
        id: "The immutable ID of the message."
        thread_id: ``threadId``, "The ID of the thread the message belongs to."
    """

    id: str
    thread_id: str = Field(validation_alias="threadId")


class ListMessagesResponse(UpstreamModel):
    """``schemas.ListMessagesResponse``, the answer of ``users.messages.list``.

    Attributes:
        messages: "List of messages.", ids only; upstream leaves the field out when
            nothing matches, which reads as ``[]``.
        next_page_token: ``nextPageToken``, "Token to retrieve the next page of results in
            the list."; ``None`` on the last page.
        result_size_estimate: ``resultSizeEstimate``, "Estimated total number of results.",
            upstream's estimate, not a count.
    """

    messages: list[MessageRef] = Field(default_factory=list)
    next_page_token: str | None = Field(default=None, validation_alias="nextPageToken")
    result_size_estimate: int | None = Field(default=None, validation_alias="resultSizeEstimate")


class MessagePartHeader(UpstreamModel):
    """``schemas.MessagePartHeader``, one header of a MIME part, as upstream gives it.

    Attributes:
        name: "The name of the header before the ``:`` separator. For example, ``To``."
        value: "The value of the header after the ``:`` separator."
    """

    name: str
    value: str


class MessagePartBody(UpstreamModel):
    """``schemas.MessagePartBody``, the body of a single MIME message part.

    Also the answer of ``users.messages.attachments.get``.

    Attributes:
        attachment_id: ``attachmentId``, "the ID of an external attachment that can be
            retrieved in a separate ``messages.attachments.get`` request"; ``None`` when
            the body is in ``data``.
        data: "The body data of a MIME message part as a base64url encoded string", kept
            encoded as sent; ``None`` for a container or a body held as an attachment.
        size: "Number of bytes for the message part data (encoding notwithstanding)."
    """

    attachment_id: str | None = Field(default=None, validation_alias="attachmentId")
    data: str | None = None
    size: int | None = None


class MessagePart(UpstreamModel):
    """``schemas.MessagePart``: "A single MIME message part.", its children in ``parts``.

    Attributes:
        part_id: ``partId``, "The immutable ID of the message part."; ``""`` for the
            top-level part, ``0``, ``0.1`` and so on below it.
        mime_type: ``mimeType``, "The MIME type of the message part.", e.g. ``text/plain``.
        filename: "The filename of the attachment. Only present if this message part
            represents an attachment."; upstream sends ``""`` for every other part.
        headers: "List of headers on this message part.", the RFC 2822 headers of the
            message on the top-level part.
        body: "The message part body for this part, which may be empty for container MIME
            message parts."
        parts: "The child MIME message parts of this part.", ``[]`` for a part that is no
            container (``multipart/*``).
    """

    part_id: str | None = Field(default=None, validation_alias="partId")
    mime_type: str | None = Field(default=None, validation_alias="mimeType")
    filename: str | None = None
    headers: list[MessagePartHeader] = Field(default_factory=list)
    body: MessagePartBody | None = None
    parts: list["MessagePart"] = Field(default_factory=list)


class Message(UpstreamModel):
    """``schemas.Message``: "An email message.", the answer of ``users.messages.get``.

    What upstream fills depends on ``format``: ``minimal`` the ids and labels,
    ``metadata`` the headers too, ``full`` the whole MIME tree with each part's body in
    ``payload``. ``raw``, the base64url text of the whole message, is not modelled: no
    wrapper of this package asks for ``format=raw``. ``classificationLabelValues``, used
    for Google Workspace accounts only, is not modelled either.

    Attributes:
        id: "The immutable ID of the message."
        thread_id: ``threadId``, "The ID of the thread the message belongs to."
        label_ids: ``labelIds``, "List of IDs of labels applied to this message."
        snippet: "A short part of the message text."
        history_id: ``historyId``, "The ID of the last history record that modified this
            message.", a ``uint64`` sent as a string, kept as sent.
        internal_date: ``internalDate``, "The internal message creation timestamp (epoch
            ms), which determines ordering in the inbox.", an ``int64`` sent as a string
            of epoch milliseconds, parsed to an aware UTC ``datetime``.
        size_estimate: ``sizeEstimate``, "Estimated size in bytes of the message."
        payload: "The parsed email structure in the message parts."
    """

    id: str
    thread_id: str | None = Field(default=None, validation_alias="threadId")
    label_ids: list[str] = Field(default_factory=list, validation_alias="labelIds")
    snippet: str | None = None
    history_id: str | None = Field(default=None, validation_alias="historyId")
    internal_date: EpochMillis | None = Field(default=None, validation_alias="internalDate")
    size_estimate: int | None = Field(default=None, validation_alias="sizeEstimate")
    payload: MessagePart | None = None


class Thread(UpstreamModel):
    """``schemas.Thread``, a collection of messages representing a conversation.

    The answer of ``users.threads.get``, its messages in the format the call asked for.

    Attributes:
        id: "The unique ID of the thread."
        history_id: ``historyId``, "The ID of the last history record that modified this
            thread.", kept as the string sent.
        snippet: "A short part of the message text."
        messages: "The list of messages in the thread."
    """

    id: str
    history_id: str | None = Field(default=None, validation_alias="historyId")
    snippet: str | None = None
    messages: list[Message] = Field(default_factory=list)


class Attachment(UpstreamModel):
    """An attachment of a ``DecodedMessage``, its metadata only and never its content.

    A part of the MIME tree that carries a ``filename`` or whose body is held as an
    attachment (``body.attachmentId``); ``messages.attachments.get`` would fetch its
    content, which 0.1.0 serves over no tool.

    Attributes:
        part_id: The part's ``partId``.
        filename: The part's ``filename``, as upstream gives it.
        mime_type: The part's ``mimeType``.
        size: The part's ``body.size``, in bytes.
        attachment_id: The part's ``body.attachmentId``.
    """

    part_id: str | None
    filename: str | None
    mime_type: str | None
    size: int | None
    attachment_id: str | None


class DecodedMessage(UpstreamModel):
    """A message's decoded view: its headers, text and attachments, by MIME role (ADR-0025).

    The fields ``Message`` has are copied from it; ``headers``, ``text``, ``html`` and
    ``attachments`` come from its ``payload``, the MIME tree, decoded by
    ``_decode.decode_message``: decoding the tree is parsing a value, the one override
    conventions §3 permits, and each field takes the part its MIME role names.

    Attributes:
        id: The message's ``id``.
        thread_id: Its ``threadId``.
        label_ids: Its ``labelIds``.
        internal_date: Its ``internalDate``, an aware UTC ``datetime``, e.g.
            ``2026-10-29T09:54:34Z`` over MCP and HTTP.
        size_estimate: Its ``sizeEstimate``.
        snippet: Its ``snippet``.
        headers: The top-level part's headers named From, To, Cc, Bcc, Date, Subject,
            Message-ID, In-Reply-To and References, in upstream's order, each name and
            value as upstream gives them; every other header is left out.
        text: The first ``text/plain`` part's body, decoded; ``None`` when there is none.
        html: The first ``text/html`` part's body, decoded, when asked for
            (``include_html``); ``None`` otherwise or when there is none.
        attachments: The attachments' metadata, in the tree's order.
    """

    id: str
    thread_id: str | None
    label_ids: list[str]
    internal_date: EpochMillis | None
    size_estimate: int | None
    snippet: str | None
    headers: list[MessagePartHeader]
    text: str | None
    html: str | None
    attachments: list[Attachment]


class SearchRow(UpstreamModel):
    """A message found by ``search_messages``: its ids, three headers and its snippet.

    ``id``, ``thread_id`` and ``snippet`` are copied from the ``Message`` a
    ``format=metadata`` call answers; ``from_``, ``subject`` and ``date`` are the values
    of its first ``From``, ``Subject`` and ``Date`` header, as upstream gives them
    (``from_`` with Python's trailing underscore, since ``from`` is a keyword).

    Attributes:
        id: The message's ``id``.
        thread_id: Its ``threadId``.
        from_: Its ``From`` header's value, e.g. ``Alice <alice@example.com>``.
        subject: Its ``Subject`` header's value.
        date: Its ``Date`` header's value, the text the sender wrote, e.g.
            ``Thu, 29 Oct 2026 14:02:11 +0100``.
        snippet: Its ``snippet``.
    """

    id: str
    thread_id: str | None
    from_: str | None
    subject: str | None
    date: str | None
    snippet: str | None
