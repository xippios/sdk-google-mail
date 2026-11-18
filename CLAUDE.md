# sdk-google-mail — instructions for Claude Code

`xippios-google-mail` wraps Gmail as a typed Python client, an MCP server
and FastAPI routes, built on `xippios-core`. This file holds what an agent needs to work on
the package: what it wraps, its layout, its commands and its code rules. The documents it
follows, sdk-core's conventions among them, are listed under
[`## Documents` in the README](README.md#documents); read them before you change a file.

## What this package wraps

| | |
| --- | --- |
| Upstream | Gmail, https://developers.google.com/workspace/gmail/api |
| API base URL | `https://gmail.googleapis.com/gmail/v1/users/me` |
| Documentation | Gmail's discovery document, https://gmail.googleapis.com/$discovery/rest?version=v1, which the REST reference https://developers.google.com/workspace/gmail/api/reference/rest names (`SPEC_URL` of `scripts/refresh_upstream.py`), kept as `docs/upstream/discovery.json` with its keys sorted; beside it, as Markdown under `docs/upstream/pages/`, the usage limits, the errors, the scopes, the Gmail Help article on search operators and Google's OAuth 2.0 overview and guides for desktop apps and web server applications (`/refresh-upstream`) |
| Credentials | an OAuth 2.0 bearer renewed from a refresh token per account: the operator's Desktop-app client `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET`, the account list `GOOGLE_ACCOUNTS` and one `GOOGLE_<NAME>_REFRESH_TOKEN` per account (`demo` declared by `GOOGLE_DEMO_REFRESH_TOKEN` alone, never listed), from the environment, the registry `TENANTS` in `_client.py`, core's `Tenants`; `RefreshTokenAuth` (`_auth.py`), a subclass of core's `RenewingBearerAuth`, exchanges the refresh token at `POST https://oauth2.googleapis.com/token`, keeps the access token, its expiry and the granted scopes in memory and sends the access token as a bearer, renewing it within 60 s of its expiry and once more on a 401; nothing is written to disk; a refresh token comes from `xippios-google-mail-auth` (`_consent.py`), for the scope set `read` (`gmail.readonly`) or `write` (`gmail.modify`); the nightly `live.yml` passes the client pair and demo's token as org secrets |
| Rate limit | 60 requests per minute, burst 10, a self-imposed pace per account, enforced client-side in `_client.py` (`RATE_LIMIT`, one token bucket per account client); upstream documents a quota in units, 6,000 per minute per user per project and 1,200,000 per minute per project, with the units per method on the quota page, `docs/upstream/pages/quota.md` (`getProfile` 1; `messages.get` 20, 60 with `format=full`, its default; `threads.get` 40, 120 with `format=full`, its default), so 60 full `threads.get` in a minute (7,200 units) would exceed the per-user quota; the backstop is a `QuotaPolicy` per account (`account_config`, sdk-core ADR-0010, sdk-core ADR-0015; queue rows 143 and 299) that reads upstream's 403 `rateLimitExceeded` and `userRateLimitExceeded` (Gmail documents no quota header and no `Retry-After`) and holds the account's requests back for 60 s, which `classify` maps to `RateLimitError` with `retry_after` 60 s, `dailyLimitExceeded` with 3,600 s |
| Write tools | yes: `create_draft`, `delete_draft`, `send_message`, `modify_message` and `trash_message` in `tasks_write.py`, served by the write server's entry point `xippios-google-mail-mcp-write` (`mcp_write.py`, registered with `read_only=False`, so core keeps `--http` on loopback without `--allow-remote`), never by the read server or the HTTP router; each takes `account` first and `confirm: bool = False` last, and without `confirm=True` sends nothing to the write endpoint and returns a preview; they need the account's `write` grant (`gmail.modify`) |

The snapshot listed in `docs/upstream/FETCHED.md` is the reference for names, parameters and
semantics; a wrapper is never written from memory of the API.

## Layout

```
src/xippios/google/mail/
  __init__.py      public surface: __version__ and re-exported task functions
  py.typed         PEP 561 marker; consumers' mypy --strict needs it
  _version.py      __version__ from distribution metadata
  _client.py       the account registry TENANTS (core's Tenants), client_for(account) (one
                   AsyncClient per account: its auth, pace and quota policy),
                   check_account, the bound server's startup check, and classify,
                   upstream's documented error shapes
  _auth.py         RefreshTokenAuth, the renewing bearer, a subclass of core's
                   RenewingBearerAuth, and SCOPE_SETS
  _consent.py      xippios-google-mail-auth, the loopback PKCE consent that prints a
                   refresh token once
  _compose.py      the RFC 2822 text a write task sends (EmailMessage, 7-bit, CRLF; no
                   From), its base64url raw, the address and subject checks, a reply's
                   headers from the original (Reply)
  errors.py        the package's own error classes, where conventions §5 calls for one
  models.py        pydantic models mirroring upstream payloads (UpstreamModel): the
                   faithful models (Label, ListMessagesResponse, Message with its MIME
                   tree, Thread, Draft, ModifyMessageRequest, ...), the projections the
                   read tasks return (DecodedMessage and its Attachment, SearchRow) and
                   the write tasks' previews (ComposePreview, ActionPreview) and
                   DraftDeleted; by the family's precedent also an enumeration written
                   by hand, for a value set upstream documents and no packaged list
                   renders
  _decode.py       decode_message, the decoded view of a message's MIME tree
                   (sdk-core ADR-0025)
  api/             endpoint wrappers, one module per upstream operation (conventions §2),
                   named by its method id minus the shared gmail.users.: get_profile,
                   labels_list, messages_list, messages_get, threads_get and
                   messages_attachments_get, the last without a task in 0.1.0; the
                   writes drafts_create, drafts_delete, messages_send, messages_modify
                   and messages_trash
  tasks.py         task functions; __all__ is what the adapters register, but BULK_TASKS,
                   the Python-only ones; MCP_CAPS the per-tool result caps (the notes of
                   get_message and get_thread, the default bounds); SEARCH_MAX_RESULTS,
                   search_messages' bound
  tasks_write.py   the write tasks, each previewing unless confirm=True; __all__ is what
                   mcp_write.py registers, and the read server and the router none of it
  mcp.py           the read server's entry point, xippios-google-mail-mcp-read; build(lock),
                   its ToolSet, held to loopback (loopback_only), served by core's
                   serve_tenants
  mcp_write.py     the write server's entry point, xippios-google-mail-mcp-write; build(lock),
                   its ToolSet, served by core's serve_tenants
  http.py          FastAPI router
  params.py        the packaged reference lists, read from reference/
  enums.py         StrEnums (IntEnums for integer ids) generated from the packaged lists;
                   never edited by hand
  reference/       packaged code lists (CSV/JSON) plus README with provenance
scripts/
  refresh_reference.py   fetches the packaged lists through api/ and regenerates enums.py
  refresh_upstream.py    captures docs/upstream/ and writes FETCHED.md (/refresh-upstream): the
                         discovery document, its keys sorted, and seven pages as Markdown
  record_fixture.py      records one exchange of the demo account, or every exchange of
                         a task run for demo, into tests/fixtures/, a write task under
                         the write safety rule, scrubbed and checked before the one
                         write (/record-fixture)
  adr_index.py           writes docs/adr.md and docs/adr.json from the records in docs/adr/;
                         --check fails while either is stale
tests/
  conftest.py      enables xippios.core.testing.plugin, which keeps the run offline,
                   strips every account but demo at import (core's keep_reserved_tenants),
                   and holds a session-wide write audit hook (sys.addaudithook, off behind
                   a threading.Event) that watches open and the file-system events for
                   the tests that take its no_writes fixture
  fixtures/        recorded responses, never hand-written: <wrapper>/<case>.json, or
                   <task>/<case>.json for an exchange recorded through a task (conventions
                   §10), the k-th exchange of one recording <task>/<case>_<k>.json
  test_family.py   core's family check, one line: namespace levels, py.typed, __version__,
                   the adapters serving exactly the tasks, task shapes and docstrings, the
                   README status table, the template tag, the core bound
  test_api.py      optional: the endpoint wrappers' own tests
  test_tasks.py    per-task replay tests, and classify on the recorded error answers (the
                   401 authError, the 403 scope refusal; the token endpoint's
                   invalid_grant and invalid_client are test_auth.py's) and on answers
                   built from handle-errors.md's documented samples (the per-minute
                   and daily quota and domainPolicy, which demo cannot provoke)
  test_auth.py     RefreshTokenAuth: the refresh, the 401 retry, the refusals, no disk
  test_account_lock.py  the package's wiring of core's --account lock on both servers and
                   the two loopback rules
  test_consent.py  the consent tool, offline
  test_tasks_messages.py  the read tasks on labels, messages and threads: replays, MCP
                   calls, the search bound, the thread's slice and cut note, and the decoder's
                   rules on messages built in memory
  test_tasks_write.py  the write tasks: previews that send nothing, the refusals before
                   any request, the composer, replays and MCP calls of the confirmed
                   tools, the write server (core's assert_write_tools) and the live
                   tests' safety helper
  test_record_fixture.py  the recorder's scrub step and check, and its write safety rule,
                   offline
  test_live.py     @pytest.mark.live smoke tests, one per task, against demo only; the
                   write tasks in two sequences that write to demo itself and clean up
docs/
  upstream/        documentation snapshot and FETCHED.md
  adr/             this package's design decisions, one NNNN-<slug>.md record each, laid
                   out in its README.md
  adr.md           the index of the decisions, written by scripts/adr_index.py, never by hand
  adr.json         the same index, for machines
  reports/         dated reports, the evidence a decision cites, laid out in its README.md
changelog/         the next release's changelog entries, one fragment per change, laid out
                   in its README.md
CHANGELOG.md       the releases, each section assembled from the fragments
.github/workflows/ ci.yml (the required ci check, with the changelog and fixture-size steps),
                   publish.yml (the release train: tag, build, PyPI), live.yml (nightly live
                   tests), organization.yml (the organization's checks from xippios/.github:
                   conventions, a review of a pull request against the documents the README
                   lists, and adr-index, which runs scripts/adr_index.py --check),
                   siblings.yml (the sibling proof of sdk-core ADR-0006: this wheel beside the
                   siblings under xippios.google, a visible check the ruleset does not require)
.github/scripts/   check-siblings.sh, which siblings.yml runs; the template's, as is the workflow
.claude/commands/  add-endpoint, record-fixture, refresh-upstream, refresh-reference-data, release-prep
```

The import path is `xippios.google.mail` (sdk-core ADR-0006); `src/xippios/` is a
namespace package, and so is `src/xippios/google/`, the provider level.
Where the API's operation ids repeat across its services, `api/` keeps one module per service
instead, named after the service, and this Layout says so (conventions §2).

## Commands

```bash
uv sync --all-extras
uv run pytest                      # offline, replays the fixtures
uv run ruff check . && uv run ruff format --check .
uv run mypy
uv run pre-commit run --all-files
python3 scripts/adr_index.py --check   # the index matches the records in docs/adr/
uv run pytest -m live --tb=short --assert=plain   # needs XIPPIOS_LIVE_TESTS=1, GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, GOOGLE_DEMO_REFRESH_TOKEN; demo only

uv run xippios-google-mail-mcp-read   # stdio; --http for streamable HTTP, on loopback
uv run xippios-google-mail-mcp-read --account demo   # bound to one account
uv run xippios-google-mail-mcp-write --account demo  # the write tools; --http on loopback

xippios-google-mail-auth --account demo   # --scopes read by default; a consent: the operator's step
xippios-google-mail-auth --account demo --scopes write   # gmail.modify, for the write tools

direnv exec ~/github/sdk uv run scripts/record_fixture.py get_profile ok   # demo only
direnv exec ~/github/sdk uv run scripts/record_fixture.py search_messages ok \
  q=newer_than:1y max_results=2   # through the task: ok.json, ok_2.json, ok_3.json
direnv exec ~/github/sdk uv run scripts/record_fixture.py send_message ok \
  'subject=xippios-live send' body=hi confirm=true   # a write task, to demo itself
```

`uv run pytest` is offline: core's plugin refuses the network and skips the rate limiter's
waits in every test not marked `live` (conventions §10, sdk-core ADR-0013). A test that
asserts a limiter wait or a quota pause is marked `rate_limited`, one that must reach the
network `network`.

`.github/workflows/live.yml` runs the live tests nightly at 02:37 UTC, a time derived from
the slug, and on demand; a pull request never runs them. Only its
`uv run pytest -m live --tb=short --assert=plain` step sees `GOOGLE_CLIENT_ID`,
`GOOGLE_CLIENT_SECRET` and `GOOGLE_DEMO_REFRESH_TOKEN`, each from the secret of the same
name, as the `live_secrets` answer in `.copier-answers.yml` names them. Every live run
takes `--tb=short --assert=plain`, since the log of `live.yml` is public: the first keeps a
failing test's frame arguments out of it, a raw upstream payload among them, and the second
the values a failed `assert` compares. A live test asserts on shapes and counts, never on a
raw payload.

Offline, core's plugin sets those three to its placeholder
(`xippios_placeholder_key_envs` in `pyproject.toml`), and every Gmail fixture replays the
refresh, `tests/fixtures/oauth2_token/ok.json`, before it, a write fixture
`oauth2_token/write.json`, the refresh under the write grant. Demo holds the write set,
`gmail.modify`, which serves the reads too and which the write tasks' fixtures and live
tests need: consented with the read scopes on 2026-10-29 and, that grant revoked, with
`--scopes write` the same day, under a consent screen in Testing status, whose grants
live seven days: a refresh answered `invalid_grant` means a new consent, by hand.

## Code rules

- Three layers (sdk-core ADR-0001, conventions §2): endpoint wrappers in `api/`, task
  functions in `tasks.py`, adapters in `mcp.py` and `http.py`. The adapters register
  `tasks.__all__` and call only tasks, the tasks call only wrappers and reference data, and
  a wrapper returns models.
- Upstream's documented error shapes go into `classify` in `_client.py`, once, and an error
  class of the package's own into `errors.py`; a wrapper never inspects a status code
  (conventions §5, §6).
- A change brings its tests, written before the code, and passes the offline checks above;
  its changelog entry is a fragment under `changelog/`, one file holding its bullet, as
  `changelog/README.md` lays it out (conventions §12). Commit subjects follow Conventional
  Commits (conventions §13): the rule binds a pull request's own commits, not the commit
  that lands the pull request on `main`, whose subject GitHub writes.
- `ruff` leaves `docs/` and `.claude/` alone (`pyproject.toml`), so the upstream snapshot
  keeps its bytes: never reformat it.
- A convention that seems wrong is followed anyway and said in the pull request; changing
  it is a change of sdk-core's `docs/conventions.md` with an ADR.

## Never

- Never hand-write a fixture, and never commit one without grepping it for key patterns
  (conventions §10).
- Never read or print `.envrc`, `.env`, the credential variables of this package,
  `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `GOOGLE_DEMO_REFRESH_TOKEN` and every other
  `GOOGLE_<NAME>_REFRESH_TOKEN`, or any variable ending in `_API_KEY`, `_REFRESH_TOKEN` or
  `_SECRET`.
- Never write a key, a token or another credential into a file or a commit.
- Never put logic in `mcp.py` or `http.py`, and never add a route or a tool by hand: the
  adapters register `tasks.__all__` (sdk-core ADR-0001).
- Never rename or re-map an upstream concept to another source's (sdk-core ADR-0003).
- Never return a documented personal identifier in a task's projection, echo one in
  `meta.request`, or commit one in a fixture (sdk-core ADR-0017).
- Never edit `docs/upstream/` by hand: `/refresh-upstream` fetches it, and the pull request
  shows its diff (conventions §3).
- Never add `src/xippios/__init__.py`, nor an `__init__.py` at a level between it and the
  package (sdk-core ADR-0006).
- Never push a tag by hand: a release's tag comes from `publish.yml` alone (conventions §12).

## Template updates

This repository is generated from sdk-template, at the tag `.copier-answers.yml` records. A
template release reaches it in a pull request of its own, with
`uvx --from 'copier>=9.19' copier update --defaults --vcs-ref=<tag>` (conventions §15): the
update renders the template's files again and never overwrites the files this package
fills, `__init__.py`, `_client.py`, `errors.py`, `models.py`, `api/`, `parse/`, `tasks.py`,
`params.py`, `enums.py`, `reference/`, `scripts/refresh_reference.py`,
`scripts/refresh_upstream.py`, `tests/test_tasks.py`, `tests/test_live.py`,
`tests/fixtures/`, `docs/upstream/`, `docs/adr.md`, `docs/adr.json`, `README.md`,
`CHANGELOG.md` and `CLAUDE.md`. sdk-template's
[CHANGELOG](https://github.com/xippios/sdk-template/blob/main/CHANGELOG.md) says, per
release, what to port into those by hand in the same pull request.

## When something is unclear

Prefer the smallest change that satisfies the documents the README lists, state the
assumption in the pull request's description, and stop rather than guess at upstream
semantics or the shape of the public API.

## Gmail specifics

### The credentials

`GOOGLE_CLIENT_SECRET`, every `GOOGLE_<NAME>_REFRESH_TOKEN` and any access token are never
read by value, printed, logged or written to a file or a commit, and `.envrc` is never
read; code reads the variables by name. They reach a process only through `direnv exec`
from the family workspace's root, whose `.envrc` holds them, or through the org secrets.
No process listing or environment dump (`env`, `printenv`, `set`, `export`, `ps`,
`/proc/*/environ`). A fixture is recorded through the recorder's scrub only, never by
hand: `scripts/record_fixture.py` records through `client_for("demo")` only, with core's
`Scrub(secrets=..., response_secrets=...)` naming demo's refresh token, the client secret
and the client id, and reading the tokens of a refresh answer, then its own step (encoded forms, token fields by key, every address to
`user@example.com`), and checks for Google's token shapes (`ya29.`, `1//`, `GOCSPX-`,
`apps.googleusercontent.com`) before the one write. A message or thread answer is read as
JSON first (`MessageScrub`, the list of the Gmail investigation that sdk-core ADR-0025
rests on):
every part's headers cut to From, To, Cc, Bcc, Date, Subject, Message-ID, In-Reply-To,
References, Content-Type and MIME-Version, so `Received`, `ARC-*`, `DKIM-Signature` and
`X-Google-*` go; each id of a
Message-ID, In-Reply-To or References value numbered `<n@example.com>` within the
fixture; in From, To, Cc, Bcc, Reply-To and Sender, on every part, each `Name <addr>` or
`"Name" <addr>` made `User <user@example.com>`, a bare address `user@example.com`, since
a display name identifies its person as the address does (the four fixtures recorded
before the rule, `get_message/ok`, `get_thread/ok`, `search_messages/ok_2` and
`search_messages/ok_3`, keep the sender name "Google"); every `body.data` decoded, its
addresses scrubbed and encoded again, `size` as served; `attachmentId` to `att-<n>` in
the body and the path; a profile-picture URL (`lh3.googleusercontent.com/a/...`, shown
beside the account a security notice names) to `.../a/REDACTED`; `raw` dropped, from a
write's JSON request body as from an answer; message and thread ids kept, since replay
matches the path. A task's name, read or write, records through the task, each Gmail
exchange under the task's name (`ok.json`, then `ok_2.json`, ...).
Grep every fixture, its decoded `body.data` included, for those shapes and for addresses
before committing it. detect-secrets reads a `body.data` as a Base64 High Entropy String:
a fixture that carries one needs an audited `.secrets.baseline` entry.

### The decoded view

`get_message` makes one `messages.get` call with `format=full` and returns a
`DecodedMessage`, the decoded view of sdk-core ADR-0025 ("The decoded view"), built by
`_decode.decode_message`: decoding the MIME tree is parsing a value, conventions §3's one
permitted override, and the view picks the parts by MIME role; the faithful tree stays on
the wrapper's `Message.payload`. `headers` keeps the top-level part's From, To, Cc, Bcc,
Date, Subject, Message-ID, In-Reply-To and References, compared without regard to case,
in upstream's order, name and value as given; `text` is the first `text/plain` part that
is no attachment, `html` the first `text/html` one, on request only (`include_html`);
a body is base64url decoded and read in its `Content-Type` charset, else UTF-8, with
U+FFFD for what that cannot read. A part with a `filename` or a `body.attachmentId` is an
attachment: `attachments` lists its `part_id`, `filename`, `mime_type`, `size` and
`attachment_id`, never its content, and the walk does not descend into it. Over the result cap a
message, no list, comes back as the result's first 64 KB and a note of the tool's own
(`MCP_CAPS["get_message"]`): its text or html is cut, `include_html=False` gives the text
alone, and no `start` resumes it (sdk-core ADR-0025, sdk-core ADR-0016). `get_thread`
returns the views of a thread's messages, text only, from `start`, at most `limit`, and
records `start` in `meta.request`, so core's cut note ends `; resume with start=<start +
shown>` (conventions §8). `search_messages` reads at most 50 messages (`SEARCH_MAX_RESULTS`,
in the task for every caller, sdk-core ADR-0001): a list and one `format=metadata` call per
id, 5 + 20 units each, 1,005 for 50. No task reports `meta.total`: Gmail's
`resultSizeEstimate` is an estimate, kept as a data field of `list_messages` (sdk-core
ADR-0012).

### The write tools and the write safety rule

The five write tasks (`tasks_write.py`) take `account` first and `confirm: bool = False`
last; without `confirm=True` (from Python anything else) a task sends nothing to the
write endpoint and returns a preview: a `ComposePreview` (the message's To, Cc, Bcc,
Subject, In-Reply-To and References as rendered, the body's first 10 lines and its line
count, the `threadId`) or an `ActionPreview` (the id, the labels); a reply's preview reads
the original's metadata once (20 units). Confirmed, a task returns upstream's answer:
the `Draft`, the `Message` (ids and labels), or `DraftDeleted` for `drafts.delete`,
which answers no body. `_compose` builds the RFC 2822 text with `EmailMessage` under
`email.policy.SMTP` held to seven bits: no From (Gmail sets it, with Date and
Message-ID), Bcc kept in the text (`messages.send` reads the recipients from To, Cc and
Bcc), `raw` base64url without padding. A reply sets `threadId`, In-Reply-To (the
original's Message-ID), References (its References and Message-ID) and, for an empty
subject, `Re: <original>`, the discovery document's rule for joining a thread.
`meta.request` holds the parameters and the `threadId`, never the `body` (sdk-core
ADR-0017).

The write safety rule binds the recorder and the live tests: demo writes to itself only.
Its own address comes from `users.getProfile` at runtime (1 unit), never from a file, a
constant or a variable; a To, Cc or Bcc other than that address is refused before any
write (`self_only` in the recorder, which takes no `to`, `cc` or `bcc` at all,
`self_addressed` in the live tests); every message written carries `xippios-live` in its
Subject, and the recorder modifies or trashes only a message whose Subject carries it. A
live sequence sweeps marked leftovers first (`search_messages`, then
`messages.trash`; drafts stay out, since no wrapper lists them by draft id), creates,
reads back with `get_message`, then deletes the draft or trashes the message through the
task, and in a `finally` through the wrapper. A write is irreversible even on the
throwaway mailbox: nothing else is written.

### Accounts, scopes and errors

Every task takes `account` first; `list_accounts` names the declared accounts, names only.
`--account <name>` binds either server, the read or the write one, to one account
(core's `TenantLock`, through `serve_tenants`). `--http` serves the read server on a loopback
`--host` only unless `--allow-remote` is passed (sdk-core ADR-0025 D-a), since it reaches
every declared mailbox and carries no authentication, and core's `main` holds the write
server to the same rule (sdk-core ADR-0014, decision 9). Upstream enforces a grant's
scopes: a call the grant does not cover is answered HTTP 403 `insufficientPermissions` with
`WWW-Authenticate: Bearer error="insufficient_scope", scope="..."` (recorded 2026-10-29;
Gmail's error page says 401 `authError`), which `classify` maps to the `AuthError`
naming the granted scopes and the re-run with `--scopes write`. The token endpoint's 400
`invalid_grant` is the `AuthError` that names the re-run of `xippios-google-mail-auth`, its
401 `invalid_client` a `ConfigError` naming the client variables.
