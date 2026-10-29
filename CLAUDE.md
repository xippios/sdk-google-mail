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
| Credentials | from PR 3 on, an OAuth 2.0 bearer renewed from a refresh token per account: the operator's Desktop-app client `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET`, the account list `GOOGLE_ACCOUNTS` and one `GOOGLE_<NAME>_REFRESH_TOKEN` per account (`demo` declared by `GOOGLE_DEMO_REFRESH_TOKEN` alone, never listed), from the environment; the package exchanges the refresh token at `POST https://oauth2.googleapis.com/token` and sends the access token as a bearer (queue row 252); a refresh token comes from `xippios-google-mail-auth`; the nightly `live.yml` passes the client pair and demo's token as org secrets |
| Rate limit | 60 requests per minute, burst 10, a self-imposed pace (per account from PR 3 on), enforced client-side in `_client.py`; upstream documents a quota in units, 6,000 per minute per user per project and 1,200,000 per minute per project, with the units per method on the quota page, `docs/upstream/pages/quota.md` (`messages.get` 20, 60 with `format=full`, its default; `threads.get` 40, 120 with `format=full`, its default), so 60 full `threads.get` in a minute (7,200 units) would exceed the per-user quota; the QuotaPolicy on the 403 quota reasons comes with PR 3 (ADR-0010, ADR-0015) |
| Write tools | no, this package is read-only |

The snapshot listed in `docs/upstream/FETCHED.md` is the reference for names, parameters and semantics.

## Layout

```
src/xippios/google/mail/
  __init__.py      public surface: __version__ and re-exported task functions
  py.typed         PEP 561 marker; consumers' mypy --strict needs it
  _version.py      __version__ from distribution metadata
  _client.py       the one configured AsyncClient (base URL, rate limit) and classify,
                   upstream's documented error shapes
  errors.py        the package's own error classes, where conventions §5 calls for one
  models.py        pydantic models mirroring upstream payloads (UpstreamModel)
  api/             endpoint wrappers, one module per upstream operation
  tasks.py         task functions; __all__ is what the adapters register, but BULK_TASKS,
                   the Python-only ones; MCP_CAPS any per-tool result caps
  mcp.py           the read server's entry point, xippios-google-mail-mcp-read
  http.py          FastAPI router
  params.py        the packaged reference lists, read from reference/
  enums.py         StrEnums (IntEnums for integer ids) generated from the packaged lists;
                   never edited by hand
  reference/       packaged code lists (CSV/JSON) plus README with provenance
scripts/
  refresh_reference.py   fetches the packaged lists through api/ and regenerates enums.py
  refresh_upstream.py    captures docs/upstream/ and writes FETCHED.md (/refresh-upstream): the
                         discovery document, its keys sorted, and seven pages as Markdown
tests/
  conftest.py      enables xippios.core.testing.plugin, which keeps the run offline
  fixtures/        recorded responses, <endpoint>/<case>.json, never hand-written
  test_family.py   core's family check, one line: namespace levels, py.typed, __version__,
                   the adapters serving exactly the tasks, task shapes and docstrings, the
                   README status table, the template tag, the core bound
  test_api.py      optional: the endpoint wrappers' own tests
  test_tasks.py    per-task replay tests
  test_live.py     @pytest.mark.live smoke tests, one per task
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
uv run pytest -m live --tb=short   # needs XIPPIOS_LIVE_TESTS=1

uv run xippios-google-mail-mcp-read   # stdio; add --http for streamable HTTP
```

`.github/workflows/live.yml` runs the live tests nightly at 02:37 UTC, a time derived from
the slug.
From PR 3 on it passes the three org secrets `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` and
`GOOGLE_DEMO_REFRESH_TOKEN` by hand, since the template renders `_API_KEY` secrets only
(queue row 255); until then it uses no secret.

## Gmail specifics

### The credentials

`GOOGLE_CLIENT_SECRET`, every `GOOGLE_<NAME>_REFRESH_TOKEN` and any access token are never
read by value, printed, logged or written to a file or a commit, and `.envrc` is never
read; code reads the variables by name. They reach a process only through `direnv exec`
from the family workspace's root, whose `.envrc` holds them, or through the org secrets.
No process listing or environment dump (`env`, `printenv`, `set`, `export`, `ps`,
`/proc/*/environ`). A fixture is recorded through the recorder's scrub only, never by
hand; the recorder, `scripts/record_fixture.py`, comes with PR 3.
