# Queue row 281: a copy of sdk-cashctrl's kit, which returns to xippios-core in 0.4.0 with
# its two consumers (ADR-0023, decision 4; ADR-0025, D-d); this package adopts it at 0.2.0.
"""``--account`` for the MCP server, and the read server's loopback rule, through core's hooks.

Copied from sdk-cashctrl's ``src/xippios/cashctrl/_cli.py`` at
``6db155b9f80e20b17b1533db66134b9eb2144c46`` (``main``, release 0.3.0), with ``account``
for ``org`` (``add_account``, ``locked``, ``serve``; ADR-0014, decision 8), and one rule
added, ADR-0025's D-a.

Core's ``main`` parses the command line once, with ``--account`` added by ``add_account``.
Given ``--account <name>``, ``locked`` checks the account as ``client_for`` would, declared
(``TENANTS.require``), and that its refresh token variable is set, and returns the server
built anew with ``AccountLock("account", name)``: every tool is bound to that account and
takes no ``account``, the account list lists it alone, and the instructions gain a
sentence naming it (``_account_lock``). An undeclared name or an unset refresh token stops
the process at startup, exit status 2, the message on stderr, nothing served; the token is
read on the first request. Without ``--account`` the server is served as built, for every
account its environment declares.

The read server reaches every declared mailbox and carries no authentication, so it is
held to loopback as core holds a write server (ADR-0025, D-a): ``--http`` on a ``--host``
other than ``127.0.0.1``, ``localhost`` or ``::1`` without ``--allow-remote`` is refused
with ``ConfigError`` before anything is built, exit status 2. A write server
(``mcp_write``, ``is_write_server``) is left to core's ``main``, which applies its own
rule to it, with its own message: for the write server ``locked`` binds ``--account``
alone. Core's ``build_server`` and ``main`` keep httpx's request lines out of the log
(``quiet_request_logging``). The HTTP router has no lock: a deployment that needs one
declares only that account.
"""

import argparse
from collections.abc import Callable, Sequence

from mcp.server.mcpserver import MCPServer
from xippios.core import ConfigError
from xippios.core.mcp import main
from xippios.core.mcp.server import is_write_server

from xippios.google.mail._account_lock import AccountLock
from xippios.google.mail._auth import CONSENT_COMMAND
from xippios.google.mail._client import SOURCE, TENANTS
from xippios.google.mail._tenants import _is_set

__all__ = ["ACCOUNT_HELP", "REMOTE_REFUSAL", "add_account", "check_loopback", "serve"]

ACCOUNT_HELP = "serve only this account, e.g. demo; every tool acts on it and takes no account"

# Queue row 282: a private copy of core's _LOOPBACK_HOSTS (xippios.core.mcp.cli, core
# 0.3.0), the mcp SDK's own list; core's build_server(..., loopback_only=True) replaces it
# in 0.4.0, with the host xippios-mcp as its second consumer (ADR-0025, D-a).
_LOOPBACK_HOSTS = ("127.0.0.1", "localhost", "::1")

REMOTE_REFUSAL = (
    "the read server of xippios-google-mail reaches mailboxes and carries no authentication; "
    "serve it on a loopback --host or pass --allow-remote behind a gateway"
)
"""ADR-0025, D-a: the refusal of ``--http`` on a non-loopback ``--host``."""

Build = Callable[[AccountLock | None], MCPServer]
"""Builds an adapter's server, bound to one account or, given ``None``, not."""


def add_account(parser: argparse.ArgumentParser) -> None:
    """Put ``--account`` on core's parser, so ``--help`` lists it and a typo fails as usual."""
    parser.add_argument("--account", help=ACCOUNT_HELP)


def check_loopback(namespace: argparse.Namespace, server: MCPServer) -> None:
    """Refuse the read server over ``--http`` beyond loopback without ``--allow-remote``.

    Raises:
        ConfigError: ``--http`` with a ``--host`` other than ``127.0.0.1``, ``localhost``
            or ``::1`` and no ``--allow-remote``, for a server that is not a write server
            (core's ``main`` holds those to loopback itself); ``REMOTE_REFUSAL``.
    """
    if is_write_server(server):
        return
    if namespace.http and namespace.host not in _LOOPBACK_HOSTS and not namespace.allow_remote:
        raise ConfigError(REMOTE_REFUSAL, source=SOURCE)


def serve(server: MCPServer, build: Build, argv: Sequence[str] | None = None) -> None:
    """Run ``server``, or the one ``build`` locks to ``--account``, through core's ``main``.

    Args:
        server: The adapter's unlocked server, built by ``build(None)``.
        build: Builds the adapter's server; called with the lock when ``--account`` is given.
        argv: Arguments without the program name; defaults to ``sys.argv[1:]``.
    """

    def locked(namespace: argparse.Namespace, unlocked: MCPServer) -> MCPServer:
        check_loopback(namespace, unlocked)
        if namespace.account is None:
            return unlocked
        account = TENANTS.require(namespace.account)
        variable = TENANTS.var(account, "REFRESH_TOKEN")
        if not _is_set(variable):
            raise ConfigError(
                f"{variable} is not set; export the refresh token "
                f"{CONSENT_COMMAND} --account {account} printed",
                source=SOURCE,
            )
        return build(AccountLock("account", account))

    main(server, argv, add_options=add_account, apply_options=locked)
