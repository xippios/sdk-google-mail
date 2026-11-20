- A tool of either server refuses an argument it does not declare, before it runs, and
  names the ones it takes, where the argument was dropped and the tool ran with its
  defaults. So a server that `--account` binds refuses a call that still names `account`,
  where it answered it for the bound account, and a client drops the argument.
  (https://github.com/xippios/sdk-core/issues/84)
