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
