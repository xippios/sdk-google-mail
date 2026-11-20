- The account registry, the `--account` lock and its serve command, the renewing bearer,
  the write requests and the fixture writer are xippios-core 0.4.0's, where the package
  carried copies of its own. The private modules `_tenants`, `_account_lock` and `_cli` go
  with them, and core holds the read server to loopback, as it holds the write server.
  (https://github.com/xippios/sdk-google-mail/issues/19)
