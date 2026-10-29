# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/) (pre-1.0: breaking changes bump the minor).

## [Unreleased]

### Added

- Scaffold from [sdk-template](https://github.com/xippios/sdk-template): configured
  client for Gmail, empty task layer, MCP server and FastAPI router
  wired to it, test harness with core's family check, CI, the nightly live tests and
  the release train, in which a release PR's merge tags and publishes.
- The read server's entry point is `xippios-google-mail-mcp-read`, since a write
  server with an entry point of its own follows in this release (decision of
  2026-10-29).
- The documentation snapshot in `docs/upstream/`, captured on 2026-10-29 by
  `scripts/refresh_upstream.py`: Gmail's discovery document (revision 20261026, kept
  with its keys sorted), the API's usage limits, errors and scopes, the Gmail Help
  article on search operators, and Google's OAuth 2.0 overview with its guides for
  desktop apps and web server applications, the pages as Markdown.
- README and CLAUDE.md name the credentials the API takes, an OAuth 2.0 bearer renewed
  from a refresh token per account with the operator's Desktop-app client, where the
  scaffold, rendered without a key, said none was needed; CLAUDE.md's rate-limit row
  names upstream's quota, 6,000 units per minute per user per project, where it said
  upstream documents no limit.
- Several Gmail accounts from the environment: `GOOGLE_ACCOUNTS` names them, one
  `GOOGLE_<NAME>_REFRESH_TOKEN` each, and the test account `demo` is declared by
  `GOOGLE_DEMO_REFRESH_TOKEN` alone. Each account has a client of its own, whose auth
  exchanges the refresh token at Google's token endpoint with the operator's OAuth client
  (`GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`) and sends the access token as a bearer,
  renewed in memory before it expires and once more on a 401; nothing is written to disk.
  A revoked or expired refresh token is an `AuthError` that says how to consent again; a
  client the token endpoint refuses is a `ConfigError` naming the two client variables.
- `xippios-google-mail-auth --account <name> [--scopes read|write]`, the consent tool
  (`read` by default): a loopback redirect with PKCE that prints the account's refresh
  token once and stores nothing.
- `list_accounts`, the declared accounts by name, and `get_profile(account)`, the
  account's address, message and thread counts and history id (`users.getProfile`).
- `--account <name>` binds the read server to one account: every tool acts on it and
  takes no `account`.
- The read server is served over `--http` on a loopback `--host` only, unless
  `--allow-remote` is passed: it reaches every declared mailbox and carries no
  authentication.
- Gmail's error answers mapped: a spent per-minute or daily quota is a `RateLimitError`
  (60 s, 3,600 s), a grant without the scope a call needs an `AuthError` naming the
  scopes granted and the consent to re-run, a domain policy or a refused access token an
  `AuthError`; each account's requests are paced at 60 a minute and held back for a
  minute after a spent quota.

- The read tasks on a mailbox, each with the account first: `list_labels`, the labels
  whose ids `label_ids` takes (`users.labels.list`); `list_messages`, one page of message
  and thread ids for a query in Gmail's own search syntax, sent verbatim, or for labels,
  with upstream's `resultSizeEstimate` as data and no total (`users.messages.list`);
  `search_messages`, at most 50 messages with their From, Subject, Date and snippet, a
  list and one `format=metadata` call per message (5 + 20 quota units each);
  `get_message`, one message's decoded view (`users.messages.get`, `format=full`): its
  From, To, Cc, Bcc, Date, Subject, Message-ID, In-Reply-To and References headers, the
  first plain-text part decoded in its charset, the first HTML part on request
  (`include_html`), and its attachments' metadata, never their content, a message over
  the MCP result cap coming back as its first 64 KB with a note saying so; and
  `get_thread`, the views of a thread's messages as text (`users.threads.get`,
  `format=full`), from `start` and at most `limit`, a cut answer ending with where to
  resume.
- Endpoint wrappers for `users.labels.list`, `users.messages.list`, `users.messages.get`,
  `users.threads.get` and `users.messages.attachments.get`, the last without a task, and
  faithful models of their answers, the whole MIME tree of a message among them.
- The fixture recorder records through a read task and scrubs a message or thread answer
  as JSON before it writes: headers cut to an allowlist on every part, Message-ID,
  In-Reply-To and References numbered `<n@example.com>`, display names in the address
  headers replaced by `User`, every body decoded, its addresses replaced and encoded
  again, attachment ids numbered, a profile-picture URL redacted and the raw message
  never kept; its final check reads the decoded bodies too.
- The write tools, on a server of their own, `xippios-google-mail-mcp-write`, which
  takes `--account` too and is served over `--http` on a loopback `--host` only unless
  `--allow-remote` is passed: `create_draft` and `send_message` build a plain-text
  message in UTF-8 (To, Cc, Bcc and Subject; From is Gmail's) and store it as a draft
  (`users.drafts.create`) or send it (`users.messages.send`), a reply joining the
  original's conversation with its thread, In-Reply-To and References; `delete_draft`
  deletes a draft permanently (`users.drafts.delete`); `modify_message` adds and removes
  a message's labels (`users.messages.modify`); `trash_message` moves a message to the
  trash (`users.messages.trash`). Every one previews and changes nothing unless called
  with `confirm=True`: a message's headers and first lines, or the draft or message and
  the labels; confirmed, it returns Gmail's answer. They need an account consented with
  `--scopes write`, are re-exported from `xippios.google.mail` and are no HTTP routes;
  a result's request echo never carries the message's text.
- The fixture recorder records the write tools under a safety rule: the demo account
  writes to its own address only, read from its profile when it records, every message
  it writes carries `xippios-live` in its subject, and the raw message a write sends is
  dropped from the recorded request; the live tests follow the same rule and clean up
  what they write.
