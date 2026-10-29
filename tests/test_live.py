"""Live smoke tests, one per task, skipped unless XIPPIOS_LIVE_TESTS=1 (conventions §10).

Every live test takes the ``demo_account`` fixture: live runs reach the demo account only,
a throwaway gmail.com mailbox, with ``GOOGLE_DEMO_REFRESH_TOKEN`` and the OAuth client pair
from the environment (``.github/workflows/live.yml`` passes the three org secrets). They
assert shapes and counts, never a raw payload or the mailbox's address, since pytest's
assertion introspection prints what an ``assert`` compares into a public log. The calls of
one run, 242 quota units (``docs/upstream/pages/quota.md``): one token request per
test, which costs no quota; ``users.getProfile`` 1; ``labels.list`` 1; ``messages.list``
5, three times (the list, and an id each for the message and the thread); the search of
two messages, 5 + 2 x 20; ``messages.get`` with ``format=full`` 60; ``threads.get`` with
``format=full`` 120. ``list_accounts`` sends nothing.
"""

import pytest

from xippios.google.mail import tasks
from xippios.google.mail._decode import HEADERS
from xippios.google.mail.models import (
    DecodedMessage,
    Label,
    ListMessagesResponse,
    Profile,
    SearchRow,
)

KEPT = {name.lower() for name in HEADERS}

pytestmark = pytest.mark.live


def test_list_accounts_names_demo_alone(demo_account: str) -> None:
    result = tasks.list_accounts()
    assert result.data == [demo_account]
    assert result.meta.tenant is None


def test_get_profile_answers_for_demo(demo_account: str) -> None:
    result = tasks.get_profile(demo_account)
    assert isinstance(result.data, Profile)
    has_an_address = "@" in result.data.email_address
    assert has_an_address, "email_address is an address"
    assert result.data.messages_total >= 0
    assert result.data.threads_total >= 0
    assert result.data.history_id.isdigit()
    assert (result.meta.tenant, result.meta.request) == (demo_account, {"account": demo_account})
    assert result.meta.endpoint == "gmail.users.getProfile"


def test_list_labels_answers_for_demo(demo_account: str) -> None:
    result = tasks.list_labels(demo_account)
    assert result.data, "a mailbox has its system labels"
    assert all(isinstance(label, Label) for label in result.data)
    assert "INBOX" in {label.id for label in result.data}
    assert (result.meta.tenant, result.meta.endpoint) == (demo_account, "gmail.users.labels.list")


def first_message_id(account: str) -> tuple[str, str]:
    """The id and thread id of the newest message, from one ``messages.list`` (5 units)."""
    listed = tasks.list_messages(account, max_results=1).data
    assert len(listed.messages) == 1, "demo's mailbox holds messages"
    return listed.messages[0].id, listed.messages[0].thread_id


def test_list_messages_answers_a_page_of_ids(demo_account: str) -> None:
    result = tasks.list_messages(demo_account, max_results=2, label_ids=["INBOX"])
    assert isinstance(result.data, ListMessagesResponse)
    assert 1 <= len(result.data.messages) <= 2
    assert all(m.id.isalnum() and m.thread_id.isalnum() for m in result.data.messages)
    assert result.data.result_size_estimate is not None
    assert result.data.result_size_estimate >= 0
    assert result.meta.total is None
    assert result.meta.request == {"account": demo_account, "labelIds": ["INBOX"], "maxResults": 2}


def test_search_messages_shows_two_messages(demo_account: str) -> None:
    result = tasks.search_messages(demo_account, "in:inbox", max_results=2)
    assert 1 <= len(result.data) <= 2
    for row in result.data:
        assert isinstance(row, SearchRow)
        has_headers = all(isinstance(value, str) for value in (row.from_, row.subject, row.date))
        assert has_headers, "From, Subject and Date came back"
    assert result.meta.request["metadataHeaders"] == ["From", "Subject", "Date"]


def test_get_message_decodes_a_message(demo_account: str) -> None:
    message_id, thread_id = first_message_id(demo_account)
    result = tasks.get_message(demo_account, message_id)
    view = result.data
    assert isinstance(view, DecodedMessage)
    assert (view.id == message_id, view.thread_id == thread_id) == (True, True)
    names = {header.name.lower() for header in view.headers}
    assert names <= KEPT, "the allowlist alone"
    assert {"from", "date"} <= names
    assert view.text is None or isinstance(view.text, str)
    assert view.html is None
    assert (view.internal_date or "").isdigit()
    assert result.meta.endpoint == "gmail.users.messages.get"


def test_get_thread_decodes_its_messages(demo_account: str) -> None:
    _, thread_id = first_message_id(demo_account)
    result = tasks.get_thread(demo_account, thread_id)
    assert result.data, "a thread holds a message at least"
    assert all(isinstance(view, DecodedMessage) and view.html is None for view in result.data)
    assert all(view.thread_id == thread_id for view in result.data)
    assert (result.meta.request["start"], result.meta.endpoint) == (0, "gmail.users.threads.get")
