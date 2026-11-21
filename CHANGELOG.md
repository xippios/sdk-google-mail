# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/) (pre-1.0: breaking changes bump the minor).

## [0.2.0] - 2026-11-21

### Changed

- A keyed command runs as `direnv exec .` from the clone or a worktree, in `CLAUDE.md` and
  the recorder's docstring, where both named a directory outside the repository. Four
  docstrings that cited a document no reader can open state their reason, or cite
  `CLAUDE.md` and sdk-core ADR-0025. (https://github.com/xippios/sdk-google-mail/issues/18)
- The usage line and the error prefix of `xippios-google-mail-mcp-read` and
  `xippios-google-mail-mcp-write` name the command that runs, where both named the server,
  `xippios-google-mail`, and the read server's help of `--allow-remote` speaks of a read
  server, where it spoke of a write server. The read server's refusal of a remote `--host`
  is core's, `xippios-google-mail is a read server held to loopback and carries no
  authentication`, where the package's own said that the server reaches mailboxes, and it
  comes after the check of `--account`, where the package's came first.
  (https://github.com/xippios/sdk-core/issues/167)
- A route answers an argument that its parameter's type refuses, or one that is missing,
  as problem details before the task runs: status 422, title `InvalidRequestError` and
  the detail `<argument>: <message>`, as the MCP tools word it. FastAPI's own 422 answered
  a `detail` list that echoed the value sent. (https://github.com/xippios/sdk-core/issues/66)
- The refusal of a `max_results` over 50 by `search_messages` ends naming the tool of
  `list_messages` too, `(over MCP, the tool google_mail_list_messages)`, where it named
  the task alone. (https://github.com/xippios/sdk-core/issues/111)
- A `google_mail_get_thread` answer whose message at `start` alone passes the cap says so
  in its note, `the row at offset <start> alone exceeds the byte cap`, and names no resume
  offset, where it ended `resume with start=<start>`, the start the call had just used.
  Such a message reads alone with `google_mail_get_message`, as the note says.
  (https://github.com/xippios/sdk-core/issues/175)
- A token endpoint answer other than `invalid_grant` and `invalid_client` is an error that
  names its status, the request and the reason phrase, `503 from POST /token: Service
  Unavailable`, and quotes nothing of the answer. Its message and its `body` quoted the
  answer's text before, which a token endpoint may write from the credentials it was sent.
  (https://github.com/xippios/sdk-core/issues/156)
- A tool's error answer sets `isError` on its result beside the text
  `error: <ErrorClass>: <message>`, an unknown account, a refused argument and a task's
  refusal alike, where the result was not flagged and a client read the text to tell it
  from data. (https://github.com/xippios/sdk-core/issues/168)
- The error of an undeclared account names the source `google-mail`, in the exception's
  `source` and a route's problem details, where it named `google`, the prefix of the
  variables, which stays `GOOGLE`. (https://github.com/xippios/sdk-core/issues/279)
- The account registry, the `--account` lock and its serve command, the renewing bearer,
  the write requests and the fixture writer are xippios-core 0.4.0's, where the package
  carried copies of its own. The private modules `_tenants`, `_account_lock` and `_cli` go
  with them, and core holds the read server to loopback, as it holds the write server.
  (https://github.com/xippios/sdk-google-mail/issues/19)
- Adopts sdk-template v0.3.4, from v0.3.2, with conventions 1.27 and no new core minor: the
  floor stays `xippios-core~=0.3.0`. The README lists the package's documents under
  `## Documents`, and `CLAUDE.md` holds the rules that `.claude/family.md` carried, which
  leaves with `/adopt-release`. Changelog entries are fragments under `changelog/`, the
  package's own decisions go into `docs/adr/` with the index that `scripts/adr_index.py`
  writes, and a pull request runs the organization's checks from `organization.yml`, where
  `gates.yml` was. (https://github.com/xippios/sdk-template/releases/tag/v0.3.4,
  https://github.com/xippios/sdk-google-mail/issues/16)
- Adopts sdk-template v0.3.2, from v0.3.0.
- `.copier-answers.yml` records sdk-template v0.3.2's answer `live_secrets` ahead of its
  adoption: `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` and `GOOGLE_DEMO_REFRESH_TOKEN`, the
  client pair and demo's refresh token that the nightly live tests read. The adoption then
  renders `live.yml` passing the three secrets from this answer, where a hand edit passed
  them; under sdk-template v0.4.0 the answer `tenant_credential_env` names demo's instead.

### Breaking

- `xippios.google.mail.mcp.build()` and `xippios.google.mail.mcp_write.build()` return
  core's `ToolSet`, the tools a host serves as the entry point does, and take core's
  `TenantLock`, where they returned the server and took the package's own `AccountLock`.
  A host that built a server from `build()` passes the tool set to
  `xippios.core.mcp.build_server`, binds it with `build(TenantLock(TENANTS, "<account>"))`,
  `TENANTS` of `xippios.google.mail._client`, and reads the variables the package needs
  from the modules' new `ENV_VARS`, while `mcp.server` and `mcp_write.server` are the
  servers as before. (https://github.com/xippios/sdk-core/issues/245,
  https://github.com/xippios/sdk-core/issues/166)
- A tool of either server refuses an argument it does not declare, before it runs, and
  names the ones it takes, where the argument was dropped and the tool ran with its
  defaults. So a server that `--account` binds refuses a call that still names `account`,
  where it answered it for the bound account, and a client drops the argument.
  (https://github.com/xippios/sdk-core/issues/84)
- The modules `xippios.google.mail.params` and `xippios.google.mail.enums` are gone, with
  the package's `reference/` directory. They held no list: `params.PACKAGED_LISTS` was
  empty and `enums` defined no enumeration, so a caller that imported either drops the
  import, and `LabelType` and the other value sets stay in `xippios.google.mail.models`.
  (https://github.com/xippios/sdk-google-mail/issues/58)
- Adopts sdk-template v0.4.0, from v0.3.4, with `core_version=0.4.0`,
  `live_secrets=[GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET]`,
  `rate_limit_provenance=below-documented`. The package runs on `xippios-core~=0.4.0`
  under conventions 1.28, and the four answers recorded ahead of the update,
  `write_server=true`, `tenant_param=account`,
  `tenant_credential_env=GOOGLE_<NAME>_REFRESH_TOKEN` and `has_reference_data=false`,
  render its two servers, their binding to one account and the live run's credentials,
  and drop the reference scaffold.
  (https://github.com/xippios/sdk-template/releases/tag/v0.4.0,
  https://github.com/xippios/sdk-google-mail/issues/59,
  https://github.com/xippios/sdk-google-mail/issues/52)

## [0.1.0] - 2026-10-29

First release, on xippios-core 0.3.0 from PyPI (`~=0.3.0`), sdk-template v0.3.0 and the
family's conventions 1.23. It wraps the Gmail API for several Gmail accounts at once. The
environment names the operator's Desktop-app OAuth client (`GOOGLE_CLIENT_ID`,
`GOOGLE_CLIENT_SECRET`), the accounts (`GOOGLE_ACCOUNTS`) and one refresh token per
account (`GOOGLE_<NAME>_REFRESH_TOKEN`); the client renews each account's access token
in memory and writes nothing to disk, and the consent tool `xippios-google-mail-auth`
gets an account's refresh token, for reading (`gmail.readonly`) or for writing as well
(`gmail.modify`). Seven read tasks name the accounts and read an account's profile, its
labels, a page of message ids, the messages a search finds, one message's decoded view
and a thread's; they are the tools of the read server, `xippios-google-mail-mcp-read`,
and routes of the FastAPI router. Five write tasks store and delete a draft, send a
message, change a message's labels and move a message to the trash; each previews and
changes nothing unless it is called with `confirm=True`, and they are the tools of a
server of their own, `xippios-google-mail-mcp-write`, never routes. Either server runs on
stdio by default and, over `--http`, serves streamable HTTP on a loopback host only unless
`--allow-remote` is passed, since neither carries authentication. The client keeps a
self-imposed pace of 60 requests a minute per account. The documentation snapshot of
2026-10-29 under `docs/upstream/`, Gmail's discovery document among it, is the reference
for names and semantics.

### Added

- Scaffold from [sdk-template](https://github.com/xippios/sdk-template): configured
  client for Gmail, empty task layer, MCP server and FastAPI router
  wired to it, test harness with core's family check, CI, the nightly live tests and
  the release train, in which a release PR's merge tags and publishes.
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
- Gmail's error answers mapped: a spent per-minute or daily quota is a `RateLimitError`
  (60 s, 3,600 s), a grant without the scope a call needs an `AuthError` naming the
  scopes granted and the consent to re-run, a domain policy or a refused access token an
  `AuthError`; each account's requests are paced at 60 a minute and held back for a
  minute after a spent quota.
- `list_accounts`, the declared accounts by name, and `get_profile(account)`, the
  account's address, message and thread counts and history id (`users.getProfile`).
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
- The read server's entry point is `xippios-google-mail-mcp-read`, as the family names
  the servers of a source with a write server; the write server has an entry point of
  its own. It is served over `--http` on a loopback `--host` only, unless
  `--allow-remote` is passed: it reaches every declared mailbox and carries no
  authentication.
- `--account <name>` binds either server, the read or the write one, to one account:
  every tool acts on it and takes no `account`.
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
  the labels; confirmed, it returns Gmail's answer, and `delete_draft`, which Gmail
  answers without a body, an acknowledgement of its own, `{"performed": true, "id"}`.
  They need an account consented with `--scopes write`, are re-exported from
  `xippios.google.mail` and are no HTTP routes; a result's request echo never carries
  the message's text.
- The fixture recorder records through a read task and scrubs a message or thread answer
  as JSON before it writes: headers cut to an allowlist on every part, Message-ID,
  In-Reply-To and References numbered `<n@example.com>`, display names in the address
  headers replaced by `User`, every body decoded, its addresses replaced and encoded
  again, attachment ids numbered, a profile-picture URL redacted and the raw message
  never kept; its final check reads the decoded bodies too.
- The fixture recorder records the write tools under a safety rule: the demo account
  writes to its own address only, read from its profile when it records, every message
  it writes carries `xippios-live` in its subject, and the raw message a write sends is
  dropped from the recorded request; the live tests follow the same rule and clean up
  what they write.
