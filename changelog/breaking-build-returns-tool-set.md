- `xippios.google.mail.mcp.build()` and `xippios.google.mail.mcp_write.build()` return
  core's `ToolSet`, the tools a host serves as the entry point does, and take core's
  `TenantLock`, where they returned the server and took the package's own `AccountLock`.
  A host that built a server from `build()` passes the tool set to
  `xippios.core.mcp.build_server`, binds it with `build(TenantLock(TENANTS, "<account>"))`,
  `TENANTS` of `xippios.google.mail._client`, and reads the variables the package needs
  from the modules' new `ENV_VARS`, while `mcp.server` and `mcp_write.server` are the
  servers as before. (https://github.com/xippios/sdk-core/issues/245,
  https://github.com/xippios/sdk-core/issues/166)
