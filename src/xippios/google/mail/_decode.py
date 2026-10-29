"""The decoded view of a message: its MIME tree read by role (ADR-0025, "The decoded view").

``decode_message`` turns a ``Message`` fetched with ``format=full`` into a
``DecodedMessage``. Decoding the tree is parsing a value, the one override conventions
§3 permits a projection, and the view picks the tree's parts by their MIME role, under
the role's name; the fields ``Message`` itself has are copied with
``UpstreamModel.project``. The faithful tree stays on ``Message.payload``.

The walk is depth first, in upstream's order, the top-level part first: a part's
``parts`` are its children. A part is an attachment when it carries a ``filename`` or its
body is held as an attachment (``body.attachmentId``); the walk lists it and does not
descend into it, so a forwarded message attached as ``message/rfc822`` gives no text.

- ``headers``: the top-level part's headers whose name, compared without regard to case,
  is one of ``HEADERS``, in upstream's order, name and value as upstream gives them.
- ``text``: the body of the first ``text/plain`` part that is no attachment; ``html``:
  that of the first ``text/html`` part, when asked for. A body is ``body.data``,
  base64url (``RFC 4648`` §5, the padding upstream may leave out restored), decoded to
  bytes and then to text by the ``charset`` parameter of the part's ``Content-Type``
  header when it has one and Python knows it, else as UTF-8; bytes the charset cannot
  read become U+FFFD, the replacement character. A part with no ``data`` (an empty
  body) gives ``""``; no such part gives ``None``.
- ``attachments``: each attachment's ``partId``, ``filename``, ``mimeType``,
  ``body.size`` and ``body.attachmentId``, never its content.
"""

import base64
import binascii
from collections.abc import Iterator
from email.message import Message as EmailHeaders

from xippios.core import ValidationError

from xippios.google.mail._client import SOURCE
from xippios.google.mail.models import (
    Attachment,
    DecodedMessage,
    Message,
    MessagePart,
    MessagePartHeader,
)

__all__ = ["HEADERS", "decode_body", "decode_message", "header_value"]

HEADERS = (
    "From",
    "To",
    "Cc",
    "Bcc",
    "Date",
    "Subject",
    "Message-ID",
    "In-Reply-To",
    "References",
)
"""The headers a decoded view keeps (ADR-0025), compared without regard to case."""
_KEPT = frozenset(name.lower() for name in HEADERS)
_ENDPOINT = "gmail.users.messages.get"


def header_value(headers: list[MessagePartHeader], name: str) -> str | None:
    """The value of the first header called ``name``, without regard to case; else ``None``.

    Args:
        headers: A part's headers, e.g. ``message.payload.headers``.
        name: A header name, e.g. ``"Subject"``.
    """
    wanted = name.lower()
    return next((header.value for header in headers if header.name.lower() == wanted), None)


def _charset(part: MessagePart) -> str | None:
    """The ``charset`` parameter of the part's ``Content-Type`` header, lower-cased."""
    value = header_value(part.headers, "Content-Type")
    if value is None:
        return None
    parsed = EmailHeaders()
    parsed["Content-Type"] = value
    return parsed.get_content_charset()


def decode_body(data: str, charset: str | None = None) -> str:
    """``body.data`` as text: base64url decoded, then read in ``charset``, else UTF-8.

    Bytes the charset cannot read become U+FFFD; a charset Python does not know reads as
    UTF-8 the same way.

    Args:
        data: A part's ``body.data``, base64url with or without its padding.
        charset: The part's ``charset``, e.g. ``"iso-8859-1"``; ``None`` for UTF-8.

    Raises:
        binascii.Error: ``data`` is not base64url.
    """
    raw = base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))
    try:
        return raw.decode(charset or "utf-8", errors="replace")
    except LookupError:
        return raw.decode("utf-8", errors="replace")


def _is_attachment(part: MessagePart) -> bool:
    return bool(part.filename) or (part.body is not None and part.body.attachment_id is not None)


def _walk(part: MessagePart) -> Iterator[MessagePart]:
    """``part`` and its descendants, depth first, not descending into an attachment."""
    yield part
    if _is_attachment(part):
        return
    for child in part.parts:
        yield from _walk(child)


def _text_of(parts: list[MessagePart], mime_type: str, message_id: str) -> str | None:
    """The decoded body of the first part of ``mime_type`` that is no attachment."""
    for part in parts:
        if (part.mime_type or "").lower() != mime_type or _is_attachment(part):
            continue
        data = part.body.data if part.body is not None else None
        if not data:
            return ""
        try:
            return decode_body(data, _charset(part))
        except binascii.Error:
            raise ValidationError(
                f"{SOURCE} {_ENDPOINT}: message {message_id}, part {part.part_id!r}: "
                "body.data is not base64url",
                raw=data,
                source=SOURCE,
            ) from None
    return None


def decode_message(message: Message, *, include_html: bool = False) -> DecodedMessage:
    """The decoded view of ``message``, one fetched with ``format=full`` (module docstring).

    Args:
        message: The message, its ``payload`` the whole MIME tree.
        include_html: Decode the first ``text/html`` part into ``html`` as well; ``False``
            leaves ``html`` ``None``.

    Raises:
        ValidationError: The ``text/plain`` or ``text/html`` part chosen holds a
            ``body.data`` that is not base64url.
    """
    payload = message.payload
    parts = list(_walk(payload)) if payload is not None else []
    headers = [h for h in payload.headers if h.name.lower() in _KEPT] if payload else []
    attachments = [
        Attachment(
            part_id=part.part_id,
            filename=part.filename,
            mime_type=part.mime_type,
            size=part.body.size if part.body is not None else None,
            attachment_id=part.body.attachment_id if part.body is not None else None,
        )
        for part in parts
        if _is_attachment(part)
    ]
    return DecodedMessage.project(
        message,
        headers=headers,
        text=_text_of(parts, "text/plain", message.id),
        html=_text_of(parts, "text/html", message.id) if include_html else None,
        attachments=attachments,
    )
