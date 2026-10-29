"""MCP server for Gmail.

Every task in ``tasks.__all__`` is a read-only tool, but those ``tasks.BULK_TASKS``
names, which core skips: the server is passed ``tasks.__all__`` whole (ADR-0019). No
logic lives here (ADR-0001). Entry point: ``xippios-google-mail-mcp-read``, the read
server; ``--account <name>`` binds it to one account (``_cli``, ``_account_lock``), and
``--http`` serves it on a loopback ``--host`` only, unless ``--allow-remote`` is passed
(ADR-0025, D-a).
"""

from mcp.server.mcpserver import MCPServer
from xippios.core.mcp import build_server

from xippios.google.mail import tasks
from xippios.google.mail._account_lock import AccountLock, bind_tasks, instructions
from xippios.google.mail._cli import serve
from xippios.google.mail._client import SOURCE
from xippios.google.mail._version import __version__

INSTRUCTIONS = (
    "Gmail via xippios. "
    "Tools are read-only. google_mail_list_accounts lists the configured accounts; "
    "every other tool takes account, the name of one of them, and an unknown name is "
    "answered with the declared ones. "
    "Call the list tools (google_mail_list_*) first to find valid values, then query."
)


def build(lock: AccountLock | None = None) -> MCPServer:
    """The read-only server, bound to one account when ``lock`` is given."""
    return build_server(
        SOURCE,
        bind_tasks([getattr(tasks, name) for name in tasks.__all__], lock),
        version=__version__,
        instructions=instructions(INSTRUCTIONS, lock),
        caps=getattr(tasks, "MCP_CAPS", None),
    )


server = build()


def run() -> None:
    """Run the server: stdio by default, ``--http`` for streamable HTTP, ``--account`` binds it."""
    # Command-line options of the source's own reach core's parser through its hooks
    # (ADR-0014): _cli.serve calls main(server, add_options=add_account, apply_options=<checks
    # the loopback rule and returns the server, bound to the account --account names>).
    serve(server, build)
