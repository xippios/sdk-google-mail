"""Write server for Gmail.

``tasks_write.__all__`` as confirm-gated tools, behind an entry point of their own,
``xippios-google-mail-mcp-write``, so a client of the read server never sees a write tool
(conventions §8; ADR-0026: read and write never mix). Core registers them as a write
server (``read_only=False``): every task takes ``confirm: bool = False``, checked before
anything is registered, and ``is_write_server`` holds, so core's ``main`` serves
``--http`` on a loopback ``--host`` only, unless ``--allow-remote`` is passed (ADR-0014,
decision 9). ``--account <name>`` binds it to one account (``_cli``, ``_account_lock``);
the read server's own loopback check in ``_cli`` steps aside for a write server, since
core applies its rule. ``server.name`` is core's default, ``xippios-google-mail``, as the
read server's (conventions §8). No logic lives here (ADR-0001).
"""

from mcp.server.mcpserver import MCPServer
from xippios.core.mcp import build_server

from xippios.google.mail import tasks_write
from xippios.google.mail._account_lock import AccountLock, bind_tasks, instructions
from xippios.google.mail._cli import serve
from xippios.google.mail._client import SOURCE
from xippios.google.mail._version import __version__

INSTRUCTIONS = (
    "Gmail write tools via xippios. "
    "Every tool takes account, the name of a configured account, which the read server's "
    "google_mail_list_accounts lists. "
    "Every tool returns a preview and changes nothing unless confirm=true is passed. "
    "Call it once without confirm, show the preview, then confirm."
)
BOUND_INSTRUCTIONS = (
    "Gmail write tools via xippios. "
    "Every tool returns a preview and changes nothing unless confirm=true is passed. "
    "Call it once without confirm, show the preview, then confirm."
)
"""The text of a write server ``--account`` binds; the lock's sentence names the account."""


def build(lock: AccountLock | None = None) -> MCPServer:
    """The write server, bound to one account when ``lock`` is given."""
    return build_server(
        SOURCE,
        bind_tasks([getattr(tasks_write, name) for name in tasks_write.__all__], lock),
        version=__version__,
        instructions=instructions(INSTRUCTIONS, lock, bound=BOUND_INSTRUCTIONS),
        caps=getattr(tasks_write, "MCP_CAPS", None),
        read_only=False,
    )


server = build()


def run() -> None:
    """Run the write server: stdio by default, ``--http`` on loopback, ``--account`` binds it."""
    # _cli.serve calls core's main(server, add_options=add_account, apply_options=<binds the
    # server to the account --account names>); core's main keeps a write server on loopback.
    serve(server, build)
