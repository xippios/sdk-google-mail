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

Tools are read-only and named `google_mail_<verb>_<noun>`. Results are capped, at
200 rows or 64 KB unless a tool has a cap of its own, with a note on how to see the rest.
`--account <name>` binds the server to one account: every tool acts on it and takes no
`account`. `--http` serves streamable HTTP instead of stdio, on a loopback `--host` only
(`127.0.0.1`, `localhost`, `::1`): the server reaches every declared mailbox and carries no
authentication, so serving it beyond loopback takes `--allow-remote` and a gateway in front.

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

## Upstream terms and attribution

Data comes from Gmail (https://developers.google.com/workspace/gmail/api), on whatever terms upstream
states for it, if any; this package adds no rights to it. Documentation snapshot and its
attribution: `docs/upstream/FETCHED.md`.

## Licence

Apache-2.0. See [LICENSE](LICENSE).
