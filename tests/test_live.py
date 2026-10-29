"""Live smoke tests, one per task, skipped unless XIPPIOS_LIVE_TESTS=1 (conventions §10).

Every live test takes the ``demo_account`` fixture: live runs reach the demo account only,
a throwaway gmail.com mailbox, with ``GOOGLE_DEMO_REFRESH_TOKEN`` and the OAuth client pair
from the environment (``.github/workflows/live.yml`` passes the three org secrets). They
assert shapes and counts, never a raw payload or the mailbox's address, since pytest's
assertion introspection prints what an ``assert`` compares into a public log, even under
``--tb=short``: every assertion is on a named boolean or a plain value, never on an
attribute chain of a record that holds personal data (a ``Result``, ``Profile``,
``Message``, ``DecodedMessage`` or preview, whose repr carries the address and the
Message-IDs), and ``as_kind`` checks a record's type without printing it.

The write tasks need demo's write grant (``gmail.modify``) and are tested in two
sequences under the write safety rule (the briefing's "Write safety"): demo writes to
itself only, its own address read from ``users.getProfile`` at runtime (``own_address``),
never written down; ``self_addressed`` refuses, before any write, a To, Cc or Bcc other
than that address or a Subject without ``MARK``, ``xippios-live``. Each sequence sweeps
first: the marked messages an earlier run left outside the trash are trashed, drafts
aside (``sweep``; a leftover draft is not reachable by its draft id without
``drafts.list``, which 0.1.0 does not wrap). Then it
creates, reads back with ``get_message`` and deletes the draft or trashes the message
through the task, and in a ``finally`` through the wrapper should an assertion fail
first. The draft sequence previews, creates, reads back and deletes one draft; the
message sequence sends one message, reads it back, previews a reply to it (the
original's metadata read), stars and unstars it with two modifies and trashes it.

The calls of one run, 544 quota units (``docs/upstream/pages/quota.md``) with nothing to
sweep: one token request per test, which costs no quota; the reads, 242:
``users.getProfile`` 1; ``labels.list`` 1; ``messages.list`` 5, three times (the list,
and an id each for the message and the thread); the search of two messages, 5 + 2 x 20;
``messages.get`` with ``format=full`` 60; ``threads.get`` with ``format=full`` 120;
``list_accounts`` sends nothing; the writes, 302: per sequence ``users.getProfile`` 1 and
the sweep's search 5 (20 more and a trash, 20, per leftover); the draft's create 10,
read-back 60 and delete 10; the message's send 100, read-back 60, reply preview 20, two
modifies 5 each and trash 20.
"""

from datetime import timedelta
from email.utils import getaddresses
from typing import cast

import pytest
from xippios.core import sync

from xippios.google.mail import api, tasks, tasks_write
from xippios.google.mail._client import client_for
from xippios.google.mail._decode import HEADERS, header_value
from xippios.google.mail.models import (
    ComposePreview,
    DecodedMessage,
    Draft,
    DraftDeleted,
    Label,
    ListMessagesResponse,
    Message,
    Profile,
    SearchRow,
)

KEPT = {name.lower() for name in HEADERS}
MARK = "xippios-live"
"""The marker in the Subject of every message a live test writes, and the sweep's query."""
BODY = "Written by the xippios-google-mail live tests, to this mailbox itself."

pytestmark = pytest.mark.live


def as_kind[T](value: object, kind: type[T], what: str) -> T:
    """``value`` as a ``kind``, or the test fails naming ``what``, never printing ``value``."""
    is_kind = isinstance(value, kind)
    assert is_kind, f"{what} is a {kind.__name__}"
    return cast("T", value)


def test_list_accounts_names_demo_alone(demo_account: str) -> None:
    result = tasks.list_accounts()
    names, tenant = result.data, result.meta.tenant
    assert names == [demo_account]
    assert tenant is None


def test_get_profile_answers_for_demo(demo_account: str) -> None:
    result = tasks.get_profile(demo_account)
    profile = as_kind(result.data, Profile, "the answer")
    has_an_address = "@" in profile.email_address
    assert has_an_address, "email_address is an address"
    messages_total, threads_total = profile.messages_total, profile.threads_total
    assert messages_total >= 0
    assert threads_total >= 0
    history_digits = profile.history_id.isdigit()
    assert history_digits, "history_id is a uint64 sent as a string of digits"
    tenant, request, endpoint = result.meta.tenant, result.meta.request, result.meta.endpoint
    assert (tenant, request) == (demo_account, {"account": demo_account})
    assert endpoint == "gmail.users.getProfile"


def test_list_labels_answers_for_demo(demo_account: str) -> None:
    result = tasks.list_labels(demo_account)
    labels = result.data
    every_label = all(isinstance(label, Label) for label in labels)
    assert every_label, "every item is a Label"
    ids = [label.id for label in labels]
    assert ids, "a mailbox has its system labels"
    assert "INBOX" in ids
    tenant, endpoint = result.meta.tenant, result.meta.endpoint
    assert (tenant, endpoint) == (demo_account, "gmail.users.labels.list")


def first_message_id(account: str) -> tuple[str, str]:
    """The first id the list returns and its thread id, from one ``messages.list`` (5 units)."""
    refs = tasks.list_messages(account, max_results=1).data.messages
    count = len(refs)
    assert count == 1, "demo's mailbox holds messages"
    return refs[0].id, refs[0].thread_id


def test_list_messages_answers_a_page_of_ids(demo_account: str) -> None:
    result = tasks.list_messages(demo_account, max_results=2, label_ids=["INBOX"])
    page = as_kind(result.data, ListMessagesResponse, "the answer")
    count = len(page.messages)
    assert 1 <= count <= 2
    ids_alnum = all(m.id.isalnum() and m.thread_id.isalnum() for m in page.messages)
    assert ids_alnum, "message and thread ids are alphanumeric"
    estimate = page.result_size_estimate
    assert estimate is not None
    assert estimate >= 0
    total, request = result.meta.total, result.meta.request
    assert total is None
    assert request == {"account": demo_account, "labelIds": ["INBOX"], "maxResults": 2}


def test_search_messages_shows_two_messages(demo_account: str) -> None:
    result = tasks.search_messages(demo_account, "in:inbox", max_results=2)
    rows = result.data
    count = len(rows)
    assert 1 <= count <= 2
    every_row = all(isinstance(row, SearchRow) for row in rows)
    assert every_row, "every item is a SearchRow"
    has_headers = all(
        isinstance(value, str) for row in rows for value in (row.from_, row.subject, row.date)
    )
    assert has_headers, "From, Subject and Date came back"
    asked = result.meta.request["metadataHeaders"]
    assert asked == ["From", "Subject", "Date"]


def test_get_message_decodes_a_message(demo_account: str) -> None:
    message_id, thread_id = first_message_id(demo_account)
    result = tasks.get_message(demo_account, message_id)
    view = as_kind(result.data, DecodedMessage, "the answer")
    same_ids = view.id == message_id and view.thread_id == thread_id
    assert same_ids, "the view is of the message asked for"
    names = {header.name.lower() for header in view.headers}
    assert names <= KEPT, "the allowlist alone"
    assert {"from", "date"} <= names
    text_is_text = view.text is None or isinstance(view.text, str)
    assert text_is_text, "text is a string or None"
    no_html = view.html is None
    assert no_html, "html only on request"
    offset = None if view.internal_date is None else view.internal_date.utcoffset()
    assert offset == timedelta(0), "an aware UTC datetime"
    endpoint = result.meta.endpoint
    assert endpoint == "gmail.users.messages.get"


def test_get_thread_decodes_its_messages(demo_account: str) -> None:
    _, thread_id = first_message_id(demo_account)
    result = tasks.get_thread(demo_account, thread_id)
    views = result.data
    count = len(views)
    assert count >= 1, "a thread holds a message at least"
    text_only = all(isinstance(view, DecodedMessage) and view.html is None for view in views)
    assert text_only, "every view is a DecodedMessage, text only"
    same_thread = all(view.thread_id == thread_id for view in views)
    assert same_thread, "every view is of the thread asked for"
    start, endpoint = result.meta.request["start"], result.meta.endpoint
    assert (start, endpoint) == (0, "gmail.users.threads.get")


# The write tasks, under the write safety rule ----------------------------------------


def self_addressed(
    own: str,
    *,
    to: list[str],
    subject: str,
    cc: list[str] | None = None,
    bcc: list[str] | None = None,
) -> None:
    """The write safety rule, before any write: demo writes to itself, with the marker.

    Args:
        own: Demo's own address, as ``users.getProfile`` answered it at runtime.
        to: The To addresses of the message about to be written.
        subject: Its Subject.
        cc: Its Cc addresses.
        bcc: Its Bcc addresses.

    Raises:
        ValueError: ``to`` is empty, an address of ``to``, ``cc`` or ``bcc`` is not
            ``own`` (compared without regard to case, a display name allowed), or the
            subject lacks ``MARK``; the message names the parameter, never an address.
    """
    if not to:
        raise ValueError("to names no address; demo writes to itself")
    for name, values in (("to", to), ("cc", cc), ("bcc", bcc)):
        for value in values or []:
            found = getaddresses([value])
            if len(found) != 1 or found[0][1].lower() != own.lower():
                raise ValueError(f"{name} holds an address other than demo's own")
    if MARK not in subject:
        raise ValueError(f"the subject carries no {MARK}")


@pytest.fixture
def own_address(demo_account: str) -> str:
    """Demo's own address, read at runtime (``users.getProfile``, 1 unit), never written down."""
    return tasks.get_profile(demo_account).data.email_address


def sweep(account: str) -> int:
    """Trash every marked message an earlier run left, drafts aside; how many it trashed.

    ``search_messages`` leaves the trash and spam out, and a found message is trashed
    only when its Subject carries ``MARK``: the search's words are no proof.
    """
    found = tasks.search_messages(account, f'subject:"{MARK}" -in:drafts', max_results=50).data
    trash = sync(api.messages_trash)
    left = [row.id for row in found if MARK in (row.subject or "")]
    for message_id in left:
        trash(client_for(account), message_id)
    return len(left)


def subject_of(view: DecodedMessage) -> str | None:
    return header_value(view.headers, "Subject")


def test_a_draft_to_demo_itself_is_created_read_back_and_deleted(
    demo_account: str, own_address: str
) -> None:
    sweep(demo_account)
    to, subject = [own_address], f"{MARK} draft"
    self_addressed(own_address, to=to, subject=subject)
    preview = tasks_write.create_draft(demo_account, to, subject, BODY).data
    as_kind(preview, ComposePreview, "the answer without confirm")
    self_addressed(own_address, to=to, subject=subject)
    created = tasks_write.create_draft(demo_account, to, subject, BODY, confirm=True).data
    draft = as_kind(created, Draft, "the confirmed answer")
    deleted = False
    try:
        message = as_kind(draft.message, Message, "the draft's message")
        labels = message.label_ids
        assert "DRAFT" in labels
        view = as_kind(
            tasks.get_message(demo_account, message.id).data, DecodedMessage, "the read-back"
        )
        got_subject = subject_of(view)
        assert got_subject == subject
        to_self = own_address.lower() in (header_value(view.headers, "To") or "").lower()
        assert to_self, "the draft is addressed to demo itself"
        got_text = (view.text or "").strip()
        assert got_text == BODY
        acknowledgement = tasks_write.delete_draft(demo_account, draft.id, confirm=True).data
        acknowledged = acknowledgement == DraftDeleted(id=draft.id)
        assert acknowledged, "the deletion is acknowledged with the draft's id"
        deleted = True
    finally:
        if not deleted:
            sync(api.drafts_delete)(client_for(demo_account), draft.id)


def test_a_message_to_demo_itself_is_sent_read_back_relabelled_and_trashed(
    demo_account: str, own_address: str
) -> None:
    sweep(demo_account)
    to, subject = [own_address], f"{MARK} send"
    self_addressed(own_address, to=to, subject=subject)
    answer = tasks_write.send_message(demo_account, to, subject, BODY, confirm=True).data
    sent = as_kind(answer, Message, "the confirmed answer")
    trashed = False
    try:
        labels = sent.label_ids
        assert "SENT" in labels
        view = as_kind(
            tasks.get_message(demo_account, sent.id).data, DecodedMessage, "the read-back"
        )
        got_subject = subject_of(view)
        assert got_subject == subject
        names = {header.name.lower() for header in view.headers}
        assert {"from", "date", "message-id"} <= names, "Gmail sets From, Date and Message-ID"
        reply = as_kind(
            tasks_write.create_draft(
                demo_account, to, "", BODY, in_reply_to_message_id=sent.id
            ).data,
            ComposePreview,
            "the reply's preview",
        )
        shown = {header.name: header.value for header in reply.headers}
        reply_subject = shown.get("Subject")
        assert reply_subject == f"Re: {subject}"
        answers_it = shown.get("In-Reply-To") == header_value(view.headers, "Message-ID")
        assert answers_it, "In-Reply-To names the sent message's Message-ID"
        same_thread = reply.thread_id == sent.thread_id
        assert same_thread, "the reply joins the sent message's thread"
        starred = tasks_write.modify_message(
            demo_account, sent.id, add_label_ids=["STARRED"], confirm=True
        ).data
        starred_labels = as_kind(starred, Message, "the first modify's answer").label_ids
        assert "STARRED" in starred_labels
        unstarred = tasks_write.modify_message(
            demo_account, sent.id, remove_label_ids=["STARRED"], confirm=True
        ).data
        unstarred_labels = as_kind(unstarred, Message, "the second modify's answer").label_ids
        assert "STARRED" not in unstarred_labels
        moved = tasks_write.trash_message(demo_account, sent.id, confirm=True).data
        trashed_labels = as_kind(moved, Message, "the trash's answer").label_ids
        assert "TRASH" in trashed_labels
        trashed = True
    finally:
        if not trashed:
            sync(api.messages_trash)(client_for(demo_account), sent.id)
