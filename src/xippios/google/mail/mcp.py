"""MCP server for Gmail.

Every task in ``tasks.__all__`` is a read-only tool, but those ``tasks.BULK_TASKS``
names (``tool_functions``, sdk-core ADR-0040). No logic lives here (sdk-core ADR-0001).
Entry point: ``xippios-google-mail-mcp-read``, the read server; ``--account <name>`` binds
it to one account (core's ``serve_tenants`` and ``TenantLock``), and core holds it to
loopback (``loopback_only``): ``--http`` on a loopback ``--host`` only, unless
``--allow-remote`` is passed, since it reaches every declared mailbox and carries no
authentication (sdk-core ADR-0025, D-a). The write tools are ``mcp_write.py``'s, on a
server of their own.

The package contract (conventions §8; sdk-core ADR-0026, amendment 2026-11-18, points c and d):
``build()`` returns the tool set a host serves as this entry point does, with the caps
and the option hooks ``tasks.py`` defines, ``MCP_CAPS``, ``mcp_add_options`` and
``mcp_apply_options``; ``server`` is built from it, and ``ENV_VARS`` beside ``SOURCE``
names the variables the package reads.
"""

from xippios.core import TenantLock, bind_tasks, tenant_instructions
from xippios.core.mcp import ToolSet, build_server, serve_tenants, tool_functions

from xippios.google.mail import tasks
from xippios.google.mail._client import ENV_VARS, SOURCE, TENANTS, check_account
from xippios.google.mail._version import __version__

__all__ = ["ENV_VARS", "SOURCE", "build", "run", "server"]

INSTRUCTIONS = (
    "Gmail via xippios. "
    "Tools are read-only. google_mail_list_accounts lists the configured accounts; "
    "every other tool takes account, the name of one of them, and an unknown name is "
    "answered with the declared ones. "
    "Call the list tools (google_mail_list_*) first to find valid values, then query."
)
BOUND_INSTRUCTIONS = (
    "Gmail via xippios. "
    "Tools are read-only. "
    "Call the list tools (google_mail_list_*) first to find valid values, then query."
)
"""The text of a server ``lock`` binds, whose tools take no account: core's sentence names it."""


def build(lock: TenantLock | None = None) -> ToolSet:
    """The read-only tool set, bound to one account when ``lock`` is given."""
    return ToolSet(
        SOURCE,
        bind_tasks(tool_functions(tasks), lock),
        caps=getattr(tasks, "MCP_CAPS", None),
        instructions=tenant_instructions(INSTRUCTIONS, lock, bound=BOUND_INSTRUCTIONS),
        version=__version__,
        add_options=getattr(tasks, "mcp_add_options", None),
        apply_options=getattr(tasks, "mcp_apply_options", None),
        loopback_only=True,
    )


server = build_server(build())


def run() -> None:
    """Run the server: stdio by default, ``--http`` for streamable HTTP, on loopback.

    ``--account <name>`` binds it to one account.
    """
    serve_tenants(server, build, TENANTS, check=check_account)
