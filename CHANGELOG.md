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
- `xippios-google-mail-auth --account <name> --scopes read|write`, the consent tool: a
  loopback redirect with PKCE that prints the account's refresh token once and stores
  nothing.
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
