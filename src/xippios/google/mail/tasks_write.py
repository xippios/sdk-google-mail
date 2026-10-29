"""Write tasks for Gmail: drafts created and deleted, messages sent, relabelled and trashed.

Served only by ``xippios-google-mail-mcp-write``, never by the read server and never on
the HTTP router (conventions §8; ADR-0026: read and write never mix). ``__all__`` lists
the sync names; ``mcp_write.py`` registers exactly them.

``account: str`` is the first, required parameter of every write task, and ``confirm:
bool = False`` its last (ADR-0014, decision 9). The gate is ``confirm is True``: from
Python, anything but ``True`` (``"true"``, ``1``) previews; over MCP the tool schema turns
``"true"``, ``"yes"``, ``"1"`` and ``1`` into ``True`` and ``"false"``, ``"no"``, ``"0"``
and ``0`` into ``False``, and refuses anything else.

- **Preview.** Without ``confirm`` a task sends nothing to the write endpoint. Its
  ``Result`` holds a preview: for ``create_draft`` and ``send_message`` a
  ``ComposePreview``, the message's To, Cc, Bcc, Subject, In-Reply-To and References as
  the standard library renders them (``_compose``), the body's first ``PREVIEW_LINES``
  (10) lines and its line count, and the ``threadId``; for ``delete_draft``,
  ``modify_message`` and ``trash_message`` an ``ActionPreview``, the id and, for a modify,
  the labels. Each says in ``action`` what the confirmed call does. A preview reads
  nothing, but that of a reply: the headers a reply sends are the original's, so its
  preview reads the original once (``messages.get``, ``format=metadata`` with
  Message-ID, References and Subject, 20 quota units), and the confirmed call reads it
  again.
- **Confirmed.** With ``confirm=True`` a task sends the write and returns upstream's
  answer: the ``Draft`` stored, the ``Message`` sent, modified or trashed, as Gmail
  answers them (ids and labels), and for a draft's deletion, which upstream answers
  without a body, ``DraftDeleted``.
- **Before any request** every argument is checked (``_compose.addresses``,
  ``check_subject``, ``_check_id``, ``_labels``) and the account looked up: an undeclared
  account, a recipient that is not one address, a header value with a line break or an
  empty modify is an ``InvalidRequestError`` and nothing is sent, not even the token
  request.

``meta.endpoint`` is the write operation's id in the discovery document
(``gmail.users.messages.send``), for the preview as for the confirmed call, and
``meta.tenant`` the account. ``meta.request`` is ``account`` and the parameters: ``to``,
``cc``, ``bcc``, ``subject`` and ``in_reply_to_message_id`` under the task's names and
the ``threadId`` sent, for a modify ``id``, ``addLabelIds`` and ``removeLabelIds`` under
upstream's; never the message's ``body``, which goes to upstream inside ``raw`` and
nowhere else (ADR-0017; conventions §4). ``published_at`` and ``vintage`` stay empty.

Quota units (``docs/upstream/pages/quota.md``): ``create_draft`` 10, ``send_message``
100, ``delete_draft`` 10, ``modify_message`` 5, ``trash_message`` 20; a reply 20 more for
the original's read, and its preview 20.
"""

from collections.abc import Sequence
from email.message import EmailMessage
from typing import Any

from xippios.core import InvalidRequestError, Result, sync

from xippios.google.mail import api
from xippios.google.mail._client import SOURCE, client_for
from xippios.google.mail._compose import (
    PREVIEW_LINES,
    REPLY_HEADERS,
    Reply,
    addresses,
    check_subject,
    compose,
    encode_raw,
    preview_headers,
)
from xippios.google.mail.api.drafts_create import ENDPOINT as DRAFTS_CREATE
from xippios.google.mail.api.drafts_delete import ENDPOINT as DRAFTS_DELETE
from xippios.google.mail.api.messages_modify import ENDPOINT as MESSAGES_MODIFY
from xippios.google.mail.api.messages_send import ENDPOINT as MESSAGES_SEND
from xippios.google.mail.api.messages_trash import ENDPOINT as MESSAGES_TRASH
from xippios.google.mail.models import (
    ActionPreview,
    ComposePreview,
    Draft,
    DraftDeleted,
    Message,
)
from xippios.google.mail.tasks import _meta

__all__ = [
    "create_draft",
    "delete_draft",
    "modify_message",
    "send_message",
    "trash_message",
]

MAX_LABELS = 100
"""``ModifyMessageRequest``: "You can add up to 100 labels with each update", and remove."""

DraftOutcome = ComposePreview | Draft
"""``create_draft``'s data: the preview (``performed`` false) or the draft stored."""
SendOutcome = ComposePreview | Message
"""``send_message``'s data: the preview (``performed`` false) or the message sent."""
DeleteOutcome = ActionPreview | DraftDeleted
"""``delete_draft``'s data: the preview (``performed`` false) or the acknowledgement."""
ChangeOutcome = ActionPreview | Message
"""``modify_message``'s and ``trash_message``'s data: the preview or the message changed."""


def _refuse(message: str) -> InvalidRequestError:
    return InvalidRequestError(message, source=SOURCE)


def _check_id(name: str, value: object) -> str:
    """``value`` when it is a non-empty id; ``name`` names it in the refusal."""
    if not isinstance(value, str) or not value.strip():
        raise _refuse(f"{name} is an id, e.g. '19a0b0c0d0e0f001', not {value!r}")
    return value


def _labels(name: str, values: object) -> list[str]:
    """``values`` as a list of label ids, at most ``MAX_LABELS``; ``None`` is ``[]``."""
    if values is None:
        return []
    if isinstance(values, str | bytes) or not isinstance(values, Sequence):
        raise _refuse(f"{name} takes a list of label ids, e.g. ['STARRED'], not a single value")
    if any(not isinstance(value, str) or not value.strip() for value in values):
        raise _refuse(f"{name}: each item is a label id, e.g. 'STARRED' or 'Label_1'")
    if len(values) > MAX_LABELS:
        raise _refuse(f"{name} holds {len(values)} labels; upstream takes {MAX_LABELS} at most")
    return list(values)


def _body(body: object) -> str:
    if not isinstance(body, str):
        raise _refuse(f"body is the message's plain text, not {type(body).__name__}")
    return body


async def _compose(
    account: str,
    to: object,
    subject: object,
    body: object,
    cc: object,
    bcc: object,
    in_reply_to_message_id: object,
) -> tuple[EmailMessage, Reply | None, str]:
    """The message, the reply's part of the original (or ``None``) and the body checked.

    Every argument is checked and the account looked up before the one request a reply
    makes, the original's ``format=metadata`` read.
    """
    recipients = addresses("to", to, required=True)
    copies = addresses("cc", cc, required=False)
    blind = addresses("bcc", bcc, required=False)
    text = _body(body)
    given = check_subject(subject)
    original_id = (
        None
        if in_reply_to_message_id is None
        else _check_id("in_reply_to_message_id", in_reply_to_message_id)
    )
    client = client_for(account)
    reply = None
    if original_id is not None:
        original = await api.messages_get(
            client, original_id, format="metadata", metadata_headers=REPLY_HEADERS
        )
        reply = Reply.of(original)
    message = compose(to=recipients, cc=copies, bcc=blind, subject=given, body=text, reply=reply)
    return message, reply, text


def _sent(
    to: object,
    subject: object,
    cc: object,
    bcc: object,
    in_reply_to_message_id: object,
    reply: Reply | None,
) -> dict[str, Any]:
    """``meta.request`` of a composing task, ``account`` aside: never the body."""
    return {
        "to": to,
        "cc": cc,
        "bcc": bcc,
        "subject": subject,
        "in_reply_to_message_id": in_reply_to_message_id,
        "threadId": None if reply is None else reply.thread_id,
    }


def _compose_preview(
    endpoint: str, action: str, message: EmailMessage, text: str, reply: Reply | None
) -> ComposePreview:
    lines = text.splitlines()
    return ComposePreview(
        endpoint=endpoint,
        action=action,
        thread_id=None if reply is None else reply.thread_id,
        headers=preview_headers(message),
        body_lines=lines[:PREVIEW_LINES],
        body_line_count=len(lines),
    )


async def create_draft_async(
    account: str,
    to: list[str],
    subject: str,
    body: str,
    *,
    cc: list[str] | None = None,
    bcc: list[str] | None = None,
    in_reply_to_message_id: str | None = None,
    confirm: bool = False,
) -> Result[DraftOutcome]:
    r"""Store a new plain-text message as a draft in an account's mailbox, sending nothing.

    The draft waits in the mailbox's Drafts until someone sends it from Gmail or
    ``google_mail_delete_draft`` deletes it by the ``id`` this tool returns. Gmail sets
    From to the account's address. A reply (``in_reply_to_message_id``) joins the
    original's conversation: the draft carries its ``threadId``, In-Reply-To and
    References, and keeps its subject unless given another. Without ``confirm=true``
    nothing is stored: the answer is a preview (``performed`` false) with the headers
    and the body's first 10 lines; call again with ``confirm=true`` to store the draft.
    10 quota units; a reply reads the original first, 20 more, and so does its preview.

    Args:
        account: A declared account's name, e.g. ``demo`` or ``work``;
            ``google_mail_list_accounts`` on the read server lists them.
        to: The recipients, a list of one address at least, e.g.
            ``["alice@example.com"]`` or ``["Alice <alice@example.com>", "bob@example.com"]``.
        subject: The subject, e.g. ``"Lunch on Friday"``; for a reply ``""`` takes
            ``Re: `` and the original's subject, which Gmail needs to match to keep the
            reply in its conversation.
        body: The message's plain text, e.g. ``"Hi Alice,\n\nsee you on Friday."``.
        cc: Copy recipients, a list of addresses, e.g. ``["carol@example.com"]``.
        bcc: Blind copy recipients, a list of addresses, e.g. ``["dave@example.com"]``.
        in_reply_to_message_id: The id of the message this answers, e.g.
            ``"19a0b0c0d0e0f001"``, from ``google_mail_search_messages`` or
            ``google_mail_list_messages`` on the read server.
        confirm: ``true`` stores the draft; ``false`` (the default) only previews it.
    """
    message, reply, text = await _compose(
        account, to, subject, body, cc, bcc, in_reply_to_message_id
    )
    sent = _sent(to, subject, cc, bcc, in_reply_to_message_id, reply)
    if confirm is not True:
        action = (
            f"store this message as a draft in the mailbox of account {account}; nothing is sent"
        )
        preview = _compose_preview(DRAFTS_CREATE, action, message, text, reply)
        return Result[DraftOutcome](data=preview, meta=_meta(account, DRAFTS_CREATE, sent))
    draft = await api.drafts_create(
        client_for(account), raw=encode_raw(message), thread_id=sent["threadId"]
    )
    return Result[DraftOutcome](data=draft, meta=_meta(account, DRAFTS_CREATE, sent))


create_draft = sync(create_draft_async)


async def delete_draft_async(
    account: str, draft_id: str, *, confirm: bool = False
) -> Result[DeleteOutcome]:
    """Delete a draft of an account's mailbox immediately and permanently.

    The draft is not moved to the trash: it is gone (``drafts.delete``). Without
    ``confirm=true`` nothing is deleted: the answer is a preview (``performed`` false)
    naming the draft; call again with ``confirm=true`` to delete it. 10 quota units.

    Args:
        account: A declared account's name, e.g. ``demo`` or ``work``;
            ``google_mail_list_accounts`` on the read server lists them.
        draft_id: The draft's ``id``, e.g. ``"r-1234567890123456789"``, as
            ``google_mail_create_draft`` returned it; not the id of the draft's message.
        confirm: ``true`` deletes the draft; ``false`` (the default) only previews it.
    """
    draft_id = _check_id("draft_id", draft_id)
    client = client_for(account)
    sent = {"id": draft_id}
    if confirm is not True:
        preview = ActionPreview(
            endpoint=DRAFTS_DELETE,
            action=(
                f"delete the draft {draft_id} of account {account} immediately and "
                "permanently; it is not moved to the trash"
            ),
            id=draft_id,
        )
        return Result[DeleteOutcome](data=preview, meta=_meta(account, DRAFTS_DELETE, sent))
    await api.drafts_delete(client, draft_id)
    return Result[DeleteOutcome](
        data=DraftDeleted(id=draft_id), meta=_meta(account, DRAFTS_DELETE, sent)
    )


delete_draft = sync(delete_draft_async)


async def send_message_async(
    account: str,
    to: list[str],
    subject: str,
    body: str,
    *,
    cc: list[str] | None = None,
    bcc: list[str] | None = None,
    in_reply_to_message_id: str | None = None,
    confirm: bool = False,
) -> Result[SendOutcome]:
    r"""Send a plain-text message from an account to its To, Cc and Bcc recipients.

    Gmail sets From to the account's address and keeps the message in its Sent label; a
    message sent cannot be called back. A reply (``in_reply_to_message_id``) joins the
    original's conversation: the message carries its ``threadId``, In-Reply-To and
    References, and keeps its subject unless given another. Without ``confirm=true``
    nothing is sent: the answer is a preview (``performed`` false) with the headers and
    the body's first 10 lines; call again with ``confirm=true`` to send it. 100 quota
    units; a reply reads the original first, 20 more, and so does its preview.

    Args:
        account: A declared account's name, e.g. ``demo`` or ``work``;
            ``google_mail_list_accounts`` on the read server lists them.
        to: The recipients, a list of one address at least, e.g.
            ``["alice@example.com"]`` or ``["Alice <alice@example.com>", "bob@example.com"]``.
        subject: The subject, e.g. ``"Lunch on Friday"``; for a reply ``""`` takes
            ``Re: `` and the original's subject, which Gmail needs to match to keep the
            reply in its conversation.
        body: The message's plain text, e.g. ``"Hi Alice,\n\nsee you on Friday."``.
        cc: Copy recipients, a list of addresses, e.g. ``["carol@example.com"]``.
        bcc: Blind copy recipients, a list of addresses, e.g. ``["dave@example.com"]``;
            the others do not see them.
        in_reply_to_message_id: The id of the message this answers, e.g.
            ``"19a0b0c0d0e0f001"``, from ``google_mail_search_messages`` or
            ``google_mail_list_messages`` on the read server.
        confirm: ``true`` sends the message; ``false`` (the default) only previews it.
    """
    message, reply, text = await _compose(
        account, to, subject, body, cc, bcc, in_reply_to_message_id
    )
    sent = _sent(to, subject, cc, bcc, in_reply_to_message_id, reply)
    if confirm is not True:
        action = (
            f"send this message from account {account} to its To, Cc and Bcc recipients; "
            "once sent it cannot be called back"
        )
        preview = _compose_preview(MESSAGES_SEND, action, message, text, reply)
        return Result[SendOutcome](data=preview, meta=_meta(account, MESSAGES_SEND, sent))
    answer = await api.messages_send(
        client_for(account), raw=encode_raw(message), thread_id=sent["threadId"]
    )
    return Result[SendOutcome](data=answer, meta=_meta(account, MESSAGES_SEND, sent))


send_message = sync(send_message_async)


def _change(add: list[str], remove: list[str]) -> str:
    """``add the labels A to and remove the labels B from``, the parts that apply."""
    parts = []
    if add:
        parts.append(f"add the labels {', '.join(add)} to")
    if remove:
        parts.append(f"remove the labels {', '.join(remove)} from")
    return " and ".join(parts)


async def modify_message_async(
    account: str,
    message_id: str,
    *,
    add_label_ids: list[str] | None = None,
    remove_label_ids: list[str] | None = None,
    confirm: bool = False,
) -> Result[ChangeOutcome]:
    """Add labels to and remove labels from one message of an account's mailbox.

    Label ids are those ``google_mail_list_labels`` on the read server lists: system
    labels such as ``STARRED``, ``UNREAD``, ``IMPORTANT`` or ``INBOX`` and the user's own
    (``Label_1``); one label at least, at most 100 to add and 100 to remove. The answer
    is the message with the labels it carries afterwards. Without ``confirm=true``
    nothing changes: the answer is a preview (``performed`` false) naming the message
    and the labels; call again with ``confirm=true`` to change them. 5 quota units.

    Args:
        account: A declared account's name, e.g. ``demo`` or ``work``;
            ``google_mail_list_accounts`` on the read server lists them.
        message_id: The message's ``id``, e.g. ``"19a0b0c0d0e0f001"``, from
            ``google_mail_search_messages`` or ``google_mail_list_messages``.
        add_label_ids: Label ids to add, e.g. ``["STARRED"]`` or ``["Label_1"]``.
        remove_label_ids: Label ids to remove, e.g. ``["UNREAD"]``.
        confirm: ``true`` changes the labels; ``false`` (the default) only previews it.
    """
    message_id = _check_id("message_id", message_id)
    add = _labels("add_label_ids", add_label_ids)
    remove = _labels("remove_label_ids", remove_label_ids)
    if not add and not remove:
        raise _refuse(
            "add_label_ids and remove_label_ids are both empty; name one label at least, "
            "e.g. add_label_ids=['STARRED']"
        )
    client = client_for(account)
    sent = {"id": message_id, "addLabelIds": add or None, "removeLabelIds": remove or None}
    if confirm is not True:
        preview = ActionPreview(
            endpoint=MESSAGES_MODIFY,
            action=f"{_change(add, remove)} the message {message_id} of account {account}",
            id=message_id,
            add_label_ids=add,
            remove_label_ids=remove,
        )
        return Result[ChangeOutcome](data=preview, meta=_meta(account, MESSAGES_MODIFY, sent))
    answer = await api.messages_modify(
        client, message_id, add_label_ids=add, remove_label_ids=remove
    )
    return Result[ChangeOutcome](data=answer, meta=_meta(account, MESSAGES_MODIFY, sent))


modify_message = sync(modify_message_async)


async def trash_message_async(
    account: str, message_id: str, *, confirm: bool = False
) -> Result[ChangeOutcome]:
    """Move one message of an account's mailbox to the trash.

    The answer is the message with the labels it carries afterwards, ``TRASH`` among
    them. Without ``confirm=true`` nothing moves: the answer is a preview (``performed``
    false) naming the message; call again with ``confirm=true`` to trash it. 20 quota
    units.

    Args:
        account: A declared account's name, e.g. ``demo`` or ``work``;
            ``google_mail_list_accounts`` on the read server lists them.
        message_id: The message's ``id``, e.g. ``"19a0b0c0d0e0f001"``, from
            ``google_mail_search_messages`` or ``google_mail_list_messages``.
        confirm: ``true`` trashes the message; ``false`` (the default) only previews it.
    """
    message_id = _check_id("message_id", message_id)
    client = client_for(account)
    sent = {"id": message_id}
    if confirm is not True:
        preview = ActionPreview(
            endpoint=MESSAGES_TRASH,
            action=f"move the message {message_id} of account {account} to the trash",
            id=message_id,
        )
        return Result[ChangeOutcome](data=preview, meta=_meta(account, MESSAGES_TRASH, sent))
    answer = await api.messages_trash(client, message_id)
    return Result[ChangeOutcome](data=answer, meta=_meta(account, MESSAGES_TRASH, sent))


trash_message = sync(trash_message_async)
