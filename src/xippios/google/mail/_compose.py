"""The RFC 2822 text of a message the write tasks send, built with the standard library.

``messages.send`` and ``drafts.create`` take a message as ``raw``, "The entire email
message in an RFC 2822 formatted and base64url encoded string" (``schemas.Message`` in
``docs/upstream/discovery.json``). ``compose`` builds it with
``email.message.EmailMessage`` under ``email.policy.SMTP`` held to seven bits
(``cte_type="7bit"``), so the text is the US-ASCII RFC 2822 describes: lines end in CRLF,
a header with characters outside ASCII goes out RFC 2047 encoded, and the body, one
``text/plain`` part in UTF-8, goes out as it is when it is ASCII and quoted-printable or
base64 otherwise, as the library chooses. ``encode_raw`` encodes the bytes base64url
(RFC 4648 §5) without padding.

The message carries To, Cc, Bcc, Subject and, for a reply, In-Reply-To and References;
``From`` is left out, and so are ``Date`` and ``Message-ID``: Gmail sets them for the
account whose bearer sends (an assumption of 0.1.0, which the recorded read-back shows).
``Bcc`` stays in the text: ``messages.send`` "Sends the specified message to the
recipients in the ``To``, ``Cc``, and ``Bcc`` headers".

Every value is checked before anything is sent (``addresses``, ``check_subject``): an
address list is a list, each item one address with a domain, display name allowed
(``Alice <alice@example.com>``), and no header value holds a line break, which would
start a header of its own.

A reply (``Reply``, from the original's ``format=metadata`` read) follows the discovery
document's rule for adding a message or draft to a thread (``schemas.Message.threadId``):
"The requested ``threadId`` must be specified ... The ``References`` and ``In-Reply-To``
headers must be set in compliance with the RFC 2822 standard ... The ``Subject`` headers
must match." So ``threadId`` is the original's, In-Reply-To its Message-ID, References
its References followed by its Message-ID (RFC 2822 §3.6.4), and the Subject, when the
caller gives none, ``Re: `` and the original's, unless that starts with ``Re:`` already.
"""

import base64
import email.policy
from collections.abc import Sequence
from dataclasses import dataclass
from email.headerregistry import Address, HeaderRegistry
from email.message import EmailMessage

from xippios.core import InvalidRequestError

from xippios.google.mail._client import SOURCE
from xippios.google.mail._decode import header_value
from xippios.google.mail.models import Message, MessagePartHeader

__all__ = [
    "POLICY",
    "PREVIEW_HEADERS",
    "PREVIEW_LINES",
    "REPLY_HEADERS",
    "Reply",
    "addresses",
    "check_subject",
    "compose",
    "encode_raw",
    "preview_headers",
]

POLICY = email.policy.SMTP.clone(cte_type="7bit")
"""CRLF line ends, 78-character lines, and nothing but US-ASCII in the text."""
PREVIEW_HEADERS = ("To", "Cc", "Bcc", "Subject", "In-Reply-To", "References")
"""The headers a preview shows, each only when set, in this order."""
PREVIEW_LINES = 10
"""How many of the body's lines a preview shows."""
REPLY_HEADERS = ["Message-ID", "References", "Subject"]
"""The ``metadataHeaders`` a reply reads from the original (``messages.get``, 20 units)."""
_REGISTRY = HeaderRegistry()


def _refuse(message: str) -> InvalidRequestError:
    return InvalidRequestError(message, source=SOURCE)


def _one_address(name: str, value: object) -> Address:
    """``value`` as one address, or the refusal naming ``name``; never the value itself."""
    if not isinstance(value, str) or not value.strip():
        raise _refuse(f"{name}: each item is an address, e.g. 'alice@example.com'")
    if "\r" in value or "\n" in value:
        raise _refuse(f"{name}: an address holds no line break")
    header = _REGISTRY("To", value)
    # An AddressHeader, which typeshed cannot type as the registry's dynamic class.
    found: tuple[Address, ...] = getattr(header, "addresses", ())
    if header.defects or len(found) != 1 or not found[0].username or not found[0].domain:
        raise _refuse(
            f"{name}: each item is one address with a domain, e.g. 'alice@example.com' or "
            "'Alice <alice@example.com>'; several addresses are several items"
        )
    return found[0]


def addresses(name: str, values: object, *, required: bool) -> list[Address]:
    """The addresses of ``to``, ``cc`` or ``bcc``, checked before any request.

    Args:
        name: The parameter, e.g. ``"to"``, which a refusal names.
        values: A list of addresses, e.g. ``["alice@example.com", "Bob <bob@example.com>"]``,
            or ``None``.
        required: At least one address must be given (``to``).

    Raises:
        InvalidRequestError: ``values`` is not a list, holds an item that is not one
            address with a domain or holds a line break, or is empty while ``required``.
    """
    if values is None:
        values = []
    if isinstance(values, str | bytes) or not isinstance(values, Sequence):
        raise _refuse(
            f"{name} takes a list of addresses, e.g. ['alice@example.com'], "
            f"not {type(values).__name__}"
        )
    if required and not values:
        raise _refuse(f"{name} takes one address at least, e.g. ['alice@example.com']")
    return [_one_address(name, value) for value in values]


def check_subject(subject: object) -> str:
    """``subject`` when it is text without a line break.

    Raises:
        InvalidRequestError: ``subject`` is not text or holds a line break.
    """
    if not isinstance(subject, str):
        raise _refuse(f"subject is text, e.g. 'Lunch on Friday', not {type(subject).__name__}")
    if "\r" in subject or "\n" in subject:
        raise _refuse("subject holds no line break")
    return subject


@dataclass(frozen=True, slots=True)
class Reply:
    """What a reply takes from the message it answers, read with ``format=metadata``.

    Attributes:
        thread_id: The original's ``threadId``, which the reply sends.
        message_id: Its ``Message-ID`` header, the reply's ``In-Reply-To``; ``None`` when
            it has none, and then the reply sets neither In-Reply-To nor References.
        references: Its ``References`` header, if any.
        subject: Its ``Subject`` header, if any.
    """

    thread_id: str | None
    message_id: str | None
    references: str | None
    subject: str | None

    @classmethod
    def of(cls, original: Message) -> "Reply":
        """The reply's part of ``original``, a ``messages.get`` answer with its headers."""
        headers = original.payload.headers if original.payload is not None else []
        return cls(
            thread_id=original.thread_id,
            message_id=header_value(headers, "Message-ID"),
            references=header_value(headers, "References"),
            subject=header_value(headers, "Subject"),
        )

    def references_header(self) -> str | None:
        """The reply's References: the original's References, then its Message-ID."""
        if self.message_id is None:
            return None
        return " ".join(part for part in (self.references, self.message_id) if part)

    def subject_for(self, given: str) -> str:
        """``given``, or, when it is empty, ``Re: `` and the original's subject."""
        if given:
            return given
        original = (self.subject or "").strip()
        if original.lower().startswith("re:"):
            return original
        return f"Re: {original}" if original else "Re:"


def compose(
    *,
    to: Sequence[Address],
    subject: str,
    body: str,
    cc: Sequence[Address] = (),
    bcc: Sequence[Address] = (),
    reply: Reply | None = None,
) -> EmailMessage:
    """The message: its headers and one ``text/plain`` part in UTF-8, no ``From``.

    Args:
        to: The To addresses, from ``addresses``.
        subject: The Subject; for a reply an empty one becomes ``Re: <original>``.
        body: The plain text, e.g. ``"Thanks, see you on Friday."``.
        cc: The Cc addresses.
        bcc: The Bcc addresses.
        reply: The original's part, for a reply.
    """
    message = EmailMessage(policy=POLICY)
    message["To"] = ", ".join(str(address) for address in to)
    if cc:
        message["Cc"] = ", ".join(str(address) for address in cc)
    if bcc:
        message["Bcc"] = ", ".join(str(address) for address in bcc)
    subject = subject if reply is None else reply.subject_for(subject)
    if subject:
        message["Subject"] = subject
    if reply is not None and reply.message_id is not None:
        message["In-Reply-To"] = reply.message_id
        message["References"] = reply.references_header() or reply.message_id
    message.set_content(body, charset="utf-8")
    return message


def encode_raw(message: EmailMessage) -> str:
    """``raw``: the message's bytes, base64url encoded (RFC 4648 §5), without padding."""
    return base64.urlsafe_b64encode(message.as_bytes()).decode("ascii").rstrip("=")


def preview_headers(message: EmailMessage) -> list[MessagePartHeader]:
    """The headers of ``PREVIEW_HEADERS`` the message carries, decoded, in that order."""
    return [
        MessagePartHeader(name=name, value=str(message[name]))
        for name in PREVIEW_HEADERS
        if message[name] is not None
    ]
