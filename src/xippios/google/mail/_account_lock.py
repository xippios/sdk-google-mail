# Queue row 281: a copy of sdk-cashctrl's kit, which returns to xippios-core in 0.4.0 with
# its two consumers (ADR-0023, decision 4; ADR-0025, D-d); this package adopts it at 0.2.0.
# One change: instructions() takes the bound server's own base text, since the copy kept
# the unbound text ("every other tool takes account") ahead of the lock sentence.
"""The ``--account`` lock: tools bound to one account, so no tool takes ``account``.

Copied from sdk-cashctrl's ``src/xippios/cashctrl/_org_lock.py`` at
``6db155b9f80e20b17b1533db66134b9eb2144c46`` (``main``, release 0.3.0), its ``--org`` lock,
with ``account`` for ``org`` and ``AccountLock`` for ``OrgLock``, and ``instructions``
taking ``bound=``, the bound server's own base text (ADR-0024; ADR-0025, D-d). The lock
is no middleware but a wrapper: given ``--account <name>``, ``bind_tasks`` hands
``build_server`` tools on which ``account`` is bound when the server is built. Each wrapper keeps its task's ``__name__``, so tool names and
``MCP_CAPS`` keys stay as they are, and its docstring without the ``account`` line under
``Args:``; its signature and annotations leave ``account`` out, so no tool's input schema
declares it, and it calls the task with ``account=<name>``. The account list of a bound
server lists that account only (``_only_the_locked_tenant``), still with ``meta.tenant``
``None``. The server's instructions are its bound text, which says nothing of an
``account`` argument, followed by one sentence naming the account (``lock_sentence``).

sdk-cashctrl kept from sdk-core's ``cb8ecdf379c04522e5f1dcd91ea5e8bdfa1fb3ff``
(``src/xippios/core/mcp/server.py``): ``TenantLock``'s validation of the parameter and
the tenant, as ``OrgLock``, here ``AccountLock``; ``_only_the_locked_tenant``; and
``_lock_sentence``, reworded for a bound parameter. Private to this package: ``_cli``
validates the account at startup and ``mcp.py`` calls ``bind_tasks`` and
``instructions``. The HTTP router has no lock: a deployment that needs one declares only
that account.
"""

import asyncio
import functools
import inspect
import re
import typing
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any

from xippios.core import Result

from xippios.google.mail._tenants import _LISTS_TENANTS

__all__ = ["AccountLock", "bind", "bind_tasks", "instructions", "lock_sentence"]


@dataclass(frozen=True, slots=True)
class AccountLock:
    """Bind a server to one account: every tool acts on it and none takes ``param``.

    Attributes:
        param: The task parameter that names the account, ``"account"``.
        value: The account the server serves, e.g. ``"demo"``; checked at startup by
            ``_cli``, declared.

    Raises:
        ValueError: ``param`` is not a parameter name or ``value`` is empty.
    """

    param: str
    value: str

    def __post_init__(self) -> None:
        if not self.param.isidentifier():
            raise ValueError("param must be a parameter name such as 'account'")
        if not self.value:
            raise ValueError("value must name the tenant the server serves")


def lock_sentence(lock: AccountLock) -> str:
    """The sentence a bound server's instructions end with."""
    return (
        f"This server is bound to {lock.param} {lock.value}; every tool acts on that "
        f"account and takes no {lock.param}."
    )


def instructions(
    base: str | None, lock: AccountLock | None, *, bound: str | None = None
) -> str | None:
    """A server's instructions: ``base`` unbound; bound, ``bound`` and ``lock_sentence``.

    Args:
        base: The unbound server's text, which may tell an agent that every tool takes
            ``account``.
        lock: The account the server is bound to, or ``None``.
        bound: The bound server's text, which must not say that a tool takes
            ``account``; ``None`` uses ``base``, as sdk-cashctrl's copy does.
    """
    if lock is None:
        return base
    return " ".join(filter(None, [base if bound is None else bound, lock_sentence(lock)]))


def _only_the_locked_tenant(lock: AccountLock, result: Any) -> Any:
    """A tenant list narrowed to the tenant ``lock`` binds; anything else unchanged."""
    if not isinstance(result, Result) or not isinstance(result.data, list):
        return result
    return result.model_copy(update={"data": [name for name in result.data if name == lock.value]})


def _without_arg(doc: str, param: str) -> str:
    """``doc`` without ``param``'s entry under ``Args:``, and without an ``Args:`` left empty.

    ``doc`` is cleaned as ``inspect.getdoc`` cleans it. The entry is its first line,
    ``    <param>: ...``, and every line after it indented deeper, its continuation.
    """
    entry = re.compile(rf"\s+{re.escape(param)}:")
    kept: list[str] = []
    in_args = False
    skipping: int | None = None
    for line in doc.splitlines():
        indent = len(line) - len(line.lstrip())
        if skipping is not None and line.strip() and indent > skipping:
            continue
        skipping = None
        if line.strip() == "Args:":
            in_args = True
        elif line.strip() and indent == 0:
            in_args = False
        if in_args and entry.match(line):
            skipping = indent
            continue
        kept.append(line)
    lines: list[str] = []
    for index, line in enumerate(kept):
        following = kept[index + 1] if index + 1 < len(kept) else ""
        if line == "Args:" and not following.startswith(" "):
            continue  # the parameter was its only entry
        lines.append(line)
    return "\n".join(lines).rstrip()


def bind(fn: Callable[..., Any], lock: AccountLock) -> Callable[..., Any]:
    """``fn`` as the tool of a server bound to ``lock.value``.

    A task that takes ``lock.param`` becomes an async function of its other parameters
    that awaits the task with ``param=value``: the same ``__name__``, the docstring
    without the parameter's line under ``Args:``, and a signature and annotations
    without it. The account list (``list_task``) lists the bound account only. Any
    other task is returned as it is.

    Args:
        fn: A task, a ``sync`` facade or a plain function, e.g. ``tasks.get_profile``.
        lock: The account the server is bound to, e.g. ``AccountLock("account", "demo")``.
    """
    if getattr(fn, _LISTS_TENANTS, False) is True:

        @functools.wraps(fn)
        def only_the_locked_tenant() -> Any:
            return _only_the_locked_tenant(lock, fn())

        return only_the_locked_tenant
    signature = inspect.signature(fn)
    if lock.param not in signature.parameters:
        return fn
    hints = typing.get_type_hints(fn)
    parameters = [
        p.replace(annotation=hints.get(p.name, p.annotation))
        for p in signature.parameters.values()
        if p.name != lock.param
    ]
    returns = hints.get("return", signature.return_annotation)
    twin = getattr(fn, "async_fn", None)
    target = twin if inspect.iscoroutinefunction(twin) else fn
    bound_to = {lock.param: lock.value}

    async def bound(**kwargs: Any) -> Any:
        if inspect.iscoroutinefunction(target):
            return await target(**bound_to, **kwargs)
        return await asyncio.to_thread(target, **bound_to, **kwargs)  # as core's as_async

    bound.__name__ = fn.__name__
    bound.__qualname__ = fn.__qualname__
    bound.__module__ = fn.__module__
    bound.__doc__ = _without_arg(inspect.getdoc(fn) or "", lock.param)
    bound.__signature__ = signature.replace(  # type: ignore[attr-defined]
        parameters=parameters, return_annotation=returns
    )
    bound.__annotations__ = {p.name: p.annotation for p in parameters} | {"return": returns}
    return bound


def bind_tasks(
    tasks: Iterable[Callable[..., Any]], lock: AccountLock | None
) -> list[Callable[..., Any]]:
    """``tasks`` as a server's tools: bound to ``lock`` (``bind``), or as they are without one."""
    if lock is None:
        return list(tasks)
    return [bind(fn, lock) for fn in tasks]
