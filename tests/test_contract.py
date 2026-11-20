"""The package contract and what the adapters of xippios-core 0.4.0 answer a caller.

Offline, in process: the package's modules, both MCP servers through an in-process
``mcp.client.Client`` and the router through FastAPI's ``TestClient``.
"""

import importlib.util
from pathlib import Path

import xippios.google.mail


def test_the_package_ships_no_reference_scaffold() -> None:
    """The package packages no list (``has_reference_data`` is false), so it has no
    ``params`` or ``enums`` module and no ``reference/`` directory."""
    for module in ("params", "enums"):
        assert importlib.util.find_spec(f"xippios.google.mail.{module}") is None, module
    (package,) = xippios.google.mail.__path__
    assert not (Path(package) / "reference").exists()
