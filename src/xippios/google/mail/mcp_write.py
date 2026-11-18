"""Write server for Gmail.

``tasks_write.__all__`` as confirm-gated tools, behind an entry point of their own,
``xippios-google-mail-mcp-write``, so a client of the read server never sees a write
tool (conventions §8). Core registers them as a write server (``read_only=False``): every task
takes ``confirm: bool = False``, checked before anything is registered, and ``--http``
serves on a loopback ``--host`` only, unless ``--allow-remote`` is passed, since it
carries no authentication. No logic lives here (sdk-core ADR-0001).
``--account <name>`` binds it to one account (core's ``serve_tenants`` and ``TenantLock``).
``server.name`` is core's default, ``xippios-google-mail``, as the read server's
(conventions §8).

The package contract (conventions §8; sdk-core ADR-0026, amendment 2026-11-18, points c and d):
``build()`` returns the write tool set, with the caps and the option hooks
``tasks_write.py`` defines, and ``server`` is built from it.
"""

from xippios.core import TenantLock, bind_tasks, tenant_instructions
from xippios.core.mcp import ToolSet, build_server, serve_tenants, tool_functions

from xippios.google.mail import tasks_write
from xippios.google.mail._client import ENV_VARS, SOURCE, TENANTS, check_account
from xippios.google.mail._version import __version__

__all__ = ["ENV_VARS", "SOURCE", "build", "run", "server"]

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
"""The text of a server ``lock`` binds, whose tools take no account: core's sentence names it."""


def build(lock: TenantLock | None = None) -> ToolSet:
    """The write tool set, bound to one account when ``lock`` is given."""
    return ToolSet(
        SOURCE,
        bind_tasks(tool_functions(tasks_write), lock),
        caps=getattr(tasks_write, "MCP_CAPS", None),
        instructions=tenant_instructions(INSTRUCTIONS, lock, bound=BOUND_INSTRUCTIONS),
        version=__version__,
        add_options=getattr(tasks_write, "mcp_add_options", None),
        apply_options=getattr(tasks_write, "mcp_apply_options", None),
        read_only=False,
    )


server = build_server(build())


def run() -> None:
    """Run the write server: stdio by default, ``--http`` on loopback.

    ``--account <name>`` binds it to one account.
    """
    serve_tenants(server, build, TENANTS, check=check_account)
