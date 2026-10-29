# sdk-google-mail — instructions for Claude Code

`xippios-google-mail` wraps Gmail as a typed Python client, an MCP server
and FastAPI routes, built on `xippios-core`. This file holds the facts of this package;
the family rules every source shares are in `.claude/family.md`, which the next line imports.

@.claude/family.md

## What this package wraps

| | |
| --- | --- |
| Upstream | Gmail, https://developers.google.com/workspace/gmail/api |
| API base URL | `https://gmail.googleapis.com/gmail/v1/users/me` |
| Documentation | Gmail's discovery document, https://gmail.googleapis.com/$discovery/rest?version=v1, which the REST reference https://developers.google.com/workspace/gmail/api/reference/rest names (`SPEC_URL` of `scripts/refresh_upstream.py`), kept as `docs/upstream/discovery.json` with its keys sorted; beside it, as Markdown under `docs/upstream/pages/`, the usage limits, the errors, the scopes, the Gmail Help article on search operators and Google's OAuth 2.0 overview and guides for desktop apps and web server applications (`/refresh-upstream`) |
| Credentials | an OAuth 2.0 bearer renewed from a refresh token per account: the operator's Desktop-app client `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET`, the account list `GOOGLE_ACCOUNTS` and one `GOOGLE_<NAME>_REFRESH_TOKEN` per account (`demo` declared by `GOOGLE_DEMO_REFRESH_TOKEN` alone, never listed), from the environment, the registry `TENANTS` in `_client.py`; `RefreshTokenAuth` (`_auth.py`, queue row 252) exchanges the refresh token at `POST https://oauth2.googleapis.com/token`, keeps the access token, its expiry and the granted scopes in memory and sends the access token as a bearer, renewing it within 60 s of its expiry and once more on a 401; nothing is written to disk; a refresh token comes from `xippios-google-mail-auth` (`_consent.py`), for the scope set `read` (`gmail.readonly`) or `write` (`gmail.modify`); the nightly `live.yml` passes the client pair and demo's token as org secrets |
| Rate limit | 60 requests per minute, burst 10, a self-imposed pace per account, enforced client-side in `_client.py` (`RATE_LIMIT`, one token bucket per account client); upstream documents a quota in units, 6,000 per minute per user per project and 1,200,000 per minute per project, with the units per method on the quota page, `docs/upstream/pages/quota.md` (`getProfile` 1; `messages.get` 20, 60 with `format=full`, its default; `threads.get` 40, 120 with `format=full`, its default), so 60 full `threads.get` in a minute (7,200 units) would exceed the per-user quota; the backstop is a `QuotaPolicy` per account (`account_config`, ADR-0010, ADR-0015; queue rows 143 and 299) that reads upstream's 403 `rateLimitExceeded` and `userRateLimitExceeded` (no quota header, no `Retry-After`, F8) and holds the account's requests back for 60 s, which `classify` maps to `RateLimitError` with `retry_after` 60 s, `dailyLimitExceeded` with 3,600 s |
| Write tools | no, this package is read-only |

The snapshot listed in `docs/upstream/FETCHED.md` is the reference for names, parameters and semantics.

## Layout

```
src/xippios/google/mail/
  __init__.py      public surface: __version__ and re-exported task functions
  py.typed         PEP 561 marker; consumers' mypy --strict needs it
  _version.py      __version__ from distribution metadata
  _client.py       the account registry TENANTS, client_for(account) (one AsyncClient per
                   account: its auth, pace and quota policy) and classify, upstream's
                   documented error shapes
  _auth.py         RefreshTokenAuth, the renewing bearer (queue row 252), and SCOPE_SETS
  _tenants.py      Tenants and list_task, copied from sdk-cashctrl (queue row 281)
  _account_lock.py the --account lock, AccountLock and bind_tasks, copied from
                   sdk-cashctrl's _org_lock.py (queue row 281)
  _cli.py          serve: --account through core's option hooks and the read server's
                   loopback rule (ADR-0025 D-a; queue rows 281, 282)
  _consent.py      xippios-google-mail-auth, the loopback PKCE consent that prints a
                   refresh token once
  errors.py        the package's own error classes, where conventions §5 calls for one
  models.py        pydantic models mirroring upstream payloads (UpstreamModel)
  api/             endpoint wrappers, one module per upstream operation
  tasks.py         task functions; __all__ is what the adapters register, but BULK_TASKS,
                   the Python-only ones; MCP_CAPS any per-tool result caps
  mcp.py           the read server's entry point, xippios-google-mail-mcp-read; build(lock)
  http.py          FastAPI router
  params.py        the packaged reference lists, read from reference/
  enums.py         StrEnums (IntEnums for integer ids) generated from the packaged lists;
                   never edited by hand
  reference/       packaged code lists (CSV/JSON) plus README with provenance
scripts/
  refresh_reference.py   fetches the packaged lists through api/ and regenerates enums.py
  refresh_upstream.py    captures docs/upstream/ and writes FETCHED.md (/refresh-upstream): the
                         discovery document, its keys sorted, and seven pages as Markdown
  record_fixture.py      records one exchange of the demo account into tests/fixtures/,
                         scrubbed and checked before the one write (/record-fixture)
tests/
  conftest.py      enables xippios.core.testing.plugin, which keeps the run offline, and
                   strips every account but demo at import (queue row 281)
  fixtures/        recorded responses, <endpoint>/<case>.json, never hand-written
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
  test_account_lock.py  the --account lock and the loopback rule
  test_consent.py  the consent tool, offline
  test_record_fixture.py  the recorder's scrub step and check, offline
  test_live.py     @pytest.mark.live smoke tests, one per task, against demo only
docs/upstream/     documentation snapshot and FETCHED.md
.github/workflows/ ci.yml (the required ci check, with the CHANGELOG and fixture-size steps),
                   publish.yml (the release train: tag, build, PyPI), live.yml (nightly live
                   tests), gates.yml (the merge gates as jobs, off until GATES_CALLER is 1)
                   siblings.yml (the sibling proof of ADR-0006: this wheel beside the
                   siblings under xippios.google, a visible check the ruleset does not require)
.github/scripts/   check-siblings.sh, which siblings.yml runs; the template's, as is the workflow
.claude/family.md  the family rules, the template's, imported above
.claude/commands/  add-endpoint, record-fixture, refresh-upstream, refresh-reference-data, release-prep,
                   adopt-release
```

The import path is `xippios.google.mail` (ADR-0006); `src/xippios/` is a namespace
package, and so is `src/xippios/google/`, the provider level.

## Commands

```bash
XIPPIOS_LIVE_TESTS=1 direnv exec ~/github/sdk uv run pytest -m live --tb=short   # demo only

uv run xippios-google-mail-mcp-read   # stdio; --http for streamable HTTP, on loopback
uv run xippios-google-mail-mcp-read --account demo   # bound to one account

xippios-google-mail-auth --account demo   # --scopes read by default; a consent: the operator's step

direnv exec ~/github/sdk uv run scripts/record_fixture.py get_profile ok   # demo only
```

`.github/workflows/live.yml` runs the live tests nightly at 02:37 UTC, a time derived from
the slug. It passes the three org secrets `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` and
`GOOGLE_DEMO_REFRESH_TOKEN` by hand, since the template renders `_API_KEY` secrets only
(queue row 255). Offline, core's plugin sets those three to its placeholder
(`xippios_placeholder_key_envs` in `pyproject.toml`), and every Gmail fixture replays the
refresh, `tests/fixtures/oauth2_token/ok.json`, before it. Demo's grant was consented with
the read scopes on 2026-10-29 under a consent screen in Testing status, whose grants live
seven days: a refresh answered `invalid_grant` means a new consent, by hand.

## Gmail specifics

### The credentials

`GOOGLE_CLIENT_SECRET`, every `GOOGLE_<NAME>_REFRESH_TOKEN` and any access token are never
read by value, printed, logged or written to a file or a commit, and `.envrc` is never
read; code reads the variables by name. They reach a process only through `direnv exec`
from the family workspace's root, whose `.envrc` holds them, or through the org secrets.
No process listing or environment dump (`env`, `printenv`, `set`, `export`, `ps`,
`/proc/*/environ`). A fixture is recorded through the recorder's scrub only, never by
hand: `scripts/record_fixture.py` records through `client_for("demo")` only, with core's
`Scrub(secrets=...)` naming demo's refresh token, the client secret and the client id,
then its own step (encoded forms, token fields by key, every address to
`user@example.com`), and checks for Google's token shapes (`ya29.`, `1//`, `GOCSPX-`,
`apps.googleusercontent.com`) before the one write. Grep every fixture for those shapes
and for addresses before committing it.

### Accounts, scopes and errors

Every task takes `account` first; `list_accounts` names the declared accounts, names only.
`--account <name>` binds the read server to one account (`_account_lock`), and `--http`
serves it on a loopback `--host` only unless `--allow-remote` is passed (ADR-0025 D-a): it
reaches every declared mailbox and carries no authentication. Upstream enforces a grant's
scopes: a call the grant does not cover is answered HTTP 403 `insufficientPermissions` with
`WWW-Authenticate: Bearer error="insufficient_scope", scope="..."` (recorded 2026-10-29,
F19; Gmail's error page says 401 `authError`), which `classify` maps to the `AuthError`
naming the granted scopes and the re-run with `--scopes write`. The token endpoint's 400
`invalid_grant` is the `AuthError` that names the re-run of `xippios-google-mail-auth`, its
401 `invalid_client` a `ConfigError` naming the client variables.
