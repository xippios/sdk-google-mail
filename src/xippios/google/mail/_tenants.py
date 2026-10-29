# Queue row 281: a copy of sdk-cashctrl's kit, which returns to xippios-core in 0.4.0 with
# its two consumers (ADR-0023, decision 4; ADR-0025, D-d); this package adopts it at 0.2.0.
"""The account registry's machinery: accounts declared in the environment, one client each.

Copied from sdk-cashctrl, which carries it since xippios-core 0.3.0 left it to that package
(ADR-0024): ``src/xippios/cashctrl/_tenants.py`` at sdk-cashctrl's
``6db155b9f80e20b17b1533db66134b9eb2144c46`` (``main``, release 0.3.0), whose copy came
from sdk-core's ``cb8ecdf379c04522e5f1dcd91ea5e8bdfa1fb3ff`` and closes the client a
changed variable replaces (queue row 24 b). sdk-google-mail is its second consumer
(ADR-0023, decision 4; ADR-0025, D-d). Private to this package: ``_client.py`` declares
``TENANTS`` with it and ``tasks.py`` builds ``list_accounts`` with ``list_task``; nothing
outside the package imports it. One change since the copy: ``list_task`` takes
``source=``, the slug its result names, since this registry's first argument is the
variable prefix every Google source shares, ``google``, and not the source,
``google-mail`` (ADR-0025, D-b).

Gmail serves every mailbox under a grant of its own: an account's refresh token, issued
to the operator's OAuth client when the account consented (``xippios-google-mail-auth``),
reaches that mailbox and no other. The accounts are declared in the environment, the
contract of conventions §7, and every task takes the account's name::

    TENANTS = Tenants(
        "google",
        list_var="GOOGLE_ACCOUNTS",  # GOOGLE_ACCOUNTS=work,private
        fields=("REFRESH_TOKEN",),  # GOOGLE_WORK_REFRESH_TOKEN
        reserved=("demo",),  # declared by GOOGLE_DEMO_REFRESH_TOKEN alone
    )


    def build(name: str) -> AsyncClient:
        auth = RefreshTokenAuth(TENANTS.var(name, "REFRESH_TOKEN"), account=name)
        return AsyncClient(config, auth=auth)


    client = TENANTS.client_for("work", build)

Isolation is by configuration: a deployment reaches exactly the accounts its environment
declares. Nothing here spans accounts or returns a value; messages name accounts and
variables, never what a variable holds (ADR-0005). The messages say "tenant", as the
copy does, and their ``source`` is the registry's, ``google``.
"""

import asyncio
import hashlib
import os
import re
import threading
from collections.abc import Callable, Mapping, Sequence

from xippios.core import AsyncClient, ConfigError, InvalidRequestError, Result, ResultMeta, sync

__all__ = ["Tenants", "list_task"]

_NAME = re.compile(r"[a-z0-9_]+")
_LISTS_TENANTS = "__xippios_lists_tenants__"
"""Marks a ``list_task``: the ``--account`` lock narrows its result to the bound tenant."""
_FIELD = re.compile(r"[A-Z0-9_]+")


def _is_set(variable: str) -> bool:
    return bool(os.environ.get(variable, "").strip())


class Tenants:
    """The tenants a multi-tenant source declares in the environment, and their clients.

    One variable, ``list_var``, lists the tenant names, comma-separated, each matching
    ``^[a-z0-9_]+$`` (``GOOGLE_ACCOUNTS=work,private``). Every tenant has one variable
    per field, ``<SOURCE>_<NAME>_<FIELD>`` with the source and the name upper-cased and
    ``-`` made ``_`` (``GOOGLE_WORK_REFRESH_TOKEN``). A reserved tenant, such as the demo
    tenant fixtures and live tests use, is never listed: it exists when every one of its
    field variables is set, and not otherwise. The environment is read on every call;
    only the clients are cached.

    Args:
        source: The variable prefix, e.g. ``"google"``, which every Google source
            shares; prefixes every variable and names the source in errors.
        list_var: The variable listing the tenant names, e.g. ``"GOOGLE_ACCOUNTS"``.
        fields: The per-tenant variables' suffixes, upper-case as they appear in the
            variable name, e.g. ``("REFRESH_TOKEN",)``.
        reserved: Tenant names declared by their own field variables alone and never
            listed, e.g. ``("demo",)``; they follow the listed names, in this order.
        validate: Per field, a check applied to the value ``field`` returns, e.g.
            ``{"SUBDOMAIN": check_subdomain}``. It returns the value to use, possibly
            normalised, and raises ``ValueError`` for a value it refuses; any exception
            it raises refuses the value the same way.
        hints: Per field, static text appended to the ``ConfigError`` when ``validate``
            refuses a value, e.g. ``{"SUBDOMAIN": "set the subdomain alone, e.g. acme
            for acme.example.org"}``, read as "...; <hint>". It tells the operator what
            the variable should hold; the check's own message, which may quote the
            value, is never used. A field with a hint has a ``validate`` check.

    Attributes:
        source: The source slug.
        list_var: The variable listing the tenant names.
        fields: The field suffixes, as a tuple.
        reserved: The reserved tenant names, as a tuple.

    Raises:
        TypeError: ``fields`` or ``reserved`` is one string rather than a sequence.
        ValueError: An empty ``source`` or ``list_var``; no field, a field that is not
            upper-case letters, digits and underscores, or a field given twice; a
            reserved name that is not a valid tenant name or is given twice; a
            ``validate`` or ``hints`` entry for a field not in ``fields``; a ``hints``
            entry for a field without a ``validate`` check, which could never be shown.
    """

    def __init__(
        self,
        source: str,
        *,
        list_var: str,
        fields: Sequence[str],
        reserved: Sequence[str] = (),
        validate: Mapping[str, Callable[[str], str]] | None = None,
        hints: Mapping[str, str] | None = None,
    ) -> None:
        if not source:
            raise ValueError("source must not be empty")
        if not list_var:
            raise ValueError("list_var must not be empty")
        for label, values in (("fields", fields), ("reserved", reserved)):
            if isinstance(values, str):
                raise TypeError(f"{label} is a sequence of names, e.g. [name], not one string")
        fields = tuple(fields)
        reserved = tuple(reserved)
        if not fields:
            raise ValueError("fields must name at least one per-tenant variable")
        for field in fields:
            if not _FIELD.fullmatch(field):
                raise ValueError(
                    f"field {field!r} is not upper-case letters, digits and underscores"
                )
        for label, values in (("field", fields), ("reserved name", reserved)):
            duplicates = sorted({value for value in values if values.count(value) > 1})
            if duplicates:
                raise ValueError(f"{label} given twice: {', '.join(duplicates)}")
        for name in reserved:
            if not _NAME.fullmatch(name):
                raise ValueError(
                    f"reserved name {name!r} is not lower-case letters, digits and underscores"
                )
        checks = dict(validate or {})
        unknown = sorted(set(checks) - set(fields))
        if unknown:
            raise ValueError(f"validate names fields not in fields: {', '.join(unknown)}")
        texts = dict(hints or {})
        unknown = sorted(set(texts) - set(fields))
        if unknown:
            raise ValueError(f"hints names fields not in fields: {', '.join(unknown)}")
        unchecked = sorted(set(texts) - set(checks))
        if unchecked:
            # Shown only when the check refuses a value: without a check it never would be.
            raise ValueError(f"hints names fields without a validate check: {', '.join(unchecked)}")
        self.source = source
        self.list_var = list_var
        self.fields = fields
        self.reserved = reserved
        self._prefix = source.upper().replace("-", "_")
        self._validate = checks
        self._hints = texts
        self._clients: dict[str, tuple[str, AsyncClient]] = {}
        self._closing: set[asyncio.Task[None]] = set()
        self._lock = threading.Lock()

    def __repr__(self) -> str:
        return (
            f"Tenants({self.source!r}, list_var={self.list_var!r}, fields={self.fields!r}, "
            f"reserved={self.reserved!r})"
        )

    def var(self, name: str, field: str) -> str:
        """The variable holding ``field`` for tenant ``name``, e.g. ``GOOGLE_WORK_REFRESH_TOKEN``.

        A name only: nothing is read from the environment, and ``name`` need not be
        declared.

        Args:
            name: A tenant name, e.g. ``"work"``.
            field: One of ``fields``, e.g. ``"REFRESH_TOKEN"``.

        Raises:
            ValueError: ``field`` is not one of ``fields``.
        """
        self._known_field(field)
        return f"{self._prefix}_{name.upper().replace('-', '_')}_{field}"

    def names(self) -> tuple[str, ...]:
        """The declared tenants: ``list_var`` in its order, then each reserved one present.

        A reserved name is present when every one of its field variables is set to
        something other than whitespace. Read from the environment on every call.

        Raises:
            ConfigError: ``list_var`` lists a reserved name, a name twice, or an entry that
                is not lower-case letters, digits and underscores; that entry is named by
                its position, never quoted, since it may be a value pasted by mistake.
        """
        listed = [part.strip() for part in os.environ.get(self.list_var, "").split(",")]
        names: list[str] = []
        for position, name in enumerate(listed, start=1):
            if not name:
                continue
            if name in self.reserved:
                variables = " and ".join(self.var(name, field) for field in self.fields)
                raise ConfigError(
                    f"{self.list_var} lists {name!r}, which is reserved: it is declared by "
                    f"{variables} alone",
                    source=self.source,
                )
            if not _NAME.fullmatch(name):
                # By position, never quoted: a pasted subdomain or key would be a value.
                raise ConfigError(
                    f"entry {position} of {self.list_var} is not a tenant name; names use "
                    "only lower-case letters, digits and underscores",
                    source=self.source,
                )
            if name in names:
                raise ConfigError(f"{self.list_var} lists {name!r} twice", source=self.source)
            names.append(name)
        for name in self.reserved:
            if all(_is_set(self.var(name, field)) for field in self.fields):
                names.append(name)
        return tuple(names)

    def require(self, name: str) -> str:
        """Return ``name`` when it is a declared tenant, a reserved one that is present included.

        Raises:
            InvalidRequestError: ``name`` is not declared, the caller's bad argument
                (ADR-0007); the message lists the declared tenants or, when there are
                none, the variables to set, and for an absent reserved tenant the
                variables that declare it; never a value.
            ConfigError: ``list_var`` is malformed (see ``names``).
        """
        declared = self.names()
        if name in declared:
            return name
        if declared:
            hint = "declared tenants: " + ", ".join(declared)
            if name in self.reserved:
                variables = " and ".join(self.var(name, field) for field in self.fields)
                hint += f"; {name!r} is declared by {variables} alone"
        else:
            per_name = " and ".join(f"{self._prefix}_<NAME>_{field}" for field in self.fields)
            hint = f"no tenant is declared; set {self.list_var} and, per name, {per_name}"
            for reserved in self.reserved:
                variables = " and ".join(self.var(reserved, field) for field in self.fields)
                hint += f"; {reserved!r} is declared by {variables} alone"
        raise InvalidRequestError(f"unknown tenant {name!r}; {hint}", source=self.source)

    def field(self, name: str, field: str) -> str:
        """The value of tenant ``name``'s ``field`` variable, stripped and checked.

        Args:
            name: A declared tenant, e.g. ``"work"``.
            field: One of ``fields``, e.g. ``"REFRESH_TOKEN"``.

        Returns:
            The value without surrounding whitespace, passed through ``validate[field]``
            when the source gave one.

        Raises:
            InvalidRequestError: ``name`` is not declared (see ``require``).
            ConfigError: The variable of a declared tenant is unset or blank, or the
                check refused its value: the operator's environment. The message names
                the variable, never the value; for a refused value it ends with the
                field's hint, when the source gave one.
            ValueError: ``field`` is not one of ``fields``.
        """
        self._known_field(field)
        self.require(name)
        variable = self.var(name, field)
        if not _is_set(variable):
            raise ConfigError(
                f"{variable} is not set; export the {field} of tenant {name!r}",
                source=self.source,
            )
        check = self._validate.get(field)
        if check is None:
            return os.environ[variable].strip()
        try:
            return check(os.environ[variable].strip())
        except Exception:  # any failure refuses the value; none passes it on
            pass
        # Raised outside the handler: the check's error may quote the value, so it is
        # neither the cause nor the context of this one.
        message = f"{variable} does not hold a valid {field} for tenant {name!r}"
        hint = self._hints.get(field)
        if hint:
            message += f"; {hint}"
        raise ConfigError(message, source=self.source)

    def client_for(self, name: str, build: Callable[[str], AsyncClient]) -> AsyncClient:
        """Tenant ``name``'s client: ``build(name)`` on first use, the same client after.

        The client is cached under the current values of the tenant's field variables
        (as a digest, never the values), so a changed value builds a new client on the
        next call, and the one it replaces is closed through its ``aclose()``, which
        closes the connection pool it holds on the event loop that awaits it (queue row
        24 b). Called inside a running event loop, as every endpoint wrapper calls it,
        the close is scheduled as a task on that loop and runs once the caller next
        yields; called outside any loop, it runs on core's background loop through
        ``xippios.core.sync``, the loop every sync facade uses, before this returns. A
        pool the replaced client holds on yet another loop is not reached and goes with
        the client. A request still in flight on the replaced client on the same loop is
        cut off by the close, where before it finished; only tests and notebooks that
        change the environment inside a running process meet it. Every tenant has a
        client of its own, and its own rate limiter and cache when ``build`` creates them,
        as it should. ``build`` runs under the registry's lock: it must not call
        ``client_for``. Thread-safe.

        Args:
            name: A declared tenant, e.g. ``"work"``.
            build: Builds the tenant's client from its name, usually reading
                ``field(name, ...)`` for the host and passing an auth on
                ``var(name, ...)`` that reads the key on the first request.

        Raises:
            InvalidRequestError: ``name`` is not declared; ``build`` is not called.
            ConfigError: ``build`` raised it, e.g. from ``field`` for a missing or
                invalid value.
        """
        self.require(name)
        with self._lock:
            digest = self._digest(name)
            cached = self._clients.get(name)
            if cached is not None and cached[0] == digest:
                return cached[1]
            client = build(name)
            self._clients[name] = (digest, client)
        if cached is not None:
            self._close(cached[1])
        return client

    def _close(self, replaced: AsyncClient) -> None:
        """Close ``replaced`` on the running loop, or on core's background loop without one."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            sync(replaced.aclose)()
            return
        task = loop.create_task(replaced.aclose())
        self._closing.add(task)  # held until done: the loop keeps a weak reference only
        task.add_done_callback(self._closing.discard)

    def reset(self) -> None:
        """Drop every cached client, without closing it; the next ``client_for`` builds anew.

        ``tests/conftest.py`` calls it around each test; a dropped client's pools go with
        it, unlike the client ``client_for`` replaces.
        """
        with self._lock:
            self._clients.clear()

    def _known_field(self, field: str) -> None:
        if field not in self.fields:
            raise ValueError(f"unknown field {field!r}; fields: {', '.join(self.fields)}")

    def _digest(self, name: str) -> str:
        """A digest of tenant ``name``'s field values, unset ones included; no value in a local."""
        digest = hashlib.sha256()
        for field in self.fields:
            # repr tells an unset variable (None) from an empty one and escapes NUL.
            digest.update(repr(os.environ.get(self.var(name, field))).encode("utf-8") + b"\x00")
        return digest.hexdigest()


_LIST_DOC = """List the tenants this server can reach, by name.

Every other tool takes one of these names to choose the tenant it works on. Only names
come back, never a credential, a host or another value. An empty list means that no
tenant is declared where the server runs.
"""


def list_task(
    tenants: Tenants, *, name: str, doc: str | None = None, source: str | None = None
) -> Callable[[], Result[list[str]]]:
    """A task returning the declared tenants' names, for a ``<source>_list_<tenants>`` tool.

    The task is synchronous and takes no argument; it reads ``tenants.names()`` on every
    call and returns names only (ADR-0005), with ``meta.endpoint`` set to ``name`` and no
    ``meta.tenant``. Register it with the source's other tasks. On an MCP server bound to
    one account (``--account``, ``_account_lock``) its tool lists that account only.

    Args:
        tenants: The source's registry.
        name: The task's ``__name__``, e.g. ``"list_accounts"``; the MCP tool is named
            from it (``google_mail_list_accounts``).
        doc: The docstring, which is the tool description; ``None`` gives a default
            written for agents that says "tenants".
        source: The source slug ``meta.source`` names, e.g. ``"google-mail"``; ``None``
            names the registry's ``tenants.source``, as the copy did (the one change
            since the copy).

    Raises:
        ValueError: ``name`` is not a Python identifier.
    """
    if not name.isidentifier():
        raise ValueError(f"name {name!r} is not a Python identifier")
    slug = tenants.source if source is None else source

    def task() -> Result[list[str]]:
        return Result[list[str]](
            data=list(tenants.names()), meta=ResultMeta(source=slug, endpoint=name)
        )

    task.__name__ = name
    task.__qualname__ = name
    task.__doc__ = _LIST_DOC if doc is None else doc
    setattr(task, _LISTS_TENANTS, True)
    return task
