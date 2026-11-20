from xippios.core.testing import keep_reserved_tenants

from xippios.google.mail._client import TENANTS

# Core's plugin keeps the default run offline and free of real keys (sdk-core ADR-0035),
# runs anyio tests on asyncio and serves the family check in tests/test_family.py; a key's
# variable is named under xippios_placeholder_key_envs in pyproject.toml, not here.
pytest_plugins = ["xippios.core.testing.plugin"]

# Once, when pytest imports this file, before any fixture of any scope: core removes the
# tenant list and every tenant's variables but the reserved demo's, which the live tests
# read, so no test sees another tenant (conventions §7; sdk-core ADR-0042, decision 1).
keep_reserved_tenants(TENANTS)
