"""Live smoke tests, one per task, skipped unless XIPPIOS_LIVE_TESTS=1 (conventions §10).

Every live test takes the ``demo_account`` fixture: live runs reach the demo account only,
a throwaway gmail.com mailbox, with ``GOOGLE_DEMO_REFRESH_TOKEN`` and the OAuth client pair
from the environment (``.github/workflows/live.yml`` passes the three org secrets). They
assert shapes and counts, never a raw payload or the mailbox's address, since pytest's
assertion introspection prints what an ``assert`` compares into a public log. The calls of
one run: one token request, which costs no quota, and ``users.getProfile``, one quota
unit; ``list_accounts`` sends nothing.
"""

import pytest

from xippios.google.mail import tasks
from xippios.google.mail.models import Profile

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
