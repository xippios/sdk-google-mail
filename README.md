# xippios-google-mail

Typed Python client, MCP server and FastAPI routes for
[Gmail](https://developers.google.com/workspace/gmail/api), part of the
[xippios SDK family](https://github.com/xippios). Names and semantics follow the
upstream documentation; every response carries release metadata (source, endpoint,
retrieved_at, published_at, vintage).

## Install

```bash
uv add xippios-google-mail            # client only
uv add "xippios-google-mail[mcp]"     # + MCP server
uv add "xippios-google-mail[http]"    # + FastAPI router
```

The API takes an OAuth 2.0 bearer token, which the package renews in memory from a
refresh token per Gmail account and never writes to disk. The environment names the
operator's Desktop-app OAuth client, `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET`, the
accounts, `GOOGLE_ACCOUNTS` (`work,private`), and one `GOOGLE_<NAME>_REFRESH_TOKEN` per
account (the test account `demo` is declared by `GOOGLE_DEMO_REFRESH_TOKEN` alone and
never listed). Every task takes the account's name first; `list_accounts` names the
declared ones.

A refresh token comes from the consent tool, which listens on `http://127.0.0.1:<port>/`,
prints the authorization URL to open in a browser signed in to the account, and prints the
refresh token once, to stdout; it stores nothing:

```bash
xippios-google-mail-auth --account work                  # --scopes read: gmail.readonly
xippios-google-mail-auth --account work --scopes write   # gmail.modify, every read too
```

## 30-second example

```python
from xippios.google.mail import tasks

tasks.list_accounts().data  # ["work", "private"]
result = tasks.get_profile("work")
result.data.messages_total, result.meta.tenant, result.meta.retrieved_at

found = tasks.search_messages("work", "from:alice@example.com newer_than:7d", max_results=5)
[(row.from_, row.subject, row.date) for row in found.data]
message = tasks.get_message("work", found.data[0].id).data
message.headers, message.text, message.attachments  # the decoded view; html on request
```

`q` is Gmail's own search syntax, sent as given. `get_message` returns a decoded view of
the message: its From, To, Cc, Bcc, Date, Subject, Message-ID, In-Reply-To and References
headers, the first plain-text part decoded, the first HTML part with `include_html=True`,
and its attachments' names, types and sizes, never their content. `get_thread` returns
the same view of each message in a thread, as text.

The write tasks, in `xippios.google.mail.tasks_write` (re-exported from
`xippios.google.mail`), need an account consented with `--scopes write`, and each
previews unless called with `confirm=True`:

```python
from xippios.google.mail import tasks_write

preview = tasks_write.send_message("work", ["alice@example.com"], "Lunch on Friday", "See you!")
preview.data.headers, preview.data.body_lines  # nothing was sent
sent = tasks_write.send_message(
    "work", ["alice@example.com"], "Lunch on Friday", "See you!", confirm=True
)
sent.data.id, sent.data.label_ids  # upstream's answer: ["SENT"], ...
```

Task functions are listed in the status table below as they are added.

## MCP

```json
{
  "mcpServers": {
    "google-mail": {
      "command": "uvx",
      "args": ["--from", "xippios-google-mail[mcp]", "xippios-google-mail-mcp-read"]
    }
  }
}
```

The read server's tools are read-only and named `google_mail_<verb>_<noun>`. Results are capped, at
200 rows or 64 KB unless a tool has a cap of its own, with a note on how to see the rest.
`google_mail_search_messages` reads at most 50 messages a call (a list and one call per
message, 5 + 20 quota units each), and `google_mail_get_thread` reads a long thread in
parts with `start` and `limit`: a cut answer ends with where to resume.
`google_mail_get_message` returns a message over the cap as its first 64 KB with a note:
`include_html=False` leaves the HTML out, and the rest is read in a mail client.
`--account <name>` binds the server to one account: every tool acts on it and takes no
`account`. `--http` serves streamable HTTP instead of stdio, on a loopback `--host` only
(`127.0.0.1`, `localhost`, `::1`): the server reaches every declared mailbox and carries no
authentication, so serving it beyond loopback takes `--allow-remote` and a gateway in front.

The write tools run in a server of their own, `xippios-google-mail-mcp-write`, so a
client of the read server never sees one; it takes `--account` too, and core holds it to
a loopback `--host` over `--http` unless `--allow-remote` is passed:

```json
{
  "mcpServers": {
    "google-mail-write": {
      "command": "uvx",
      "args": ["--from", "xippios-google-mail[mcp]", "xippios-google-mail-mcp-write"]
    }
  }
}
```

Five write tools store a draft (`google_mail_create_draft`), delete one
(`google_mail_delete_draft`), send a message (`google_mail_send_message`), change a
message's labels (`google_mail_modify_message`) and move a message to the trash
(`google_mail_trash_message`). Every one returns a preview and changes nothing unless it
is called with `confirm=true`: a message's preview shows its To, Cc, Bcc, Subject,
In-Reply-To and References and the body's first 10 lines, the others name the draft or
the message and the labels. Confirmed, a tool returns Gmail's answer: the draft, or the
message with its labels; `google_mail_delete_draft`, which Gmail answers without a body,
returns the package's acknowledgement, `{"performed": true, "id": "<the draft's id>"}`.
A message is plain text in UTF-8, and Gmail sets From to the
account's address; a reply (`in_reply_to_message_id`) joins the original's conversation
and reads the original's headers first, its preview too. The write tools are no HTTP
routes.

## Status

<!-- Core's family check (tests/test_family.py) reads this table: keep the "## Status"
heading and the "Task function" column. Each row's Task function cell starts with the
task's name in backticks, alone or as its call (`get_data(code, year)`); several tasks in
one row are backticked names separated by commas; prose may follow. The tables list
exactly tasks.__all__. -->

| Upstream operation | Task function | MCP tool | Status |
| --- | --- | --- | --- |
| none: the declared accounts | `list_accounts` | `google_mail_list_accounts` | available; names only, no request |
| `GET gmail/v1/users/{userId}/profile` (`users.getProfile`) | `get_profile(account)` | `google_mail_get_profile` | available; `userId` `me`, the account's own |
| `GET gmail/v1/users/{userId}/labels` (`users.labels.list`) | `list_labels(account)` | `google_mail_list_labels` | available; the label ids `label_ids` takes |
| `GET gmail/v1/users/{userId}/messages` (`users.messages.list`) | `list_messages(account, q=None, label_ids=None, max_results=None, page_token=None, include_spam_trash=False)` | `google_mail_list_messages` | available; ids only, one page; `q` in Gmail's search syntax, verbatim; `result_size_estimate` upstream's estimate, `meta.total` `None` |
| `users.messages.list`, then `GET gmail/v1/users/{userId}/messages/{id}` (`users.messages.get`, `format=metadata`) per id | `search_messages(account, q, label_ids=None, max_results=20)` | `google_mail_search_messages` | available; From, Subject, Date and snippet per message; `max_results` at most 50 (5 + 20 quota units a message) |
| `GET gmail/v1/users/{userId}/messages/{id}` (`users.messages.get`, `format=full`) | `get_message(account, message_id, include_html=False)` | `google_mail_get_message` | available; the decoded view: nine headers, the plain text, the HTML on request, the attachments' metadata (no content) |
| `GET gmail/v1/users/{userId}/threads/{id}` (`users.threads.get`, `format=full`) | `get_thread(account, thread_id, start=0, limit=None)` | `google_mail_get_thread` | available; each message's decoded view, text only; `start` and `limit` read a long thread in parts |
| `POST gmail/v1/users/{userId}/drafts` (`users.drafts.create`) | `create_draft(account, to, subject, body, cc=None, bcc=None, in_reply_to_message_id=None, confirm=False)` | `google_mail_create_draft` (write server) | available; a preview unless `confirm=True`; a reply reads the original's headers (`users.messages.get`, `format=metadata`) |
| `DELETE gmail/v1/users/{userId}/drafts/{id}` (`users.drafts.delete`) | `delete_draft(account, draft_id, confirm=False)` | `google_mail_delete_draft` (write server) | available; a preview unless `confirm=True`; deleted permanently, not trashed |
| `POST gmail/v1/users/{userId}/messages/send` (`users.messages.send`) | `send_message(account, to, subject, body, cc=None, bcc=None, in_reply_to_message_id=None, confirm=False)` | `google_mail_send_message` (write server) | available; a preview unless `confirm=True`; plain text in UTF-8, From set by Gmail |
| `POST gmail/v1/users/{userId}/messages/{id}/modify` (`users.messages.modify`) | `modify_message(account, message_id, add_label_ids=None, remove_label_ids=None, confirm=False)` | `google_mail_modify_message` (write server) | available; a preview unless `confirm=True`; label ids only, 100 each at most |
| `POST gmail/v1/users/{userId}/messages/{id}/trash` (`users.messages.trash`) | `trash_message(account, message_id, confirm=False)` | `google_mail_trash_message` (write server) | available; a preview unless `confirm=True` |

## Upstream terms and attribution

Data comes from Gmail (https://developers.google.com/workspace/gmail/api), on whatever terms upstream
states for it, if any; this package adds no rights to it. Documentation snapshot and its
attribution: `docs/upstream/FETCHED.md`.

## Licence

Apache-2.0. See [LICENSE](LICENSE).
